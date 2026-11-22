"""
사전검증 시험 세트 24건 (파트 1 계약 형식, 가상 데이터).

데이터: testdata/preliminary_testset.json   (PRELIM_TESTSET 환경변수로 바꿀 수 있음)
실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active $PY -m pytest -v preliminary/test_testset.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from preliminary.preliminary_integrator import PreliminaryIntegrator

DATA_DIR = Path(os.environ["WASTE_DATA_DIR"])
TESTSET = Path(os.environ.get(
    "PRELIM_TESTSET", Path(__file__).resolve().parent.parent / "testdata" / "preliminary_testset.json"))
G = ("gate1", "gate2", "gate3", "gate4")

R, S, NI, RV = "RUN", "SKIP", "NEEDS_INFO", "REVIEW"

# case_id: (분류 상태, 지정 판정, waste_status, 재활용 금지, [관문1,2,3,4], 전체 상태, 추가 확인)
EXPECTED = {
    # 일반·지정 기본
    "PRE-01": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {}),
    "PRE-02": ("MATCHED", "HAZARDOUS",     "designated", False, [S, R, S, R], "READY", {"mixed": True}),
    # 재활용 금지·제한 규칙
    "PRE-03": ("MATCHED", "HAZARDOUS",     "designated", True,  [S, S, S, R], "READY", {"rules": {"PR-L1", "PR-R0"}}),
    "PRE-04": ("MATCHED", "HAZARDOUS",     "designated", True,  [S, S, S, R], "READY", {"rules": {"PR-L3", "PR-R0"}}),
    "PRE-05": ("MATCHED", "HAZARDOUS",     "designated", False, [S, R, S, R], "READY", {"allowed": ["R-4-10"]}),
    "PRE-06": ("MATCHED", "HAZARDOUS",     "designated", False, [S, R, S, R], "READY", {"question": "PCB", "required": "analysis.pcb"}),
    "PRE-07": ("MATCHED", "HAZARDOUS",     "designated", False, [S, R, S, R], "READY", {"review": True}),
    # 지정 의심
    "PRE-08": ("MATCHED", "SUSPECTED",     "designated", False, [S, R, S, R], "READY", {"basis": "의심"}),
    "PRE-09": ("MATCHED", "SUSPECTED",     "designated", False, [S, R, S, R], "READY", {"basis": "의심"}),   # 파트 1이 designated로 보냄
    # 일반폐기물 (관문 1 후보 품목들)
    "PRE-10": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {}),
    "PRE-11": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {}),
    "PRE-12": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {}),
    "PRE-13": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {"question": "hazardous_suspect"}),
    # 혼합물 (성분별 분류번호)
    "PRE-14": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {"mixed": True, "components": 1}),
    "PRE-15": ("MATCHED", "HAZARDOUS",     "designated", False, [S, R, S, R], "READY", {"mixed": True}),
    "PRE-16": ("MATCHED", "HAZARDOUS",     "designated", True,  [S, S, S, R], "READY", {"mixed": True}),
    # 분류번호 문제
    "PRE-17": ("PARENT_CLASS", None, None, None, [NI, NI, NI, NI], "NEEDS_INFO", {}),
    "PRE-18": ("PARENT_CLASS", None, None, None, [NI, NI, NI, NI], "NEEDS_INFO", {}),
    "PRE-19": ("NEEDS_INFO",   None, None, None, [NI, NI, NI, NI], "NEEDS_INFO", {}),
    "PRE-20": ("REVIEW",       None, None, None, [RV, RV, RV, RV], "NEEDS_REVIEW", {}),
    "PRE-21": ("NOT_FOUND",    None, None, None, [NI, NI, NI, NI], "NEEDS_INFO", {}),
    # 경고·공통 규칙
    "PRE-22": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {"warning": "신고 분류번호"}),
    # 금지 물질이 "있다"고 답함 → 질문이 아니라 검토 필요 (관문 2는 RUN, 경로는 needs_info)
    "PRE-23": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {"rules": {"PR-D1c"}, "review": True, "warning": "PR-D1c"}),
    "PRE-24": ("MATCHED", "NON_HAZARDOUS", "general",    False, [R, R, R, R], "READY", {}),
}


@pytest.fixture(scope="module")
def integrator():
    return PreliminaryIntegrator(DATA_DIR)


@pytest.fixture(scope="module")
def cases():
    if not TESTSET.is_file():
        pytest.skip(f"시험 세트 없음: {TESTSET}")
    return {c["case_id"]: c for c in json.loads(TESTSET.read_text(encoding="utf-8"))["cases"]}


def test_all_cases_have_expected(cases):
    assert set(cases) == set(EXPECTED)


@pytest.mark.parametrize("cid", sorted(EXPECTED))
def test_case(integrator, cases, cid):
    cls, haz, wstat, blocked, plan, status, extra = EXPECTED[cid]
    r = integrator.run(cases[cid])

    statuses = [r.classification.status] + [c["classification_status"] for c in r.component_results]
    assert cls in statuses, statuses
    assert [r.gate_plan[g].action.value for g in G] == plan
    assert r.status == status
    assert r.case_version == 1 and r.schema_version == "1.0"

    if haz is not None:
        assert r.hazardous.status == haz
        assert r.waste_status == wstat
        assert r.restriction.recycling_blocked is blocked

    rules = {m["rule_id"] for m in r.restriction.matched_rules}
    if "rules" in extra:
        assert extra["rules"] <= rules, rules
    if "allowed" in extra:
        assert r.restriction.allowed_rcodes == extra["allowed"]
    if "review" in extra:
        assert r.restriction.review_required is True
    if "question" in extra:
        assert any(extra["question"] in q for q in r.questions + r.restriction.questions), r.questions
    if "required" in extra:
        assert extra["required"] in r.required_information
    if "basis" in extra:
        assert r.hazardous_basis == extra["basis"]
    if "mixed" in extra:
        assert r.mixture.is_mixed is True
    if "components" in extra:
        assert len(r.component_results) == extra["components"]
    if "warning" in extra:
        assert any(extra["warning"] in w for w in r.warnings)


def test_free_text_name_never_mismatch(integrator, cases):
    """파트 1의 name(자유 기술)이 공식 명칭 대조에 들어가지 않아야 함"""
    for cid, raw in cases.items():
        assert integrator.run(raw).classification.status != "MISMATCH", cid
