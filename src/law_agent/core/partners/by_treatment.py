# route_agent/src/partners/by_treatment.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""처분 업체 (treatment) 매칭 — partners_disposal (소각·매립·중간처분).

조건 (앞에서부터, 처음 걸린 이유로 업체 수를 센다)
1. 영업 중 (영업중 = Y)                                        → "영업 중단"
2. 건설폐기물만 받는 업체 제외 (대상군 = 건설)                   → "건설폐기물만"
3. 대상 폐기물
   - 의료폐기물(10-) → 대상군 의료
   - 지정폐기물     → 대상군 지정 + 받을 수 있는 분류번호 앞자리(* = 전체)
   - 사업장일반     → 대상군에 사업장일반 + 영업대상 구분에 사업장(배출시설계/비배출시설계)
                                                                → "대상 폐기물 다름"
4. 처리방법 코드 (경로 단계의 2101 소각, 2201 매립 등)           → "처리 방법 다름"

지정 여부는 분류번호마다 본다. 일반 분류번호인데 경로가 지정 기준(지정 의심)이면
일반 처분 업체를 찾고 "분석 후 위탁" 질문을 붙인다 (재활용 업체와 같은 규칙).
"""
from __future__ import annotations

from collections import Counter

from law_agent.core.routes import RouteDraft, Step

from .by_recycler import is_designated_code
from .data import PartnerData
from .models import Location, PartnerCandidate


def _split(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(";") if x.strip()]


def is_medical_code(code: str) -> bool:
    return code.split("-")[0] == "10"


def target_ok(row: dict, code: str) -> bool:
    """이 업체가 이 분류번호를 대상으로 영업하는지."""
    group = row["대상군"]
    if is_medical_code(code):
        return group == "의료"
    if is_designated_code(code):
        if group != "지정":
            return False
        prefixes = _split(row["지정_분류번호_앞자리"])
        return "*" in prefixes or any(code.startswith(p) for p in prefixes)
    if "사업장일반" not in group:
        return False
    scope = row["영업대상_구분"]
    return not scope or "사업장" in scope           # 구분이 비어 있으면 대상군만으로 판단


STAGES = [
    ("영업 중단",        lambda r, code, tc: r["영업중"] == "Y"),
    ("건설폐기물만",     lambda r, code, tc: r["대상군"] != "건설"),
    ("대상 폐기물 다름", lambda r, code, tc: target_ok(r, code)),
    ("처리 방법 다름",   lambda r, code, tc: bool(set(_split(r["처리방법코드"])) & tc)),
]


def judge(row: dict, codes: list[str], want_tc: set[str]) -> str | None:
    """업체 한 곳 판정. 반환: 뺀 이유 또는 None. 혼합물은 모든 분류번호를 받아야 한다."""
    for reason, ok in STAGES:
        results = [ok(row, c, want_tc) for c in codes]
        if not any(results):
            return reason
        if not all(results):
            return "혼합물 일부만 대상"
    return None


def match(route: RouteDraft, step: Step, data: PartnerData, origin: Location
          ) -> tuple[list[PartnerCandidate], Counter, list[dict]]:
    """반환: (조건을 맞춘 업체 전체, 뺀 이유별 업체 수, 질문)"""
    codes = [c for wc in step.waste_codes for c in wc.split("+")] or route.waste_codes
    want_tc = set(step.treatment_codes)
    suspected = route.waste_status != "general" and not any(is_designated_code(c) for c in codes)

    rejected: Counter = Counter()
    found: list[PartnerCandidate] = []
    seen: set[str] = set()
    for r in data.disposal:
        key = r["업체_키"]
        if key in seen:                     # 같은 업체가 여러 행(업종별)이면 처음 맞는 행만
            continue
        reason = judge(r, codes, want_tc)
        if reason:
            continue
        seen.add(key)
        capacity = {k: r[k] for k in ("시설용량_톤일", "잔여매립가능량_m3", "2023_처리량") if r.get(k)}
        c = PartnerCandidate(
            company_id=key,
            name=r["업체명"],
            role=step.role,
            sido=r["시도"],
            sigungu=r["시군구"],
            address=r["주소"],
            phone=r["전화번호"] or None,
            data_date=r["기준연도"],
            source=r["출처"],
            permit_type="허가",
            registration_type=r["업종"],
            waste_group=r["대상군"],
            treatment_codes=sorted(set(_split(r["처리방법코드"])) & want_tc),
            accepts_designated=_split(r["지정_분류번호_앞자리"]),
            scope_text=r["영업대상_원문"] or None,
            capacity=capacity or None,
            reasons=[f'{r["업종"]} ({r["대상군"]})',
                     f'처리방법 {", ".join(sorted(set(_split(r["처리방법코드"])) & want_tc))}'
                     + (f' / {r["소각방식"]}' if r["소각방식"] else "")
                     + (f' / {r["매립지종류"]} 매립' if r["매립지종류"] else "")],
        )
        c.location = data.locator.company(key, c.sido, c.sigungu, c.address, origin)
        found.append(c)

    # 뺀 이유는 업체 단위로 센다 (조건을 맞춘 행이 하나도 없는 업체만)
    by_company: dict[str, str] = {}
    for r in data.disposal:
        key = r["업체_키"]
        if key in seen:
            continue
        by_company.setdefault(key, judge(r, codes, want_tc) or "")
    rejected.update(v for v in by_company.values() if v)

    questions = []
    if suspected:
        questions.append({"field": "designated_analysis", "to": "part1",
                          "text": "지정폐기물 의심: 성분 분석으로 일반폐기물임이 확인되어야 이 업체들에 위탁할 수 있습니다. 분석 결과가 있나요?"})
    if found:
        questions.append({"field": "acceptance", "to": "partner",
                          "text": "반입 기준(성상·수분·크기)과 월 반입 가능 물량, 처리 단가를 알려주세요."})
    return found, rejected, questions
