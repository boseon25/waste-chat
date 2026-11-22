# route_agent/src/partners/by_recycler.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""재활용 업체 (recycler / preprocessor) 매칭 — partners_recycling.

조건 (앞에서부터, 처음 걸린 이유로 업체 수를 센다)
1. 다른 회사 폐기물을 위탁받는지 (위탁가능 = Y)          → "자가처리만 신고"
2. 지정/일반 구분 (지정 기준이면 지정 허가 행만)           → "지정/일반 범위 밖"
3. 처리방법 코드 (경로 단계의 2003 등)                    → "처리 방법 다름"
4. 재활용 유형 상위 R코드 (R-3-3 → R-3)                   → "재활용 유형 다름"
혼합물(섞인 채 가공)은 성분 분류번호마다 1~4를 따로 건다. 일부 성분만 통과 → "혼합물 일부만 허가"

신고 업체(허가신고 = 신고)는 공개 자료로 신고 범위(별표 16)와 하루 처리량을 알 수 없어
빼지 않고 질문을 붙인다.
"""
from __future__ import annotations

from collections import Counter

from law_agent.core.routes import RouteDraft, Step

from .data import PartnerData
from .models import Location, PartnerCandidate


def _groups_for(code: str) -> list[str]:
    """분류번호 → 업체 표에서 찾을 중분류 후보. 지정폐기물은 "06"처럼 두 자리로 적힌 업체도 있다."""
    parts = code.split("-")
    out = []
    if len(parts) >= 2:
        out.append(f"{parts[0]}-{parts[1]}")
    out.append(parts[0])
    return out


def _top(rc: str) -> str:
    p = rc.split("-")
    return "-".join(p[:2]) if len(p) >= 2 else rc


STAGES = [
    ("자가처리만 신고",   lambda r, d, tc, top: r["위탁가능"] == "Y"),
    ("지정/일반 범위 밖", lambda r, d, tc, top: (r["폐기물_구분"] == "지정") == d),
    ("처리 방법 다름",    lambda r, d, tc, top: r["처리방법코드"] in tc),
    ("재활용 유형 다름",  lambda r, d, tc, top: not top or r["r_code"] in top),
]


def is_designated_code(code: str) -> bool:
    """지정폐기물 분류번호인지. 사업장일반 51-, 생활 91-, 지정 01~4x."""
    try:
        return int(code.split("-")[0]) < 50
    except ValueError:
        return False


def judge(rows_by_code: list[list[dict]], designated: bool | list[bool], want_tc: set[str], want_top: set[str]
          ) -> tuple[str | None, list[dict]]:
    """업체 한 곳 판정. rows_by_code = 성분 분류번호마다 그 업체의 허가 행.
    반환: (뺀 이유 또는 None, 조건을 맞춘 행).
    조건은 성분마다 따로 건다. 모든 성분이 같은 단계에서 걸리면 그 단계 이유,
    일부 성분만 걸리면 "혼합물 일부만 허가"."""
    current = [list(rs) for rs in rows_by_code]
    flags = designated if isinstance(designated, list) else [designated] * len(current)
    if any(not rs for rs in current):
        return "혼합물 일부만 허가", []
    for reason, ok in STAGES:
        current = [[r for r in rs if ok(r, d, want_tc, want_top)] for rs, d in zip(current, flags)]
        empty = [not rs for rs in current]
        if all(empty):
            return reason, []
        if any(empty):
            return "혼합물 일부만 허가", []
    return None, [r for rs in current for r in rs]


def match(route: RouteDraft, step: Step, data: PartnerData, origin: Location
          ) -> tuple[list[PartnerCandidate], Counter, list[dict]]:
    """반환: (조건을 맞춘 업체 전체, 뺀 이유별 업체 수, 질문)"""
    codes = [c for wc in step.waste_codes for c in wc.split("+")] or route.waste_codes
    # 지정 여부는 성분 분류번호마다. 일반 분류번호인데 경로가 지정 기준(지정 의심)이면
    # 일반 허가 업체를 찾고 "분석 후 위탁" 질문을 붙인다 (51-03을 지정으로 허가받은 업체는 없음).
    designated = [is_designated_code(c) for c in codes]
    suspected = route.waste_status != "general" and not any(designated)
    want_tc = set(step.treatment_codes)
    want_top = {_top(rc) for rc in step.r_codes}

    # 성분 분류번호마다 해당 중분류 행을 가진 업체
    per_code: list[dict[str, list[dict]]] = []
    for code in codes:
        by_co: dict[str, list[dict]] = {}
        for g in _groups_for(code):
            for r in data.recycling_by_group.get(g, []):
                by_co.setdefault(r["업체_키"], []).append(r)
        per_code.append(by_co)

    all_companies = set().union(*(set(d) for d in per_code)) if per_code else set()
    rejected: Counter = Counter()
    found: list[PartnerCandidate] = []
    asked = False

    for key in sorted(all_companies):
        rows_by_code = [d.get(key, []) for d in per_code]
        reason, by_type = judge(rows_by_code, designated, want_tc, want_top)
        if reason:
            rejected[reason] += 1
            continue
        rows = [r for rs in rows_by_code for r in rs]

        first = by_type[0]
        permit = "허가" if any(r["허가신고"] == "허가" for r in by_type) else "신고"
        all_rows = data.recycling_by_company.get(key, rows)
        designated_groups = sorted({r["분류번호_중분류"] for r in all_rows
                                    if r["폐기물_구분"] == "지정" and r["위탁가능"] == "Y"})
        c = PartnerCandidate(
            company_id=key,
            name=first["업체명"],
            role=step.role,
            sido=first["시도"],
            sigungu=first["시군구"],
            address=first["주소"],
            phone=first["전화번호"] or None,
            data_date=first["기준연도"],
            source=first["출처"],
            permit_type=permit,
            registration_type=" / ".join(sorted({r["등록형태"] for r in by_type})),
            waste_group=", ".join(sorted({f'{r["분류번호_중분류"]} {r["폐기물종류"]}' for r in by_type})),
            treatment_codes=sorted({r["처리방법코드"] for r in by_type}),
            r_codes=sorted({r["r_code"] for r in by_type}),
            accepts_designated=designated_groups,
            reasons=[
                f'{", ".join(sorted({r["분류번호_중분류"] for r in by_type}))} 위탁 재활용 {permit}',
                f'처리방법 {", ".join(sorted({r["처리방법코드"] + " " + r["처리방법"] for r in by_type}))}',
            ],
        )
        c.location = data.locator.company(key, c.sido, c.sigungu, c.address, origin)
        found.append(c)
        asked = asked or permit == "신고"

    questions = []
    if suspected:
        questions.append({"field": "designated_analysis", "to": "part1",
                          "text": "지정폐기물 의심: 성분 분석으로 일반폐기물임이 확인되어야 이 업체들에 위탁할 수 있습니다. 분석 결과가 있나요?"})
    if asked:
        questions.append({"field": "report_scope", "to": "partner",
                          "text": "폐기물처리 신고 업체: 신고 범위(시행규칙 별표 16)와 하루 처리량 기준 안에서 받을 수 있나요?"})
    return found, rejected, questions
