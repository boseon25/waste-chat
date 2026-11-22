"""관문 4 — 소각·매립 (처분).

재활용하지 않을 때 법이 정한 처분 방법(시행규칙 별표 5)으로 경로를 만든다.
관문 1·2가 막혀도(재활용 금지 등) 항상 실행된다.

판정표: disposal_methods_5 (별표 5 제3호 일반, 제4호 지정 — 초안, 사람 검토 전)
입력:   WasteCase(파트 1 case), PreliminaryResult(사전검증)
출력:   list[RouteDraft]
        처분 방법 행 하나에서 소각 경로 / 매립 경로 / 중간처분 경로를 나눠 만든다.

표의 행이 사람 확인 전(status ≠ confirmed)이면 경로는 conditional을 넘지 않는다 (basis_status = "검토 대기").
confirmed 행이면 basis_status = "확정"이고, 지킬 조건(조건·수치 기준)이 없으면 confirmed, 있으면 conditional.
"""
from __future__ import annotations

from statistics import mean

from preliminary.models import PreliminaryResult, WasteCase

from .models import RouteDraft, Status, Step
from .tables import Tables, split

INCINERATION = {"2101", "2102", "2103", "2104", "2117", "2118"}
LANDFILL = {"2201", "2202", "2204", "2205", "2206"}
GENERIC_COMBUSTIBLE_RULE = "D3-1가"          # 재활용하지 않는 소각 가능한 사업장일반폐기물 (분류번호 없는 일반 규칙)
DAILY_INCINERATION_KG = 100


def _matching_rows(code: str, designated: bool, tables: Tables) -> list[dict]:
    prefix = "D4" if designated else "D3"
    return [r for r in tables.disposal
            if r["rule_id"].startswith(prefix) and code in split(r["적용_분류번호"])]


def _pick_state(rows: list[dict], form: str | None) -> tuple[list[dict], bool]:
    """같은 분류번호에 (액체)/(고체) 행이 따로 있으면 형태로 고른다. 반환: (행, 형태 질문 필요)"""
    liquid = [r for r in rows if "(액체" in r["대상_폐기물"]]
    solid = [r for r in rows if "(고체" in r["대상_폐기물"] or r["조건"] == "고체상태"]
    if not (liquid and solid):
        return rows, False
    if form == "액상":
        return [r for r in rows if r not in solid], False
    if form in ("원형보존", "파쇄", "분말"):
        return [r for r in rows if r not in liquid], False
    return rows, True


def _daily_avg_kg(case: WasteCase) -> float | None:
    months = (case.quantity or {}).get("monthly_ton")
    if not months:
        return None
    try:
        return round(mean(float(x) for x in months) * 1000 / 30, 1)
    except (TypeError, ValueError):
        return None


LANDFILL_ONLY_KEYS = ("센티미터", "수분함량", "매립")   # 수치 기준 중 매립 전 조건 (파쇄 크기, 수분)


def _bs(row: dict) -> str:
    return "확정" if row.get("status") == "confirmed" else "검토 대기"


def _base(code: str, row: dict, pre: PreliminaryResult, title: str, steps: list[Step],
          kind: str = "incineration") -> RouteDraft:
    bs = _bs(row)
    route = RouteDraft(
        gate=4,
        tier="disposal",
        route_type="alternative_treatment",
        title=title,
        waste_codes=[code],
        waste_status=pre.waste_status or "general",
        steps=steps,
        regulatory=Status.CONDITIONAL,
        reasons=[f"별표 5 {row['rule_id']} {row['대상_폐기물']}: {row['처리방법_원문']}"],
        evidence_ids=[row["근거_청크"] or row["근거_인용"]],
        basis_status=bs,
    )
    if bs != "확정":
        route.reasons.append("처분 방법 표가 초안(사람 검토 전)이라 conditional")
    if row["조건"]:
        route.conditions.append({"text": row["조건"], "basis": row["근거_청크"], "basis_status": bs})
    landfill_rule = any(k in row["수치_기준"] for k in LANDFILL_ONLY_KEYS)
    has_landfill_route = any(c in LANDFILL for c in split(row["처리방법_코드"]))
    if row["수치_기준"] and (kind == "landfill" or not (landfill_rule and has_landfill_route)):
        route.conditions.append({"text": f"기준: {row['수치_기준']}", "basis": row["근거_청크"], "basis_status": bs})
    if row["action"] == "NEEDS_INFO":
        route.ask(f"disposal.{row['rule_id']}", f"처분 방법을 정하려면 확인이 필요합니다: {row['조건'] or row['처리방법_원문']}")
        route.cap(Status.NEEDS_INFO, f"{row['rule_id']} 확인 필요")
    elif bs == "확정" and not route.conditions:
        route.regulatory = Status.CONFIRMED                       # 확인된 표 + 지킬 조건 없음
    return route


