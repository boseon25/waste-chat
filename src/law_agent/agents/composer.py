"""⑥ 경로 구성 — 판단 결과(Verdict)를 경로(RouteDraft)로. 판단하지 않는다 (판단을 경로 모양으로 옮기기만).

경로 모양(단계·역할·처리방법 코드)은 route_agent gates/gate1·2·4 와 같다 → 업체 매칭(core/partners)이 그대로 동작.
R코드 → tier·처리방법 코드는 사실 데이터(rcode_detail, treatment_code_map)로 바꾼다 (route_agent Tables 와 같은 규칙).

  관문 2 재활용 : B-1(금지면 경로 없음) · D-1(허용 R코드, 성분별 D-1.cN) · B-2(제한이면 좁힘) · A-4/사례(혼합·분리)
                 · D-2(형태로 가능한 R코드) · E-1(에너지 회수기준) · D-3·D-4(기준·준수사항) · D-5(지정폐기물 허가)
  관문 4 처분   : F-1(처리방법 코드 → 소각·매립·중간처분) · F-2(조건)
  관문 1 순환자원(기본): C-1(고시 품목) · C-2(허용 용도 R코드·기준) · G-4(인계서 제외 조건). 지정폐기물(의심 포함)은 대상 아님
  모든 경로    : A-1(분류번호) · A-2(지정 여부) 판단 상태를 반영

경로 상태(regulatory) = 쓴 판단 중 가장 나쁜 것
  모두 확정·조건 없음 → confirmed / 조건부 → conditional / needs_info·needs_human → needs_info / 금지·미충족 확정 → rejected
판단의 조건 → conditions, 빠진 사실 → questions(파트 1), 사람 검토 → questions(파트 2 내부), 인용 ref → evidence_ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from law_agent.agents.context import effective_facts
from law_agent.core.routes import RouteDraft, Status, Step
from law_agent.state import AgentState, Verdict

BASIS_STATUS = "AI 판단 (모델 합의 + 원문 인용 검증)"
TIER_ORDER = ["reuse", "material_recycle", "energy_recovery", None]          # None = 중간 가공(R-10)
TIER_TITLE = {"reuse": "재사용", "material_recycle": "재생이용", "energy_recovery": "에너지 회수",
              None: "중간 가공 (R-10)"}
INCINERATION = {"2101", "2102", "2103", "2104", "2117", "2118"}              # route_agent gate4 와 같은 코드 묶음
LANDFILL = {"2201", "2202", "2204", "2205", "2206"}
DESIGNATED = ("designated", "designated_suspect")


@dataclass
class Composed:
    routes: list[RouteDraft] = field(default_factory=list)
    used: list[list[str]] = field(default_factory=list)       # 경로마다 쓴 판단 질문 id
    skipped: dict[str, dict] = field(default_factory=dict)     # 관문 → {"action", "reason"}


# ── 사실 데이터 (fact_tools 를 쓰되, 표가 없으면 빈 값 — 테스트·--no-partners 에서도 돌게) ─────────

class Facts:
    def tier(self, rc: str) -> str | None:
        from law_agent.tools.fact_tools import r_code_tier
        try:
            return r_code_tier(rc)
        except Exception:                                   # noqa: BLE001
            return None

    def tcode_for(self, rc: str) -> str | None:
        from law_agent.tools.fact_tools import treatment_code_for_rcode
        try:
            return treatment_code_for_rcode(rc)
        except Exception:                                   # noqa: BLE001
            return None

    def tcode_name(self, code: str) -> str:
        from law_agent.tools.fact_tools import treatment_code_name
        try:
            return treatment_code_name(code) or code
        except Exception:                                   # noqa: BLE001
            return code

    def tcodes_for_method(self, word: str) -> list[str]:
        """처분 방법 이름(원문) → 처리방법 코드. 소각·매립은 route_agent 코드 묶음, 그 밖은 코드표 이름이 같은 것.
        재활용 행(R코드가 붙은 행)은 쓰지 않는다. 코드표에 소각·매립 코드가 없으면 route_agent 묶음 그대로."""
        w = (word or "").replace(" ", "")
        if not w:
            return []
        from law_agent.tools.fact_tools import treatment_codes
        try:
            rows = [r for r in treatment_codes() if not (r.get("r_code") or "").strip()]   # 재활용(R코드) 행만 뺌
        except Exception:                                   # noqa: BLE001
            rows = []
        name = {r["code"]: f"{r.get('처리분류', '')} {r.get('세분류', '')}".replace(" ", "") for r in rows}
        if "소각" in w:
            cs = sorted(c for c in INCINERATION if c in name) or sorted(INCINERATION)   # 코드표에 없으면 route_agent 묶음 그대로
            if "고온" in w:
                cs = [c for c in cs if "고온" in name.get(c, "")] or cs
            return cs
        if "매립" in w:
            return sorted(c for c in LANDFILL if c in name) or sorted(LANDFILL)
        return sorted(c for c, n in name.items() if w in n or (n and n.endswith(w)))

    def tcode_kind(self, code: str) -> str:
        """incineration / landfill / middle — route_agent 코드 묶음 우선, 없으면 사실 데이터 처리분류."""
        if code in INCINERATION:
            return "incineration"
        if code in LANDFILL:
            return "landfill"
        from law_agent.tools.fact_tools import treatment_code_info
        try:
            info = treatment_code_info(code) or {}
        except Exception:                                   # noqa: BLE001
            info = {}
        txt = f"{info.get('처리구분', '')} {info.get('처리분류', '')}"
        return "incineration" if "소각" in txt else "landfill" if "매립" in txt else "middle"


def _rc_sort(rc: str):
    return [int(x) if x.isdigit() else x for x in rc.replace("R-", "").split("-")]


def _items(v: Verdict | None) -> list[str]:
    return list((v.value or {}).get("items") or []) if v is not None else []


def _ok(v: Verdict | None) -> bool:
    return v is not None and v.status == "agreed" and v.answer in ("yes", "conditional")


# ── 판단 하나를 경로에 반영 ──────────────────────────────────

def apply_verdict(route: RouteDraft, used: list[str], v: Verdict | None, label: str, soft: bool = False,
                  blocking: bool = False) -> None:
    """판단 상태를 경로 상태·조건·질문·근거로. not_applicable·없음은 무시.
    soft: 경로를 좁히기만 하는 판단(B-2 재활용 제한)은 확인이 안 돼도 경로를 needs_info 로 막지 않고 conditional 로
          (확인되면 허용 유형만 남기는 판단이라, 모를 때 경로 전체를 '정보 필요'로 두면 지나침 — 2026.11.17 데모)."""
    cap_to = Status.CONDITIONAL if soft else Status.NEEDS_INFO
    if v is None or v.status == "not_applicable":
        return
    used.append(v.question_id)
    for q in v.quotes:
        if q.get("ref") and q["ref"] not in route.evidence_ids:
            route.evidence_ids.append(q["ref"])
    if v.status == "needs_human":
        route.cap(cap_to, f"{label}({v.question_id}): 사람 검토 필요")
        route.ask(f"review.{v.question_id}", f"{label} 판단을 사람이 검토해야 함 (모델 불일치 또는 근거 부족)", to="part2")
        return
    if v.status == "needs_info":
        route.cap(cap_to, f"{label}({v.question_id}): 사실 확인 필요")
        for m in v.missing_facts or ["관련 사실"]:
            route.ask(f"{v.question_id}.{m}", f"{label} 판단에 필요한 사실을 알려 주세요: {m}")
        return
    if v.status != "agreed":
        return
    if v.answer == "conditional":
        route.cap(Status.CONDITIONAL, f"{label}({v.question_id}): 조건부")
        disagree = [c for c in v.conditions if c.get("kind") == "model_disagreement"]
        for c in v.conditions:
            if c.get("kind") != "model_disagreement":
                add_condition(route, c.get("text", ""), c.get("ref"))
        if disagree:                                     # 모델 이견은 경로 조건이 아니라 검토 메모 (2026.11.17)
            route.reasons.append(f"{label}({v.question_id}): 모델 의견이 갈려 조건부로 낮춤 — 사람 확인 권장")
            route.ask(f"review.{v.question_id}", f"{label} 판단에서 모델 의견이 갈림 — 사람 확인", to="part2")
        for m in v.missing_facts:
            route.ask(f"{v.question_id}.{m}", f"{label} 조건 확인에 필요한 사실: {m}")
        if blocking and v.missing_facts:
            # 금지 여부를 가르는 사실(예: PCB 농도)을 모르면 조건부가 아니라 정보 필요 — 확인 전에는 재활용할 수 없음
            # (2026.11.18 PRE-06: route_agent needs_info / law_agent conditional)
            route.cap(Status.NEEDS_INFO, f"{label}({v.question_id}): 금지 여부를 가르는 사실 확인 필요")


def add_condition(route: RouteDraft, text: str, ref: str | None) -> None:
    text = (text or "").strip()
    if text and not any(c["text"] == text for c in route.conditions):
        route.conditions.append({"text": text, "basis": ref, "basis_status": BASIS_STATUS})


def add_text_verdict(route: RouteDraft, used: list[str], v: Verdict | None, label: str) -> None:
    """text 답(D-3·D-4·D-5·G 등)을 조건으로. 상태도 반영."""
    if v is None or v.status == "not_applicable":
        return
    apply_verdict(route, used, v, label)
    text = (v.value or {}).get("text") if v.status == "agreed" else None
    if text:
        ref = (v.quotes or [{}])[0].get("ref")
        add_condition(route, f"{label}: {text[:400]}", ref)


# ── 공통 ─────────────────────────────────────────────────────

def _case_codes(state: AgentState, eff: dict) -> tuple[str | None, list[str]]:
    main = (eff.get("values") or {}).get("waste_code")
    comps = [c.get("waste_code") for c in (state.get("case") or {}).get("components") or []
             if isinstance(c, dict) and c.get("waste_code")]
    codes = [main] + [c for c in comps if c != main] if main else comps
    return main, [c for c in codes if c]


def _waste_status(eff: dict) -> str:
    return "designated" if (eff.get("values") or {}).get("waste_status") in DESIGNATED else "general"


def _common(route: RouteDraft, used: list[str], vs: dict[str, Verdict], eff: dict) -> None:
    """모든 경로: 분류번호(A-1)·지정 여부(A-2) 판단 상태. 지정 의심이면 conditional + 분석 질문."""
    # A-1 이 확정 안 됐어도 파트 1 번호가 별표 4에 있으면 그 번호로 잠정 진행 → 경로를 막지 않고 조건부 (2026.11.18)
    apply_verdict(route, used, vs.get("A-1"), "분류번호", soft=bool((eff.get("provisional") or {}).get("waste_code")))
    apply_verdict(route, used, vs.get("A-2"), "지정폐기물 여부")
    if (eff.get("values") or {}).get("waste_status") == "designated_suspect":
        route.cap(Status.CONDITIONAL, "지정폐기물 의심: 성분 분석 결과에 따라 다시 판정")
        route.ask("analysis", "성분 분석으로 지정폐기물 해당 여부를 확인했나요?")
    if route.waste_status == "designated":
        add_text_verdict(route, used, vs.get("D-5") if route.gate == 2 else None, "지정폐기물 위탁 허가")


def _components_estimated(state: AgentState) -> bool:
    return any(str((c or {}).get("status") or "").lower() == "estimated"
               for c in (state.get("case") or {}).get("components") or [] if isinstance(c, dict))


# ── 관문 2 재활용 ────────────────────────────────────────────

def _restrict(rcs: list[str], allowed: list[str]) -> list[str]:
    if not allowed:
        return rcs
    return [rc for rc in rcs if any(rc == a or rc.startswith(a + "-") for a in allowed)]


def _route_types(state: AgentState, case: Any) -> list[str]:
    comps = [c for c in (state.get("case") or {}).get("components") or [] if isinstance(c, dict)]
    if len(comps) < 2:
        return ["single_material_recycle"]
    sep = getattr(case, "separable", None)
    if sep is True:
        return ["separate_then_recycle"]
    if sep is False:
        return ["mixed_recycle"]
    return ["separate_then_recycle", "mixed_recycle"]


def gate2(state: AgentState, case: Any, facts: Facts, out: Composed) -> None:
    vs = state.get("verdicts") or {}
    eff = effective_facts(state)
    b1, d1, b2 = vs.get("B-1"), vs.get("D-1"), vs.get("B-2")
    if b1 is not None and b1.status == "agreed" and b1.answer == "yes":
        out.skipped["gate2"] = {"action": "SKIP", "reason": "재활용 금지 폐기물 (B-1 판단)"}
        return
    if d1 is None or d1.status == "not_applicable":
        out.skipped["gate2"] = {"action": "SKIP", "reason": "재활용 유형 판단 없음 (D-1)"}
        return
    if not _ok(d1) or not _items(d1):
        reason = "허용 재활용 유형 없음 (D-1 판단)" if d1.status == "agreed" else f"재활용 유형 판단 보류 (D-1 {d1.status})"
        out.skipped["gate2"] = {"action": "RUN", "reason": reason, "missing_facts": d1.missing_facts}
        return

    main, codes = _case_codes(state, eff)
    codes = codes or [main or ""]
    allowed = _items(b2) if _ok(b2) else []
    attrs = (d1.value or {}).get("attrs") or {}

    def rcs_for(code_index: int) -> list[str]:
        """성분별 D-1.cN 이 있으면 그것, 없으면 D-1."""
        if code_index > 0:
            sub = vs.get(f"D-1.c{code_index}")
            if _ok(sub) and _items(sub):
                return _restrict([x for x in _items(sub) if x.startswith("R-")], allowed)
        return _restrict([x for x in _items(d1) if x.startswith("R-")], allowed)

    per_code = {c: rcs_for(i if len(codes) > 1 else 0) for i, c in enumerate(codes)}
    if len(codes) > 1:                                    # 성분 분류번호가 있으면 성분 순번 = components 순서
        comps = [c.get("waste_code") for c in (state.get("case") or {}).get("components") or [] if isinstance(c, dict)]
        per_code = {c: (rcs_for(comps.index(c) + 1) if c in comps else rcs_for(0)) for c in codes}
    pre_analysis = {rc for rc, a in attrs.items() if isinstance(a, dict) and a.get("pre_analysis")}

    def group(rcs: list[str]) -> dict:
        g: dict = {}
        for rc in sorted(set(rcs), key=_rc_sort):
            g.setdefault(facts.tier(rc), []).append(rc)
        return g

    def new_route(tier, route_type: str, rcs_by_code: dict[str, list[str]]) -> None:
        steps = [Step(1, "수집·운반", "transporter", waste_codes=list(codes))]
        if route_type == "separate_then_recycle":
            steps.append(Step(2, "재질별 선별·분리", "preprocessor", waste_codes=list(codes)))
        for code, rcs in rcs_by_code.items():
            tcs = sorted({tc for rc in rcs if (tc := facts.tcode_for(rc))})
            if tier is None:
                process, role = "중간가공폐기물 제조", "preprocessor"
            else:
                process, role = " / ".join(facts.tcode_name(tc) for tc in tcs) or TIER_TITLE[tier], "recycler"
            steps.append(Step(len(steps) + 1, process, role, tcs, rcs, [code]))
        all_rcs = sorted({rc for rcs in rcs_by_code.values() for rc in rcs}, key=_rc_sort)
        r = RouteDraft(gate=2, tier=tier, route_type=route_type, title=f"{TIER_TITLE[tier]} ({', '.join(all_rcs)})",
                       waste_codes=list(codes), waste_status=_waste_status(eff), steps=steps,
                       regulatory=Status.CONFIRMED, reasons=[f"D-1 판단: {', '.join(codes)}에 {', '.join(all_rcs)} 허용"],
                       r_code_detail=[{"r_code": rc, "tier": tier, "pre_analysis": rc in pre_analysis,
                                       "basis": (d1.quotes or [{}])[0].get("ref")} for rc in all_rcs],
                       basis_status=BASIS_STATUS)
        used: list[str] = []
        _common(r, used, vs, eff)
        apply_verdict(r, used, b1, "재활용 금지", blocking=True)
        apply_verdict(r, used, d1, "재활용 유형")
        for i in range(1, 10):
            if f"D-1.c{i}" in vs:
                apply_verdict(r, used, vs[f"D-1.c{i}"], f"성분 {i} 재활용 유형")
        if allowed:
            r.reasons.append(f"재활용 제한(B-2)으로 {', '.join(allowed)}만 허용")
        apply_verdict(r, used, b2, "재활용 제한", soft=True)
        need = [rc for rc in all_rcs if rc in pre_analysis]
        if need:
            r.required_tests.append({"test": "재활용 전 사전 분석", "r_codes": need,
                                     "basis": (d1.quotes or [{}])[0].get("ref")})
            if len(need) == len(all_rcs):
                r.cap(Status.CONDITIONAL, "모든 재활용 유형이 사전 분석 대상 (D-1)")
        _form(r, used, vs.get("D-2"), all_rcs)
        if tier == "energy_recovery":
            _energy(r, used, vs.get("E-1"), case)
        add_text_verdict(r, used, vs.get("D-3"), "재활용 기준")
        add_text_verdict(r, used, vs.get("D-4"), "재활용 업체 준수사항")
        if _components_estimated(state):
            r.cap(Status.CONDITIONAL, "구성 정보가 추정 값")
        out.routes.append(r)
        out.used.append(sorted(set(used)))

    for route_type in _route_types(state, case):
        if route_type == "separate_then_recycle" and len(codes) > 1:
            tiers = [group(per_code[c]) for c in codes]
            for tier in TIER_ORDER:
                if all(tier in t for t in tiers):
                    new_route(tier, route_type, {c: t[tier] for c, t in zip(codes, tiers)})
        elif len(codes) > 1:
            common = set.intersection(*(set(per_code[c]) for c in codes))
            groups = group(list(common))
            for tier in TIER_ORDER:
                if tier in groups:
                    new_route(tier, route_type, {"+".join(codes): groups[tier]})
        else:
            groups = group(per_code[codes[0]])
            for tier in TIER_ORDER:
                if tier in groups:
                    rtype = "direct_use" if tier == "reuse" and route_type == "single_material_recycle" else route_type
                    new_route(tier, rtype, {codes[0]: groups[tier]})

    g2 = [r for r in out.routes if r.gate == 2]
    if getattr(case, "combustible", None) is False:        # 사실: 타지 않으면 에너지 회수 경로 없음
        keep = [(r, u) for r, u in zip(out.routes, out.used) if not (r.gate == 2 and r.tier == "energy_recovery")]
        out.routes, out.used = [r for r, _ in keep], [u for _, u in keep]
    if len(_route_types(state, case)) == 2:                # 분리 가능 여부 모름 → 혼합물 경로 전부 needs_info
        for r in g2:
            r.ask("separable", "재질별로 나눌 수 있나요? (나눠서 가공 / 섞인 채 가공)")
            r.cap(Status.NEEDS_INFO, "분리 가능 여부 미확인")
    if not [r for r in out.routes if r.gate == 2]:
        out.skipped["gate2"] = {"action": "RUN", "reason": "허용 재활용 유형으로 만들 수 있는 경로 없음"}


def _form(r: RouteDraft, used: list[str], d2: Verdict | None, rcs: list[str]) -> None:
    """D-2: 사례 형태로 실제 할 수 있는 R코드. 재사용 경로에서 형태로 안 되는 유형이면 conditional."""
    if d2 is None or d2.status == "not_applicable":
        return
    apply_verdict(r, used, d2, "형태별 가능 유형")
    if d2.status == "agreed" and r.tier == "reuse":
        ok = set(_items(d2))
        can = [rc for rc in rcs if rc in ok]
        if not can:
            r.cap(Status.CONDITIONAL, "사례 형태로는 이 재사용 유형이 어려울 수 있음 (D-2)")
        elif len(can) < len(rcs):
            r.reasons.append(f"형태 기준으로 {', '.join(can)}만 해당 (D-2)")


def _energy(r: RouteDraft, used: list[str], e1: Verdict | None, case: Any) -> None:
    if getattr(case, "combustible", None) is None:
        r.ask("combustible", "타는 물질(가연성)인가요?")
        r.cap(Status.NEEDS_INFO, "가연성 미확인")
    if e1 is None or e1.status == "not_applicable":
        return
    apply_verdict(r, used, e1, "에너지 회수기준")
    if e1.status != "agreed":
        return
    val = e1.value or {}
    req = {"item": "에너지 회수기준", "criterion": val.get("criterion"), "case_value": val.get("case_value"),
           "unit": val.get("unit"), "result": val.get("result"), "basis": (e1.quotes or [{}])[0].get("ref"),
           "basis_status": BASIS_STATUS}
    r.required_tests.append({"test": "에너지 회수기준 확인", **req})
    if val.get("result") == "not_met":
        r.cap(Status.REJECTED, f"에너지 회수기준 미충족 (E-1): {val.get('criterion')}")
    elif val.get("result") == "unknown":
        r.cap(Status.NEEDS_INFO, "에너지 회수기준 확인에 사례 값 필요 (E-1)")
        r.ask("E-1.case_value", f"에너지 회수기준({val.get('criterion') or '저위발열량 등'}) 확인에 필요한 값을 알려 주세요")


# ── 관문 4 처분 ──────────────────────────────────────────────

def gate4(state: AgentState, case: Any, facts: Facts, out: Composed) -> None:
    vs = state.get("verdicts") or {}
    eff = effective_facts(state)
    f1, f2 = vs.get("F-1"), vs.get("F-2")
    main, codes = _case_codes(state, eff)
    code = main or (codes[0] if codes else "")
    transport = Step(1, "수집·운반", "transporter", waste_codes=[code])

    def base(title: str, steps: list[Step], kind: str) -> tuple[RouteDraft, list[str]]:
        r = RouteDraft(gate=4, tier="disposal", route_type="alternative_treatment", title=title, waste_codes=[code],
                       waste_status=_waste_status(eff), steps=steps, regulatory=Status.CONFIRMED,
                       reasons=["F-1 판단: 처분 방법"], basis_status=BASIS_STATUS)
        used: list[str] = []
        _common(r, used, vs, eff)
        apply_verdict(r, used, f1, "처분 방법")
        apply_verdict(r, used, f2, "처분 조건")
        if f2 is not None and f2.status == "agreed":
            for c in f2.conditions:
                add_condition(r, c.get("text", ""), c.get("ref"))
            if f2.conditions and r.regulatory == Status.CONFIRMED:
                r.regulatory = Status.CONDITIONAL
        if _components_estimated(state):
            r.cap(Status.CONDITIONAL, "구성 정보가 추정 값")
        return r, used

    items = _items(f1) if _ok(f1) else []
    tcs = [x for x in items if x.isdigit() and len(x) == 4]
    for w in items:                                   # 원문 방법 이름 → 코드 (2026.11.17: 코드는 법 원문에 없음)
        if not w.isdigit():
            tcs += [c for c in facts.tcodes_for_method(w) if c not in tcs]
    if not tcs:
        r, used = base("처분 (방법 미정)", [transport, Step(2, "처분 (방법 미정)", "treatment", waste_codes=[code])],
                       "pending")
        if f1 is None or f1.status in ("agreed", "not_applicable"):
            r.cap(Status.NEEDS_INFO, "허용 처분 방법(처리방법 코드)을 판단하지 못함 (F-1)")
            r.ask("disposal_method", "처분 방법을 정하려면 확인이 필요합니다 (F-1)", to="part2")
        out.routes.append(r)
        out.used.append(sorted(set(used)))
        return
    incin = [c for c in tcs if facts.tcode_kind(c) == "incineration"]
    land = [c for c in tcs if facts.tcode_kind(c) == "landfill"]
    middle = [c for c in tcs if c not in incin and c not in land]
    name = lambda cs: " / ".join(facts.tcode_name(c) for c in cs)      # noqa: E731
    if incin:
        r, used = base(f"소각: {name(incin)}", [transport, Step(2, name(incin), "treatment", incin, waste_codes=[code])],
                       "incineration")
        out.routes.append(r)
        out.used.append(sorted(set(used)))
    if land:
        steps = [transport]
        if middle:
            steps.append(Step(2, f"전처리: {name(middle)}", "treatment", middle, waste_codes=[code]))
        steps.append(Step(len(steps) + 1, name(land), "treatment", land, waste_codes=[code]))
        r, used = base(f"매립: {name(land)}" + (f" ({name(middle)} 후)" if middle else ""), steps, "landfill")
        out.routes.append(r)
        out.used.append(sorted(set(used)))
    if middle and not land:
        r, used = base(f"중간처분: {name(middle)}", [transport, Step(2, name(middle), "treatment", middle, waste_codes=[code])],
                       "middle")
        out.routes.append(r)
        out.used.append(sorted(set(used)))


# ── 관문 1 순환자원 (기본) ───────────────────────────────────

def gate1(state: AgentState, case: Any, out: Composed) -> None:
    vs = state.get("verdicts") or {}
    eff = effective_facts(state)
    c1, c2, g4 = vs.get("C-1"), vs.get("C-2"), vs.get("G-4")
    if _waste_status(eff) != "general":
        out.skipped["gate1"] = {"action": "SKIP", "reason": "지정폐기물(의심 포함)은 순환자원 판매 대상 아님"}
        return
    if c1 is None or c1.status == "not_applicable" or (c1.status == "agreed" and c1.answer == "no"):
        out.skipped["gate1"] = {"action": "RUN", "reason": "순환자원 지정 고시 품목 아님 (C-1 판단)"}
        return
    if c1.status != "agreed" or c1.answer == "unknown":     # 판단 보류 → 경로 없이 이유 (관문 2 D-1 과 같은 규칙)
        out.skipped["gate1"] = {"action": "RUN", "reason": f"순환자원 품목 판단 보류 (C-1 {c1.status})",
                                "missing_facts": c1.missing_facts}
        return
    main, codes = _case_codes(state, eff)
    code = main or (codes[0] if codes else "")
    rcs = sorted({x for x in _items(c2) if x.startswith("R-")}, key=_rc_sort) if _ok(c2) else []
    name = (state.get("case") or {}).get("name") or code
    r = RouteDraft(gate=1, tier=None, route_type="direct_use", title=f"지정 순환자원 판매: {name}", waste_codes=[code],
                   waste_status="general",
                   steps=[Step(1, "수집·운반 (필요 시)", "transporter", waste_codes=[code]),
                          Step(2, "순환자원 용도로 사용", "end_user", r_codes=rcs, waste_codes=[code])],
                   regulatory=Status.CONFIRMED, reasons=["C-1 판단: 순환자원 지정 고시 품목"], basis_status=BASIS_STATUS)
    used: list[str] = []
    _common(r, used, vs, eff)
    apply_verdict(r, used, c1, "순환자원 품목")
    if c2 is not None and c2.status != "not_applicable":
        apply_verdict(r, used, c2, "순환자원 용도·방법 기준")
        if c2.status == "agreed":
            for c in c2.conditions:
                add_condition(r, c.get("text", ""), c.get("ref"))
            if rcs:
                add_condition(r, f"고시 용도({', '.join(rcs)})로 사용하려는 자에게만 공급", (c2.quotes or [{}])[0].get("ref"))
            if c2.answer == "no":
                r.cap(Status.CONDITIONAL, "사례가 고시의 용도·방법 기준에 아직 맞지 않음 (C-2)")
    if g4 is not None and g4.status != "not_applicable":
        apply_verdict(r, used, g4, "인계서·수탁처리능력 확인 제외")
        if g4.status == "agreed":
            for c in g4.conditions:
                add_condition(r, c.get("text", ""), c.get("ref"))
    if len(codes) > 1:
        sep = getattr(case, "separable", None)
        if sep is True:
            r.cap(Status.CONDITIONAL, "다른 성분과 분리한 뒤 이 성분만 판매")
        elif sep is False:
            r.cap(Status.REJECTED, "다른 폐기물 성분과 분리할 수 없음")
        else:
            r.ask("separable", "다른 성분과 나눌 수 있나요?")
            r.cap(Status.NEEDS_INFO, "분리 가능 여부 미확인")
    out.routes.append(r)
    out.used.append(sorted(set(used)))


# ── 진입점 ───────────────────────────────────────────────────

def compose(state: AgentState, case: Any, facts: Facts | None = None) -> Composed:
    """판단 결과 → 경로. 출력 순서 = 법 순서 (관문 1 → 2 → 4)."""
    facts = facts or Facts()
    out = Composed()
    gate1(state, case, out)
    gate2(state, case, facts, out)
    gate4(state, case, facts, out)
    return out


def node(state: AgentState, case: Any = None, facts: Facts | None = None) -> dict:
    """composer 노드. 바뀐 칸: routes, audit."""
    if case is None:
        from law_agent.core.input_adapter import InputAdapter
        case = InputAdapter().adapt(state.get("case") or {})
    out = compose(state, case, facts)
    audit = {"node": "composer", "summary": {"routes": len(out.routes), "skipped": out.skipped},
             "output": [{"title": r.title, "gate": r.gate, "regulatory": r.regulatory.label, "used": u}
                        for r, u in zip(out.routes, out.used)]}
    return {"routes": out.routes, "audit": [audit]}
