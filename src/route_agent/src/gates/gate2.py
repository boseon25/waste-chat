"""관문 2 — 재활용 위탁.

폐기물 분류번호에 별표 4의3이 허용하는 재활용 유형(R코드)으로 경로를 만든다.
법 우선순위(tier)마다 경로 1개: 재사용 → 재생이용 → 에너지 회수, 그리고 중간 가공(R-10).

판정표: recycling_types_4_3, rcode_detail_4_2, treatment_code_map, not_in_4_3
입력:   WasteCase(파트 1 case), PreliminaryResult(사전검증)
출력:   list[RouteDraft] (경로가 없으면 빈 목록)

업체는 찾지 않는다 (업체 찾기 단계 몫).
"""
from __future__ import annotations

from preliminary.models import PreliminaryResult, WasteCase

from .models import RouteDraft, Status, Step
from .tables import Tables

TIER_ORDER = ["reuse", "material_recycle", "energy_recovery", None]   # None = 중간 가공(R-10)
TIER_TITLE = {
    "reuse": "재사용",
    "material_recycle": "재생이용",
    "energy_recovery": "에너지 회수",
    None: "중간 가공 (R-10)",
}


# ----------------------------------------------------------------------
# 분류번호 하나의 허용 R코드
# ----------------------------------------------------------------------
def allowed_rcodes(code: str, tables: Tables) -> dict[str, dict]:
    """
    {r_code: {"pre_analysis": bool, "basis": 근거}}
    - "상위코드(아래로 펼침)" 행은 빼고 세부 코드만 쓴다
    - 재활용_금지 = Y 행은 뺀다
    - 사전분석확인이 "해당" 또는 빈 값이면 사전 분석 필요 (보수적으로)
    """
    out: dict[str, dict] = {}
    for row in tables.types.get(code, []):
        rc = row["r_code"]
        if not rc or row["상태"].startswith("상위코드") or row["재활용_금지"] == "Y":
            continue
        need = row["사전분석확인"] in ("해당", "")
        item = out.setdefault(rc, {"pre_analysis": False, "basis": row["근거"]})
        item["pre_analysis"] = item["pre_analysis"] or need
    return out


def _restrict(rcodes: dict[str, dict], allowed: list[str]) -> dict[str, dict]:
    """사전검증 RESTRICT 결과(allowed_rcodes)로 좁힌다. 빈 목록 = 제한 없음."""
    if not allowed:
        return rcodes
    return {rc: v for rc, v in rcodes.items()
            if any(rc == a or rc.startswith(a + "-") for a in allowed)}


def _group_by_tier(rcodes: dict[str, dict], tables: Tables) -> dict:
    groups: dict = {}
    for rc in sorted(rcodes, key=_rc_sort):
        groups.setdefault(tables.tier_of(rc), []).append(rc)
    return groups


def _rc_sort(rc: str):
    return [int(x) if x.isdigit() else x for x in rc.replace("R-", "").split("-")]


# ----------------------------------------------------------------------
# 경로 만들기
# ----------------------------------------------------------------------
def _recycler_step(order: int, tier, rcs: list[str], codes: list[str], tables: Tables) -> Step:
    tcodes = sorted({tc for rc in rcs if (tc := tables.treatment_code_of(rc))})
    if tier is None:
        process, role = "중간가공폐기물 제조", "preprocessor"
    else:
        process = " / ".join(tables.tcode_name(tc) for tc in tcodes) or TIER_TITLE[tier]
        role = "recycler"
    return Step(order, process, role, tcodes, rcs, codes)


