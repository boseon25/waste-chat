"""
관문 3·4 테스트.

실행 (src/경로_매칭_에이전트/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active PART1_SAMPLE=<경로>/part1_sample.json \
  $PY -m pytest -v gates/test_gate34.py
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from gates import gate3, gate4
from gates.models import Status
from gates.tables import Tables
from preliminary.models import GateAction
from preliminary.preliminary_integrator import PreliminaryIntegrator

DATA_DIR = Path(os.environ["WASTE_DATA_DIR"])
SAMPLE = Path(os.environ.get("PART1_SAMPLE", Path(__file__).resolve().parent.parent / "preliminary" / "part1_sample.json"))


@pytest.fixture(scope="module")
def integrator():
    return PreliminaryIntegrator(DATA_DIR)


@pytest.fixture(scope="module")
def tables():
    return Tables.load(DATA_DIR)


@pytest.fixture(scope="module")
def samples():
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 없음: {SAMPLE}")
    return {c["case_id"]: c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]}


def prep(integrator, raw):
    case = integrator.input_adapter.adapt(raw)
    return case, integrator.run(case)


def make_case(code, **extra):
    case = {"schema_version": "1.0", "case_id": "G", "case_version": 1, "hazardous_suspect": False,
            "classification": {"selected_code": code}, "components": [{"material": "x", "share_pct": 100}]}
    case.update(extra)
    return case


# ---------------------------------------------------------------------------
# 관문 4
# ---------------------------------------------------------------------------
def test_g4_sample1_pe_film(integrator, tables, samples):
    case, pre = prep(integrator, samples["DEMO-PART2-01"])
    routes = gate4.run(case, pre, tables)
    assert [r.title.split(":")[0] for r in routes] == ["소각", "매립"]
    inc, land = routes
    assert inc.steps[-1].treatment_codes == ["2101", "2102"]
    assert land.steps[1].treatment_codes == ["2106", "2107", "2108"]          # 파쇄·절단·용융 후
    assert land.steps[-1].treatment_codes == ["2201", "2205", "2206"]
    assert any("15센티미터" in c["text"] for c in land.conditions)
    assert not any("15센티미터" in c["text"] for c in inc.conditions)         # 소각 경로엔 파쇄 기준 없음
    assert any("소각이 곤란" in c["text"] for c in land.conditions)
    # 지킬 조건(파쇄 크기, 소각 곤란 등)이 있어 표가 confirmed여도 conditional. basis_status는 표 상태를 따름
    assert all(r.regulatory == Status.CONDITIONAL and r.basis_status in ("검토 대기", "확정") for r in routes)
    assert all("D3-2자" in r.reasons[0] for r in routes)


def test_g4_sample2_liquid_solvent(integrator, tables, samples):
    case, pre = prep(integrator, samples["DEMO-PART2-02"])
    routes = gate4.run(case, pre, tables)
    assert all("D4-2다4" in r.reasons[0] for r in routes)                     # 액체 행만 (고체 D4-2다5 제외)
    assert [r.title.split(":")[0] for r in routes] == ["소각", "중간처분"]
    assert all(any("지정폐기물 처분업" in c["text"] for c in r.conditions) for r in routes)


def test_g4_liquid_solid_unknown_asks(integrator, tables):
    case, pre = prep(integrator, make_case("04-02-00"))
    routes = gate4.run(case, pre, tables)
    assert {r.reasons[0].split()[2] for r in routes} == {"D4-2다4", "D4-2다5"}
    assert all(r.regulatory == Status.NEEDS_INFO for r in routes)


def test_g4_blocked_still_runs(integrator, tables):
    case, pre = prep(integrator, make_case("07-01-01", hazardous_suspect=True))   # 석면
    assert pre.gate_plan["gate4"].action == GateAction.RUN
    routes = gate4.run(case, pre, tables)
    assert routes and routes[0].steps[-1].treatment_codes == ["2201"]
    assert not any("재활용(관문 1·2)이 우선" in c["text"] for r in routes for c in r.conditions)


def test_g4_needs_info_rule(integrator, tables):
    case, pre = prep(integrator, make_case("51-10-01"))      # 폐촉매: 가연성 여부 확인 필요
    routes = gate4.run(case, pre, tables)
    assert routes and all(r.regulatory == Status.NEEDS_INFO for r in routes)


def test_g4_generic_combustible_rule(integrator, tables):
    case, pre = prep(integrator, make_case("51-28-02", combustible=True))   # 폐지: 행 있음 (D3-2다)
    assert gate4.run(case, pre, tables)
    # 처분 방법 표에 행이 없는 일반폐기물 말단 코드 → 일반 소각 규칙(D3-1가)
    for code in tables.waste_names:
        if code.startswith("51-") and code.count("-") == 2 and not gate4._matching_rows(code, False, tables):
            case, pre = prep(integrator, make_case(code, combustible=True))
            if pre.classification.status == "MATCHED":
                break
    else:
        pytest.fail("처분 방법 표에 없는 일반폐기물 말단 코드를 찾지 못함")
    routes = gate4.run(case, pre, tables)
    assert routes and "D3-1가" in routes[0].reasons[0]
    case, pre = prep(integrator, make_case(code, combustible=False))
    assert gate4.run(case, pre, tables)[0].regulatory == Status.NEEDS_INFO     # 타지 않으면 방법 확인 필요


def test_g4_daily_100kg_note(integrator, tables):
    raw = make_case("51-03-01", combustible=True, quantity={"monthly_ton": [4.0, 4.0, 4.0]})
    case, pre = prep(integrator, raw)
    land = [r for r in gate4.run(case, pre, tables) if r.title.startswith("매립")][0]
    assert any("100kg 이상" in c["text"] for c in land.conditions)


# ---------------------------------------------------------------------------
# 관문 3
# ---------------------------------------------------------------------------
GOOD = {"intended_use": "제품 원료", "paid_trade": True, "handling_risk": False, "substitutes_resource": True,
        "notified_use_criteria": True, "is_transboundary_regulated": False}


def test_g3_sample1_questions_only(integrator, tables, samples):
    case, pre = prep(integrator, samples["DEMO-PART2-01"])
    (r,) = gate3.run(case, pre, tables)
    assert r.regulatory == Status.NEEDS_INFO
    fields = [q["field"] for q in r.questions]
    assert len(fields) == len(set(fields)) and "paid_trade" in fields and "intended_use" in fields


def test_g3_sample2_designated_skip(integrator, tables, samples):
    case, pre = prep(integrator, samples["DEMO-PART2-02"])
    assert pre.gate_plan["gate3"].action == GateAction.SKIP
    assert gate3.run(case, pre, tables) == []


def test_g3_all_facts_good_is_conditional(integrator, tables):
    raw = make_case("51-03-01", gate3_facts=GOOD, analysis={"hazardous_substances": True})
    (r,) = gate3.run(*prep(integrator, raw), tables)
    assert r.regulatory == Status.CONDITIONAL and r.questions == []     # 장기 후보라 confirmed는 없음


def test_g3_fuel_use_rejected(integrator, tables):
    raw = make_case("51-03-01", gate3_facts=dict(GOOD, intended_use="고형연료"), analysis={"hazardous_substances": True})
    (r,) = gate3.run(*prep(integrator, raw), tables)
    assert r.regulatory == Status.REJECTED


def test_g3_fuel_exception_for_rice_bran(integrator, tables):
    raw = make_case("51-19-00", gate3_facts=dict(GOOD, intended_use="고형연료"), analysis={"hazardous_substances": True})
    (r,) = gate3.run(*prep(integrator, raw), tables)
    assert r.regulatory == Status.CONDITIONAL                           # 왕겨·쌀겨는 연료 용도 예외


def test_g3_handling_risk_true(integrator, tables):
    raw = make_case("51-03-01", gate3_facts=dict(GOOD, handling_risk=True), analysis={"hazardous_substances": True})
    (r,) = gate3.run(*prep(integrator, raw), tables)
    assert r.regulatory == Status.NEEDS_INFO


def test_g4_suspected_general_code_uses_general_rows(integrator, tables):
    """분류번호는 일반인데 지정 의심 → 일반 처분 행(D3) + 지정 처분 조건"""
    case, pre = prep(integrator, make_case("51-03-01", hazardous_suspect=True))
    assert pre.waste_status == "designated"
    routes = gate4.run(case, pre, tables)
    assert routes and all("D3-2자" in r.reasons[0] for r in routes)
    assert all(any("지정폐기물 처분업" in c["text"] for c in r.conditions) for r in routes)
    assert all(any("지정폐기물 의심" in c["text"] for c in r.conditions) for r in routes)


def test_g4_mixed_general_and_designated(integrator, tables):
    raw = make_case("51-03-01")
    raw.pop("classification")
    raw["components"] = [{"material": "a", "waste_code": "51-03-01", "share_pct": 60, "separable": "아니오"},
                         {"material": "b", "waste_code": "01-01-01", "share_pct": 40, "separable": "아니오"}]
    case, pre = prep(integrator, raw)
    routes = gate4.run(case, pre, tables)
    assert {r.waste_codes[0] for r in routes} == {"51-03-01", "01-01-01"}
    assert all(r.title.startswith("[") for r in routes)
    assert not any(r.title.endswith("처분 방법 확인 필요") for r in routes)


def test_g4_medical_has_no_rows_yet(integrator, tables):
    """의료폐기물은 처분 방법 표에 행이 없음 (표 보완 필요) → 확인 필요 경로"""
    case, pre = prep(integrator, make_case("10-12-01", hazardous_suspect=True))
    routes = gate4.run(case, pre, tables)
    assert len(routes) == 1 and routes[0].regulatory == Status.NEEDS_INFO