"""
관문 2 테스트. 사전검증 → 관문 2 순서로 실제 판정표를 써서 돌린다.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active PART1_SAMPLE=<경로>/part1_sample.json \
  $PY -m pytest -v gates/test_gate2.py
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from gates import gate2
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


def run(integrator, tables, raw):
    case = integrator.input_adapter.adapt(raw)
    pre = integrator.run(case)
    assert pre.gate_plan["gate2"].action == GateAction.RUN
    return {r.tier: r for r in gate2.run(case, pre, tables)}, pre


def make_case(codes, suspect=False, **extra):
    case = {"schema_version": "1.0", "case_id": "T", "case_version": 1, "hazardous_suspect": suspect}
    if len(codes) == 1:
        case["classification"] = {"selected_code": codes[0]}
        case["components"] = [{"material": "x", "share_pct": 100}]
    else:
        case["components"] = [{"material": f"m{i}", "waste_code": c, "share_pct": 50, "separable": extra.pop("separable", None)}
                               for i, c in enumerate(codes)]
    case.update(extra)
    return case


# ---------------------------------------------------------------------------
# 파트 1 샘플
# ---------------------------------------------------------------------------
def test_sample1_pe_film(integrator, tables, samples):
    routes, _ = run(integrator, tables, samples["DEMO-PART2-01"])
    assert list(routes) == ["reuse", "material_recycle", "energy_recovery", None]   # 법 순서

    assert routes["reuse"].route_type == "direct_use"
    assert routes["reuse"].regulatory == Status.CONDITIONAL          # 파쇄라 재사용 어려움

    m = routes["material_recycle"]
    assert m.steps[-1].r_codes == ["R-3-2", "R-3-3", "R-3-4", "R-4-4"]
    assert m.steps[-1].treatment_codes == ["2003", "2004"]
    assert m.regulatory == Status.CONFIRMED
    assert m.route_type == "single_material_recycle"

    e = routes["energy_recovery"]
    assert e.steps[-1].r_codes == ["R-8-1", "R-8-2", "R-9-1", "R-9-3"]
    assert e.regulatory == Status.CONFIRMED                           # R-9-3은 사전 분석 불필요
    assert e.required_tests[0]["r_codes"] == ["R-8-1", "R-8-2", "R-9-1"]

    i = routes[None]
    assert i.steps[-1].role == "preprocessor" and i.steps[-1].treatment_codes == ["2010"]
    assert any("최종 재활용처" in c["text"] for c in i.conditions)


def test_sample2_cleaning_solvent(integrator, tables, samples):
    routes, pre = run(integrator, tables, samples["DEMO-PART2-02"])
    assert pre.waste_status == "designated"
    assert {t: r.steps[-1].r_codes for t, r in routes.items()} == {
        "reuse": ["R-2-1"],
        "material_recycle": ["R-3-2", "R-4-6"],
        "energy_recovery": ["R-9-2"],
        None: ["R-10-1"],
    }
    assert all(r.route_type == "mixed_recycle" for r in routes.values())     # 재질 분리 "아니오"
    assert all(r.waste_status == "designated" for r in routes.values())
    assert all(any("지정폐기물 재활용 허가" in c["text"] for c in r.conditions) for r in routes.values())
    assert routes["reuse"].regulatory == Status.CONFIRMED                    # 액상 세척 후 재사용(R-2-1)은 형태 문제 없음


# ---------------------------------------------------------------------------
# 경우별
# ---------------------------------------------------------------------------
def test_restrict_rule_limits_rcodes(integrator, tables):
    routes, _ = run(integrator, tables, make_case(["10-12-06"]))     # 태반: R-4-10만 허용
    assert {t: r.steps[-1].r_codes for t, r in routes.items()} == {"material_recycle": ["R-4-10"]}


def test_suspected_caps_to_conditional(integrator, tables):
    routes, pre = run(integrator, tables, make_case(["51-03-01"], suspect=True))
    assert pre.hazardous_basis == "의심"
    assert routes["material_recycle"].regulatory == Status.CONDITIONAL
    assert any(q["field"] == "analysis" for q in routes["material_recycle"].questions)


def test_not_combustible_drops_energy(integrator, tables):
    routes, _ = run(integrator, tables, make_case(["51-03-01"], combustible=False))
    assert "energy_recovery" not in routes


def test_combustible_unknown_asks(integrator, tables):
    routes, _ = run(integrator, tables, make_case(["51-03-01"]))
    assert routes["energy_recovery"].regulatory == Status.NEEDS_INFO
    assert any(q["field"] == "combustible" for q in routes["energy_recovery"].questions)


def test_mixed_codes_separable_unknown_makes_both(integrator, tables):
    raw = make_case(["51-03-01", "51-03-02"], material_form="원형보존", combustible=True)
    case = integrator.input_adapter.adapt(raw)
    pre = integrator.run(case)
    routes = gate2.run(case, pre, tables)
    assert {r.route_type for r in routes} == {"separate_then_recycle", "mixed_recycle"}
    assert all(r.regulatory == Status.NEEDS_INFO for r in routes)


def test_mixed_codes_not_separable_uses_common_rcodes(integrator, tables):
    raw = make_case(["51-03-01", "51-03-02"], separable="아니오", combustible=True)
    case = integrator.input_adapter.adapt(raw)
    pre = integrator.run(case)
    routes = {r.tier: r for r in gate2.run(case, pre, tables)}
    common = set(gate2.allowed_rcodes("51-03-01", tables)) & set(gate2.allowed_rcodes("51-03-02", tables))
    got = {rc for r in routes.values() for rc in r.steps[-1].r_codes}
    assert got == common
    assert "R-3-2" not in got                       # 51-03-02에는 R-3-2가 없음


def test_mixed_codes_separable_per_component_steps(integrator, tables):
    raw = make_case(["51-03-01", "51-03-02"], separable="예", combustible=True)
    case = integrator.input_adapter.adapt(raw)
    pre = integrator.run(case)
    r = next(r for r in gate2.run(case, pre, tables) if r.tier == "material_recycle")
    assert r.route_type == "separate_then_recycle"
    assert [s.role for s in r.steps] == ["transporter", "preprocessor", "recycler", "recycler"]
    assert r.steps[2].waste_codes == ["51-03-01"] and "R-3-2" in r.steps[2].r_codes
    assert r.steps[3].waste_codes == ["51-03-02"] and "R-3-2" not in r.steps[3].r_codes


def test_needs_info_rule_caps(integrator, tables):
    routes, _ = run(integrator, tables, make_case(["06-01-07"]))   # PCB 분석 필요
    assert routes and all(r.regulatory == Status.NEEDS_INFO for r in routes.values())
    assert any("PCB" in q["text"] for r in routes.values() for q in r.questions)


def test_route_dict_serializes(integrator, tables, samples):
    routes, _ = run(integrator, tables, samples["DEMO-PART2-01"])
    d = routes["material_recycle"].to_dict()
    json.dumps(d, ensure_ascii=False)
    assert d["regulatory"] == "confirmed" and d["gate"] == 2