def _new_route(case: WasteCase, pre: PreliminaryResult, tier, route_type: str,
               codes: list[str], rcs_by_code: dict[str, list[str]], info: dict[str, dict],
               tables: Tables) -> RouteDraft:
    steps = [Step(1, "수집·운반", "transporter", waste_codes=codes)]
    if route_type == "separate_then_recycle":
        steps.append(Step(2, "재질별 선별·분리", "preprocessor", waste_codes=codes))
    for code, rcs in rcs_by_code.items():
        steps.append(_recycler_step(len(steps) + 1, tier, rcs, [code], tables))

    all_rcs = sorted({rc for rcs in rcs_by_code.values() for rc in rcs}, key=_rc_sort)
    route = RouteDraft(
        gate=2,
        tier=tier,
        route_type=route_type,
        title=f"{TIER_TITLE[tier]} ({', '.join(all_rcs)})",
        waste_codes=codes,
        waste_status=pre.waste_status or "general",
        steps=steps,
        regulatory=Status.CONFIRMED,
        reasons=[f"별표 4의3에서 {', '.join(codes)}에 {', '.join(all_rcs)} 허용"],
        r_code_detail=[{"r_code": rc, "tier": tier, "pre_analysis": info[rc]["pre_analysis"],
                        "description": tables.rcode_desc(rc), "basis": info[rc]["basis"]} for rc in all_rcs],
        evidence_ids=sorted({info[rc]["basis"] for rc in all_rcs}),
    )
    _apply_checks(route, case, pre, info, all_rcs)
    return route


def _apply_checks(route: RouteDraft, case: WasteCase, pre: PreliminaryResult,
                  info: dict[str, dict], rcs: list[str]) -> None:
    # 사전 분석: 경로 안 R코드가 모두 사전 분석 대상이면 conditional, 일부면 조건만
    need = [rc for rc in rcs if info[rc]["pre_analysis"]]
    if need:
        route.required_tests.append({
            "test": "재활용 전 사전 분석",
            "r_codes": need,
            "basis": "폐기물관리법 시행규칙 별표 4의3 (사전분석확인)",
        })
        if len(need) == len(rcs):
            route.cap(Status.CONDITIONAL, "모든 재활용 유형이 사전 분석 대상")
        else:
            route.reasons.append(f"{', '.join(need)}은 사전 분석 필요, 나머지 유형은 바로 가능")

    # 재사용: R-1은 원형 그대로, R-2는 수리·세척 후 → 파쇄·분말이면 둘 다 어려움
    if route.tier == "reuse":
        form = case.material_form
        if form is None:
            route.ask("material_form", "폐기물이 원래 형태를 유지하고 있나요? (원형보존 / 파쇄 / 분말 / 액상)")
            route.cap(Status.CONDITIONAL, "형태 미확인")
        else:
            ok = [rc for rc in rcs
                  if (rc.startswith("R-1") and form == "원형보존")
                  or (rc.startswith("R-2") and form not in ("파쇄", "분말"))]
            if not ok:
                route.cap(Status.CONDITIONAL, f"형태가 '{form}'이라 재사용 유형(R-1 원형 그대로 / R-2 수리·세척 후)에 맞지 않을 수 있음")
            elif len(ok) < len(rcs):
                route.reasons.append(f"형태 '{form}' 기준으로 {', '.join(ok)}만 해당")
    if route.tier == "energy_recovery" and case.combustible is None:
        route.ask("combustible", "타는 물질(가연성)인가요?")
        route.cap(Status.NEEDS_INFO, "가연성 미확인")
    if route.tier is None:
        route.conditions.append({
            "text": "중간가공폐기물을 받을 최종 재활용처를 계약 시 확인",
            "basis": "폐기물관리법 시행규칙 별표 4의2 R-10",
            "basis_status": "확인됨",
        })

    # 지정폐기물
    if route.waste_status == "designated":
        route.conditions.append({
            "text": "지정폐기물 재활용 허가 업체에만 위탁",
            "basis": "폐기물관리법 제25조",
            "basis_status": "확인됨",
        })
    if pre.hazardous_basis == "의심":
        route.cap(Status.CONDITIONAL, "지정폐기물 의심: 성분 분석 결과에 따라 다시 판정")
        route.ask("analysis", "성분 분석으로 지정폐기물 해당 여부를 확인했나요?")

    # 사전검증에서 기록만 한 규칙 (정보 필요·검토)
    for q in pre.restriction.questions:
        route.ask("restriction", q)
    if pre.restriction.questions:
        route.cap(Status.NEEDS_INFO, "재활용 제한 규칙에 필요한 입력 부족")
    if pre.restriction.review_required:
        route.cap(Status.NEEDS_INFO, "재활용 제한 규칙 검토 필요")
    if pre.restriction.allowed_rcodes:
        route.reasons.append(f"재활용 제한 규칙으로 {', '.join(pre.restriction.allowed_rcodes)}만 허용")

    # 추정 값
    if any((c.status or "").lower() == "estimated" for c in case.components):
        route.cap(Status.CONDITIONAL, "구성 정보가 추정 값")