def _routes_from_row(code: str, row: dict, case: WasteCase, pre: PreliminaryResult, tables: Tables) -> list[RouteDraft]:
    codes = split(row["처리방법_코드"])
    incin = [c for c in codes if c in INCINERATION]
    land = [c for c in codes if c in LANDFILL]
    middle = [c for c in codes if c not in INCINERATION and c not in LANDFILL]
    name = lambda cs: " / ".join(tables.tcode_name(c) for c in cs)
    transport = Step(1, "수집·운반", "transporter", waste_codes=[code])
    out: list[RouteDraft] = []

    if incin:
        out.append(_base(code, row, pre, f"소각: {name(incin)}",
                         [transport, Step(2, name(incin), "treatment", incin, waste_codes=[code])]))
    if land:
        steps = [transport]
        if middle:
            steps.append(Step(2, f"전처리: {name(middle)}", "treatment", middle, waste_codes=[code]))
        steps.append(Step(len(steps) + 1, name(land), "treatment", land, waste_codes=[code]))
        r = _base(code, row, pre, f"매립: {name(land)}" + (f" ({name(middle)} 후)" if middle else ""), steps,
                  kind="landfill")
        if incin and "곤란" in (row["처리방법_원문"] + row["조건"]):
            r.reasons.append("소각이 곤란한 경우에만 매립")
            r.conditions.append({"text": "소각이 곤란한 경우에만 매립", "basis": row["근거_청크"], "basis_status": _bs(row)})
            if r.regulatory == Status.CONFIRMED:
                r.regulatory = Status.CONDITIONAL
        out.append(r)
    if middle and not land:
        steps = [transport, Step(2, name(middle), "treatment", middle, waste_codes=[code])]
        r = _base(code, row, pre, f"중간처분: {name(middle)}", steps, kind="middle")
        if incin:
            r.conditions.append({"text": "처리 후 남은 잔재물은 소각", "basis": row["근거_청크"], "basis_status": _bs(row)})
            if r.regulatory == Status.CONFIRMED:
                r.regulatory = Status.CONDITIONAL
        out.append(r)
    return out


def run(case: WasteCase, pre: PreliminaryResult, tables: Tables) -> list[RouteDraft]:
    designated = pre.waste_status == "designated"
    main = pre.classification.normalized_waste_code
    codes = [main] + [c["waste_code"] for c in pre.component_results if c["waste_code"] != main]
    daily = _daily_avg_kg(case)

    routes: list[RouteDraft] = []
    for code in codes:
        # 처분 방법은 분류번호 자체가 지정인지로 고른다 (지정 의심인 일반 분류번호는 일반 처분 행)
        code_designated = code in tables.designated_codes
        rows, ask_state = _pick_state(_matching_rows(code, code_designated, tables), case.material_form)

        # 분류번호에 맞는 행이 없으면: 일반 분류번호이고 타는 물질이면 일반 소각 규칙
        if not rows and not code_designated and case.combustible is not False:
            rows = [r for r in tables.disposal if r["rule_id"] == GENERIC_COMBUSTIBLE_RULE]

        if not rows:
            r = RouteDraft(
                gate=4, tier="disposal", route_type="alternative_treatment",
                title="처분 방법 확인 필요", waste_codes=[code], waste_status=pre.waste_status or "general",
                steps=[Step(1, "수집·운반", "transporter", waste_codes=[code]),
                       Step(2, "처분 (방법 미정)", "treatment", waste_codes=[code])],
                regulatory=Status.NEEDS_INFO, basis_status="검토 대기",
                reasons=[f"별표 5 처분 방법 표에서 {code}에 맞는 행을 찾지 못함"],
            )
            r.ask("disposal_method", "현재 이 폐기물을 어떻게 처분하고 있나요? (소각 / 매립 / 기타)")
            routes.append(r)
            continue

        for row in rows:
            for r in _routes_from_row(code, row, case, pre, tables):
                if ask_state:
                    r.ask("material_form", "액체인가요, 고체인가요?")
                    r.cap(Status.NEEDS_INFO, "액체·고체 구분 미확인")
                if pre.hazardous_basis == "의심" and not code_designated:
                    r.conditions.append({"text": "지정폐기물 의심: 분석 결과 지정폐기물이면 지정폐기물 처분 기준 적용",
                                         "basis": "폐기물관리법 시행규칙 별표 5 제4호", "basis_status": "검토 대기"})
                routes.append(r)

    if len(codes) > 1:                                   # 혼합물: 어느 분류번호 경로인지 제목에 표시
        for r in routes:
            r.title = f"[{r.waste_codes[0]}] {r.title}"

    # 공통 조건
    for r in routes:
        if designated:
            r.conditions.append({"text": "지정폐기물 처분업 허가 업체에만 위탁", "basis": "폐기물관리법 제25조",
                                 "basis_status": "확인됨"})
        if not designated and daily is not None:
            r.reasons.append(f"하루 평균 배출량 약 {daily}kg")
            if daily >= DAILY_INCINERATION_KG and r.title.startswith("매립") and case.combustible is not False:
                r.conditions.append({"text": "하루 평균 100kg 이상 소각 가능 폐기물: 재활용하지 않으면 소각 대상",
                                     "basis": "폐기물관리법 시행규칙 별표 5 제3호 가목", "basis_status": "검토 대기"})
        if not pre.restriction.recycling_blocked:
            r.conditions.append({"text": "재활용이 가능하면 재활용(관문 1·2)이 우선", "basis": "폐기물관리법 제13조의2",
                                 "basis_status": "확인됨"})
        current = (case.existing_declaration or {}).get("treatment_code")
        if current and any(current in s.treatment_codes for s in r.steps):
            r.reasons.append(f"현재 처리 방식({current})과 같음")
    return routes
