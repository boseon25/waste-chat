"""운반 업체 (transporter) 매칭 — partners_transport (폐기물 수집·운반업).

조건 (앞에서부터, 처음 걸린 이유로 업체 수를 센다)
1. 영업 중 (영업중 = Y)                                    → "영업 중단"
2. 건설폐기물만 받는 업체 제외 (대상군 = 건설)               → "건설폐기물만"
3. 대상 폐기물 (처분 업체와 같은 규칙: by_treatment.target_ok)
   - 의료(10-) → 대상군 의료
   - 지정     → 대상군 지정 + 받을 수 있는 분류번호 앞자리(* = 전체)
   - 사업장일반 → 대상군 생활·사업장일반 + 영업대상 구분에 "사업장"
                  (생활·음식물류만 받는 업체는 여기서 빠짐)        → "대상 폐기물 다름"
혼합물(여러 분류번호를 같이 싣는 경우)은 모든 분류번호를 받아야 한다 → "혼합물 일부만 대상"

거르지 않는 것
- 영업 구역: 원자료에 없음 → 거리순 정렬로 대신
- 배출시설계 / 비배출시설계: 배출자 쪽 정보가 파트 1 입력에 없음 → 업체 정보로 보여주고 질문

관문 1(순환자원) 경로의 운반은 폐기물 운반이 아니라서 여기로 오지 않는다 (matcher에서 처리).
"""
from __future__ import annotations

from collections import Counter

from gates.models import RouteDraft, Step

from .by_recycler import is_designated_code
from .by_treatment import target_ok
from .data import PartnerData
from .models import Location, PartnerCandidate

STAGES = [
    ("영업 중단",        lambda r, code: r["영업중"] == "Y"),
    ("건설폐기물만",     lambda r, code: r["대상군"] != "건설"),
    ("대상 폐기물 다름", lambda r, code: target_ok(r, code)),
]


def judge(row: dict, codes: list[str]) -> str | None:
    for reason, ok in STAGES:
        results = [ok(row, c) for c in codes]
        if not any(results):
            return reason
        if not all(results):
            return "혼합물 일부만 대상"
    return None


def _emitter_scope(scope: str) -> str:
    """영업대상 구분 → 사업장 배출시설계 / 비배출시설계 표시"""
    has_on = "사업장배출시설계" in scope
    has_off = "사업장비(非)배출시설계" in scope
    if has_on and has_off:
        return "배출시설계·비배출시설계"
    if has_on:
        return "배출시설계"
    if has_off:
        return "비배출시설계"
    return ""


def match(route: RouteDraft, step: Step, data: PartnerData, origin: Location
          ) -> tuple[list[PartnerCandidate], Counter, list[dict]]:
    """반환: (조건을 맞춘 업체 전체, 뺀 이유별 업체 수, 질문)"""
    codes = [c for wc in (step.waste_codes or route.waste_codes) for c in wc.split("+")]
    suspected = route.waste_status != "general" and not any(is_designated_code(c) for c in codes)
    general = not any(is_designated_code(c) for c in codes)

    found: list[PartnerCandidate] = []
    seen: set[str] = set()
    first_reason: dict[str, str] = {}
    for r in data.transport:
        key = r["업체_키"]
        if key in seen:
            continue
        reason = judge(r, codes)
        if reason:
            first_reason.setdefault(key, reason)
            continue
        seen.add(key)
        emitter = _emitter_scope(r["영업대상_구분"]) if general else ""
        capacity = {k: r[k] for k in ("보유차량", "2023_운반량_톤") if r.get(k)}
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
            accepts_designated=[x for x in r["지정_분류번호_앞자리"].split(";") if x],
            scope_text="; ".join(x for x in (r["영업대상_원문"], emitter) if x) or None,
            capacity=capacity or None,
            reasons=[f'수집·운반업 허가 ({r["대상군"]})' + (f", 사업장 {emitter}" if emitter else "")],
        )
        c.location = data.locator.company(key, c.sido, c.sigungu, c.address, origin)
        found.append(c)

    rejected = Counter(v for k, v in first_reason.items() if k not in seen)

    questions = []
    if suspected:
        questions.append({"field": "designated_analysis", "to": "part1",
                          "text": "지정폐기물 의심: 성분 분석으로 일반폐기물임이 확인되어야 이 업체들에 위탁할 수 있습니다. 분석 결과가 있나요?"})
    if general and found:
        questions.append({"field": "emitter_facility_type", "to": "part1",
                          "text": "배출 사업장이 폐기물 배출시설을 설치한 사업장(배출시설계)인가요, 아닌가요(비배출시설계)? 운반 업체마다 받는 범위가 다릅니다."})
    return found, rejected, questions