def _route_types(case: WasteCase, pre: PreliminaryResult) -> list[str]:
    """혼합 여부와 분리 가능 여부로 경로 종류를 정한다."""
    if not pre.mixture.is_mixed:
        return ["single_material_recycle"]
    if case.separable is True:
        return ["separate_then_recycle"]
    if case.separable is False:
        return ["mixed_recycle"]
    return ["separate_then_recycle", "mixed_recycle"]      # 모름 → 둘 다 needs_info


# ----------------------------------------------------------------------
# 진입점
# ----------------------------------------------------------------------
def run(case: WasteCase, pre: PreliminaryResult, tables: Tables) -> list[RouteDraft]:
    main = pre.classification.normalized_waste_code
    comp_codes = [c["waste_code"] for c in pre.component_results] if pre.component_results else []
    codes = [main] + [c for c in comp_codes if c != main] if comp_codes else [main]

    # 분류번호별 허용 R코드
    per_code: dict[str, dict[str, dict]] = {}
    for code in codes:
        if code in tables.not_in_4_3 and not tables.types.get(code):
            per_code[code] = {"R-11": {"pre_analysis": True, "basis": "not_in_4_3"}}
        else:
            per_code[code] = _restrict(allowed_rcodes(code, tables), pre.restriction.allowed_rcodes)

    info = {rc: v for d in per_code.values() for rc, v in d.items()}
    routes: list[RouteDraft] = []

    for route_type in _route_types(case, pre):
        if route_type == "separate_then_recycle" and len(codes) > 1:
            # 재질별로 나눠서 각자 허용 유형으로 (모든 성분에 그 tier가 있어야 경로)
            tiers = [{t: rcs for t, rcs in _group_by_tier(per_code[c], tables).items()} for c in codes]
            for tier in TIER_ORDER:
                if all(tier in t for t in tiers):
                    by_code = {c: t[tier] for c, t in zip(codes, tiers)}
                    routes.append(_new_route(case, pre, tier, route_type, codes, by_code, info, tables))
        elif len(codes) > 1:
            # 섞인 채 가공: 모든 성분 분류번호에 공통으로 허용된 R코드만
            common = set.intersection(*(set(per_code[c]) for c in codes))
            groups = _group_by_tier({rc: info[rc] for rc in common}, tables)
            for tier in TIER_ORDER:
                if tier in groups:
                    routes.append(_new_route(case, pre, tier, route_type, codes,
                                             {"+".join(codes): groups[tier]}, info, tables))
        else:
            groups = _group_by_tier(per_code[main], tables)
            for tier in TIER_ORDER:
                if tier not in groups:
                    continue
                rtype = "direct_use" if tier == "reuse" and route_type == "single_material_recycle" else route_type
                routes.append(_new_route(case, pre, tier, rtype, codes, {main: groups[tier]}, info, tables))

    # 가연성이 아니면 에너지 회수 경로는 만들지 않음
    if case.combustible is False:
        routes = [r for r in routes if r.tier != "energy_recovery"]

    # 분리 가능 여부를 모르면 혼합물 경로 전부 needs_info
    if pre.mixture.is_mixed and case.separable is None:
        for r in routes:
            r.ask("separable", "재질별로 나눌 수 있나요? (나눠서 가공 / 섞인 채 가공)")
            r.cap(Status.NEEDS_INFO, "분리 가능 여부 미확인")

    # R-11만 가능한 코드
    for r in routes:
        if any(rc.startswith("R-11") for rc in r.steps[-1].r_codes) and r.tier == "material_recycle":
            if all(rc.startswith("R-11") for rc in r.steps[-1].r_codes):
                r.cap(Status.CONDITIONAL, "재활용환경성평가 승인을 받은 유형으로만 재활용 가능")
    return routes
