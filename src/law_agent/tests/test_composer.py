"""agents/composer.py 테스트 — 판단 결과(Verdict)를 직접 넣어 경로 모양·상태를 확인. 사실 데이터는 가짜."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_agent.agents import composer as cp
from law_agent.core.input_adapter import InputAdapter
from law_agent.core.routes import Status
from law_agent.state import Verdict, initial_state

CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]
TESTSET = {c["case_id"]: c for c in json.loads(
    (Path(__file__).resolve().parents[1] / "eval" / "data" / "preliminary_testset.json").read_text(encoding="utf-8"))["cases"]}
REF = "WMA_RULE:B4의3@20261112#aa"


class FakeFacts(cp.Facts):
    TIER = {"R-1": "reuse", "R-2": "reuse", "R-3": "material_recycle", "R-4": "material_recycle",
            "R-6": "material_recycle", "R-9": "energy_recovery", "R-10": None}
    TCODE = {"R-1": "2001", "R-2": "2002", "R-3": "2003", "R-4": "2004", "R-6": "2006", "R-9": "2009", "R-10": "2010"}

    def tier(self, rc):
        return self.TIER.get("-".join(rc.split("-")[:2]))

    def tcode_for(self, rc):
        return self.TCODE.get("-".join(rc.split("-")[:2]))

    def tcode_name(self, code):
        return {"2003": "합성수지 원료", "2009": "에너지 회수", "2101": "일반 소각", "2201": "관리형 매립",
                "2301": "파쇄"}.get(code, code)

    def tcode_kind(self, code):
        return {"2101": "incineration", "2201": "landfill"}.get(code, "middle")

    def tcodes_for_method(self, word):
        return {"소각": ["2101"], "매립": ["2201"], "파쇄": ["2301"]}.get(word, [])


FACTS = FakeFacts()


def V(qid, answer="yes", status="agreed", items=None, **kw):
    value = kw.pop("value", {"items": items} if items is not None else {})
    quotes = kw.pop("quotes", [{"ref": REF, "text": "원문"}])
    return Verdict(qid, status, answer, value, quotes=quotes, **kw)


def base_verdicts(**over):
    vs = {"A-1": V("A-1"), "A-2": V("A-2", "no"), "B-1": V("B-1", "no"), "B-2": V("B-2", "no", items=[]),
          "C-1": V("C-1", "no"), "D-1": V("D-1", items=["R-3-2", "R-3-3", "R-9-1"]),
          "D-2": V("D-2", items=["R-3-2", "R-3-3", "R-9-1"]),
          "E-1": V("E-1", value={"criterion": "저위발열량 3천", "case_value": 10200, "result": "met"}),
          "F-1": V("F-1", items=["2101", "2201"]), "F-2": V("F-2", "no")}
    vs.update(over)
    return vs


def run(case, vs):
    st = initial_state(case)
    st["verdicts"] = vs
    return cp.compose(st, InputAdapter().adapt(case), FACTS)


def titles(out):
    return [(r.gate, r.title, r.regulatory.label) for r in out.routes]


# ── 관문 2 ────────────────────────────────────────────────────

def test_gate2_tiers_and_steps():
    out = run(CASES[0], base_verdicts())
    g2 = [r for r in out.routes if r.gate == 2]
    assert [r.tier for r in g2] == ["material_recycle", "energy_recovery"]
    mat = g2[0]
    assert mat.title == "재생이용 (R-3-2, R-3-3)" and mat.regulatory == Status.CONFIRMED
    assert [(s.role, s.treatment_codes, s.r_codes) for s in mat.steps] == [
        ("transporter", [], []), ("recycler", ["2003"], ["R-3-2", "R-3-3"])]
    assert mat.waste_status == "general" and mat.waste_codes == ["51-03-01"]
    assert REF in mat.evidence_ids and {"A-1", "A-2", "B-1", "D-1"} <= set(out.used[out.routes.index(mat)])
    energy = g2[1]
    assert any(t.get("test") == "에너지 회수기준 확인" and t["result"] == "met" for t in energy.required_tests)


def test_b1_prohibited_skips_gate2():
    out = run(CASES[0], base_verdicts(**{"B-1": V("B-1", "yes")}))
    assert not [r for r in out.routes if r.gate == 2] and "B-1" in out.skipped["gate2"]["reason"]
    assert [r for r in out.routes if r.gate == 4]                              # 처분 경로는 남음


def test_b1_conditional_caps_and_carries_conditions():
    b1 = V("B-1", "conditional", conditions=[{"text": "금지물질이 아니면", "quote": "q", "ref": REF}],
           missing_facts=["세척용제 성분"])
    out = run(CASES[0], base_verdicts(**{"B-1": b1}))
    g2 = [r for r in out.routes if r.gate == 2]
    # 금지 여부를 가르는 사실(세척용제 성분)이 빠졌으면 conditional 이 아니라 needs_info (2026.11.18 PRE-06)
    assert all(r.regulatory == Status.NEEDS_INFO for r in g2)
    assert any(c["text"] == "금지물질이 아니면" for c in g2[0].conditions)
    assert any("세척용제 성분" in q["text"] and q["to"] == "part1" for q in g2[0].questions)


def test_b2_restricts_rcodes():
    out = run(CASES[0], base_verdicts(**{"B-2": V("B-2", "yes", items=["R-3-3"])}))
    g2 = [r for r in out.routes if r.gate == 2]
    assert [r.title for r in g2] == ["재생이용 (R-3-3)"] and "B-2" in g2[0].reasons[-1] + "B-2"


def test_pre_analysis_and_energy_not_met():
    d1 = V("D-1", items=["R-3-2", "R-9-1"], value={"items": ["R-3-2", "R-9-1"],
                                                   "attrs": {"R-3-2": {"pre_analysis": True}}})
    e1 = V("E-1", value={"criterion": "저위발열량 3천", "case_value": 1000, "result": "not_met"})
    out = run(CASES[0], base_verdicts(**{"D-1": d1, "E-1": e1}))
    mat = next(r for r in out.routes if r.tier == "material_recycle")
    assert mat.regulatory == Status.CONDITIONAL and mat.required_tests[0]["r_codes"] == ["R-3-2"]
    energy = next(r for r in out.routes if r.tier == "energy_recovery")
    assert energy.regulatory == Status.REJECTED


def test_d1_needs_info_skips_gate2_with_reason():
    out = run(CASES[0], base_verdicts(**{"D-1": V("D-1", "unknown", "needs_info", missing_facts=["성분"])}))
    assert "보류" in out.skipped["gate2"]["reason"] and out.skipped["gate2"]["missing_facts"] == ["성분"]


def test_needs_human_caps_and_asks_part2():
    out = run(CASES[0], base_verdicts(**{"A-1": V("A-1", "unknown", "needs_human")}))
    r = out.routes[0]
    assert r.regulatory == Status.NEEDS_INFO and any(q["to"] == "part2" for q in r.questions)


def test_designated_status_and_d5():
    d5 = V("D-5", value={"text": "지정폐기물 재활용 허가 업체에만"})
    out = run(CASES[1], base_verdicts(**{"A-2": V("A-2", "yes"), "D-5": d5,
                                         "D-1": V("D-1", items=["R-6-1"])}))
    g2 = [r for r in out.routes if r.gate == 2]
    assert g2 and all(r.waste_status == "designated" for r in g2)
    assert any("지정폐기물 위탁 허가" in c["text"] for c in g2[0].conditions)


def test_mixture_with_component_codes_separable():
    case = TESTSET["PRE-14"]                                   # PP 51-03-01 + 고무 51-03-02, 분리 가능
    vs = base_verdicts(**{"D-1.c1": V("D-1.c1", items=["R-3-2"]), "D-1.c2": V("D-1.c2", items=["R-3-1", "R-9-1"])})
    out = run(case, vs)
    g2 = [r for r in out.routes if r.gate == 2]
    assert [r.route_type for r in g2] == ["separate_then_recycle"]           # 두 성분 모두 재생이용 있음
    steps = g2[0].steps
    assert [s.role for s in steps] == ["transporter", "preprocessor", "recycler", "recycler"]
    assert steps[2].r_codes == ["R-3-2"] and steps[3].r_codes == ["R-3-1"]


def test_mixture_separable_unknown_both_types_needs_info():
    case = dict(TESTSET["PRE-14"])
    case["components"] = [dict(c, separable=None) for c in case["components"]]
    out = run(case, base_verdicts())
    g2 = [r for r in out.routes if r.gate == 2]
    assert {r.route_type for r in g2} == {"separate_then_recycle", "mixed_recycle"}
    assert all(r.regulatory == Status.NEEDS_INFO for r in g2)


def test_suspect_caps_conditional():
    out = run(CASES[0], base_verdicts(**{"A-2": V("A-2", "conditional", missing_facts=["성분 분석"])}))
    for r in out.routes:
        assert r.waste_status == "designated" and r.regulatory <= Status.CONDITIONAL


# ── 관문 4 ────────────────────────────────────────────────────

def test_gate4_incineration_and_landfill():
    out = run(CASES[0], base_verdicts(**{"F-1": V("F-1", items=["2101", "2201", "2301"])}))
    g4 = [r for r in out.routes if r.gate == 4]
    assert [r.title for r in g4] == ["소각: 일반 소각", "매립: 관리형 매립 (파쇄 후)"]
    assert [s.treatment_codes for s in g4[1].steps] == [[], ["2301"], ["2201"]]
    assert all(r.tier == "disposal" and r.route_type == "alternative_treatment" for r in g4)


def test_gate4_conditions_from_f2():
    f2 = V("F-2", "yes", conditions=[{"text": "소각이 곤란한 경우에만 매립", "quote": "q", "ref": REF}])
    out = run(CASES[0], base_verdicts(**{"F-2": f2}))
    land = next(r for r in out.routes if r.title.startswith("매립"))
    assert land.regulatory == Status.CONDITIONAL and land.conditions[0]["text"] == "소각이 곤란한 경우에만 매립"


def test_gate4_unknown_method_pending():
    out = run(CASES[0], base_verdicts(**{"F-1": V("F-1", "no", items=[])}))
    g4 = [r for r in out.routes if r.gate == 4]
    assert [r.title for r in g4] == ["처분 (방법 미정)"] and g4[0].regulatory == Status.NEEDS_INFO
    assert g4[0].steps[1].treatment_codes == []


# ── 관문 1 ────────────────────────────────────────────────────

def test_gate1_basic():
    c2 = V("C-2", "yes", items=["R-4-1"], conditions=[{"text": "이물질 없이 보관", "quote": "q", "ref": REF}])
    out = run(TESTSET["PRE-10"], base_verdicts(**{"C-1": V("C-1"), "C-2": c2,
                                                    "G-4": V("G-4", "conditional",
                                                             conditions=[{"text": "고시 기준을 지키는 동안", "quote": "q", "ref": REF}])}))
    g1 = [r for r in out.routes if r.gate == 1]
    assert len(g1) == 1 and g1[0].steps[1].role == "end_user" and g1[0].steps[1].r_codes == ["R-4-1"]
    texts = [c["text"] for c in g1[0].conditions]
    assert "이물질 없이 보관" in texts and any("R-4-1" in t for t in texts) and "고시 기준을 지키는 동안" in texts
    assert g1[0].regulatory == Status.CONDITIONAL                               # G-4 조건부


def test_gate1_skipped_for_designated_or_not_item():
    out = run(CASES[0], base_verdicts(**{"C-1": V("C-1", "no")}))
    assert "C-1" in out.skipped["gate1"]["reason"]
    out = run(CASES[0], base_verdicts(**{"C-1": V("C-1"), "A-2": V("A-2", "yes")}))
    assert "지정폐기물" in out.skipped["gate1"]["reason"]


def test_order_and_used():
    out = run(TESTSET["PRE-10"], base_verdicts(**{"C-1": V("C-1"), "C-2": V("C-2", items=["R-4-1"])}))
    assert [r.gate for r in out.routes] == sorted(r.gate for r in out.routes)
    assert len(out.used) == len(out.routes)


def test_node_output_and_dicts():
    st = initial_state(CASES[0])
    st["verdicts"] = base_verdicts()
    res = cp.node(st, InputAdapter().adapt(CASES[0]), FACTS)
    assert res["routes"] and res["audit"][0]["node"] == "composer"
    d = res["routes"][0].to_dict()
    assert d["regulatory"] in ("confirmed", "conditional", "needs_info", "rejected") and d["basis_status"] == cp.BASIS_STATUS



def test_gate4_from_method_names():
    """F-1 이 원문 방법 이름(소각·매립)으로 답해도 처리방법 코드로 바꿔 경로를 만든다."""
    vs = base_verdicts(**{"F-1": V("F-1", items=["소각", "매립"])})
    titles = [r.title for r in run(CASES[0], vs).routes if r.gate == 4]
    assert any(t.startswith("소각") for t in titles) and any(t.startswith("매립") for t in titles)


def test_tcodes_for_method_real_facts(monkeypatch):
    """소각·매립 행의 처리구분이 '위탁처리'여도 쓴다 (재활용 행 = R코드가 붙은 행만 뺌). 표에 없으면 route_agent 묶음."""
    rows = [{"code": "2101", "처리구분": "위탁처리", "처리분류": "소각", "세분류": "일반소각", "r_code": ""},
            {"code": "2102", "처리구분": "위탁처리", "처리분류": "소각", "세분류": "고온소각", "r_code": ""},
            {"code": "2003", "처리구분": "위탁처리", "처리분류": "재활용", "세분류": "합성수지", "r_code": "R-3"},
            {"code": "2301", "처리구분": "중간처분", "처리분류": "기계적", "세분류": "증발농축", "r_code": ""}]
    monkeypatch.setattr("law_agent.tools.fact_tools.treatment_codes", lambda *a, **k: rows)
    f = cp.Facts()
    assert f.tcodes_for_method("소각") == ["2101", "2102"] and f.tcodes_for_method("고온소각") == ["2102"]
    assert f.tcodes_for_method("매립") == sorted(cp.LANDFILL)          # 표에 매립 코드 없음 → 묶음 그대로
    assert f.tcodes_for_method("증발농축") == ["2301"] and f.tcodes_for_method("합성수지") == []



def test_b1_conditional_without_missing_fact_stays_conditional():
    b1 = V("B-1", "conditional", conditions=[{"text": "금지물질이 아니면", "quote": "q", "ref": REF}])
    out = run(CASES[0], base_verdicts(**{"B-1": b1}))
    assert all(r.regulatory == Status.CONDITIONAL for r in out.routes if r.gate == 2)
