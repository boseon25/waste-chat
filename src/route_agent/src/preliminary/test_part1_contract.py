"""
파트 1 계약 형식(1.0 + 협의 필드) 입력으로 사전검증을 시험합니다.

- 샘플: part1_sample.json (파트 1이 준 개발용 가상 샘플 2건)
- 정답표: 파트2_사전검증_정답값.md
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from preliminary.preliminary_integrator import PreliminaryIntegrator

DATA_DIR = Path(
    os.environ.get("WASTE_DATA_DIR", Path(__file__).resolve().parent / "data" / "tables" / "active")
).resolve()
SAMPLE = Path(
    os.environ.get("PART1_SAMPLE", Path(__file__).resolve().parent.parent / "part1_sample.json")
)
GATES = ("gate1", "gate2", "gate3", "gate4")


@pytest.fixture(scope="module")
def integrator() -> PreliminaryIntegrator:
    return PreliminaryIntegrator(DATA_DIR)


@pytest.fixture(scope="module")
def sample_cases() -> dict[str, dict]:
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 파일이 없습니다: {SAMPLE}")
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return {c["case_id"]: c for c in data["cases"]}


def plan(result) -> list[str]:
    return [result.gate_plan[g].action.value for g in GATES]


# ---------------------------------------------------------------------------
# 1. 파트 1 샘플 2건
# ---------------------------------------------------------------------------
def test_sample1_pe_film(integrator, sample_cases):
    r = integrator.run(sample_cases["DEMO-PART2-01"])
    assert r.classification.status == "MATCHED"
    assert r.classification.normalized_waste_code == "51-03-01"   # classification.selected_code에서 읽음
    assert r.hazardous.status == "NON_HAZARDOUS"
    assert r.waste_status == "general"
    assert r.restriction.recycling_blocked is False
    assert plan(r) == ["RUN", "RUN", "RUN", "RUN"]
    assert r.status == "READY"
    assert r.case_version == 1 and r.schema_version == "1.0"


def test_sample2_cleaning_solvent(integrator, sample_cases):
    r = integrator.run(sample_cases["DEMO-PART2-02"])
    assert r.classification.status == "MATCHED"
    assert r.classification.normalized_waste_code == "04-02-00"
    assert r.hazardous.status == "HAZARDOUS"
    assert r.waste_status == "designated"
    assert r.hazardous_basis == "분류번호"
    assert plan(r) == ["SKIP", "RUN", "SKIP", "RUN"]
    assert r.status == "READY"
    # 재질 2개(세척용제·기계유)지만 분류번호는 하나 → 혼합 사실만 기록, 관문 선택엔 영향 없음
    assert r.mixture.is_mixed is True
    assert r.mixture.separable is False


def test_free_text_name_is_not_used_as_official_name(integrator, sample_cases):
    """name(자유 기술)이 공식 명칭 대조에 들어가면 MISMATCH가 나므로 쓰지 않아야 함"""
    r = integrator.run(sample_cases["DEMO-PART2-01"])
    assert r.classification.status != "MISMATCH"


def test_region_sido_normalized(integrator, sample_cases):
    case = integrator.input_adapter.adapt(sample_cases["DEMO-PART2-01"])
    assert case.region["sido"] == "전북"          # 전북특별자치도 → 업체 표 표기
    assert case.region["sigungu"] == "전주시"


def test_separable_text_converted(integrator, sample_cases):
    case = integrator.input_adapter.adapt(sample_cases["DEMO-PART2-02"])
    assert [c.separable for c in case.components] == [False, False]   # "아니오"
    assert case.foreign_matter["separable"] is False


def test_reported_code_differs_warning(integrator, sample_cases):
    c = copy.deepcopy(sample_cases["DEMO-PART2-01"])
    c["classification"]["reported_code"] = "51-03-02"
    r = integrator.run(c)
    assert any("신고 분류번호" in w for w in r.warnings)


def test_part1_designated_but_general_code_is_suspected(integrator, sample_cases):
    """파트 1이 designated로 보냈는데 분류번호는 일반 → 지정 의심으로 처리"""
    c = copy.deepcopy(sample_cases["DEMO-PART2-01"])
    c["hazardous_suspect"] = False
    c["designated_waste_status"] = "designated"
    r = integrator.run(c)
    assert r.hazardous.status == "SUSPECTED"
    assert plan(r) == ["SKIP", "RUN", "SKIP", "RUN"]


def test_part1_non_designated_but_designated_code_warns(integrator, sample_cases):
    c = copy.deepcopy(sample_cases["DEMO-PART2-02"])
    c["designated_waste_status"] = "non_designated"
    r = integrator.run(c)
    assert r.hazardous.status == "HAZARDOUS"
    assert any("파트 1은 비지정" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# 2. 정답표 (파트2_사전검증_정답값.md)
# ---------------------------------------------------------------------------
EXPECTED = [
    # (id, codes, suspect, cls_status, waste_status, blocked, gate1..4, status)
    ("general",           ["51-03-01"],             False, "MATCHED",      "general",    False, "RUN",  "RUN",  "RUN",  "RUN", "READY"),
    ("general_suspect",   ["51-03-01"],             True,  "MATCHED",      "designated", False, "SKIP", "RUN",  "SKIP", "RUN", "READY"),
    ("general_unknown",   ["51-03-01"],             None,  "MATCHED",      "general",    False, "RUN",  "RUN",  "RUN",  "RUN", "READY"),
    ("designated",        ["01-01-01"],             False, "MATCHED",      "designated", False, "SKIP", "RUN",  "SKIP", "RUN", "READY"),
    ("asbestos",          ["07-01-01"],             True,  "MATCHED",      "designated", True,  "SKIP", "SKIP", "SKIP", "RUN", "READY"),
    ("medical_block",     ["10-12-01"],             False, "MATCHED",      "designated", True,  "SKIP", "SKIP", "SKIP", "RUN", "READY"),
    ("medical_restrict",  ["10-12-06"],             False, "MATCHED",      "designated", False, "SKIP", "RUN",  "SKIP", "RUN", "READY"),
    ("needs_info_rule",   ["06-01-07"],             False, "MATCHED",      "designated", False, "SKIP", "RUN",  "SKIP", "RUN", "READY"),
    ("needs_review_rule", ["03-06-05"],             False, "MATCHED",      "designated", False, "SKIP", "RUN",  "SKIP", "RUN", "READY"),
    ("mixed_general",     ["51-03-01", "51-03-02"], False, "MATCHED",      "general",    False, "RUN",  "RUN",  "RUN",  "RUN", "READY"),
    ("mixed_designated",  ["51-03-01", "01-01-01"], False, "MATCHED",      "designated", False, "SKIP", "RUN",  "SKIP", "RUN", "READY"),
    ("mixed_blocked",     ["51-03-01", "07-01-01"], False, "MATCHED",      "designated", True,  "SKIP", "SKIP", "SKIP", "RUN", "READY"),
    ("parent",            ["51-03"],                False, "PARENT_CLASS", None, None, "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO"),
    ("four_digit",        ["5103"],                 False, "PARENT_CLASS", None, None, "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO"),
    ("missing_code",      [None],                   False, "NEEDS_INFO",   None, None, "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO"),
    ("invalid_code",      ["51-03-XX"],             False, "REVIEW",       None, None, "REVIEW", "REVIEW", "REVIEW", "REVIEW", "NEEDS_REVIEW"),
    ("unknown_code",      ["9999"],                 False, "NOT_FOUND",    None, None, "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO"),
    ("mixed_parent",      ["51-03-01", "51-03"],    False, "PARENT_CLASS", None, None, "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO", "NEEDS_INFO"),
]


def make_case(cid: str, codes: list, suspect) -> dict:
    """파트 1 계약 형식. 분류번호가 여러 개면 성분별 분류번호로 넣는다."""
    case = {
        "schema_version": "1.0",
        "case_id": cid,
        "case_version": 1,
        "hazardous_suspect": suspect,
        "quantity": {"monthly_ton": [3.0, 3.1, 2.9], "unit": "ton"},
        "region": {"sido": "전북특별자치도", "sigungu": "전주시"},
    }
    if len(codes) == 1:
        if codes[0]:
            case["classification"] = {"reported_code": codes[0], "selected_code": codes[0]}
        case["components"] = [{"material": "test", "share_pct": 100, "status": "confirmed"}]
    else:
        case["components"] = [
            {"material": f"m{i}", "waste_code": c, "share_pct": 100 / len(codes), "separable": "예"}
            for i, c in enumerate(codes)
        ]
    return case


@pytest.mark.parametrize("row", EXPECTED, ids=[r[0] for r in EXPECTED])
def test_expected(integrator, row):
    cid, codes, suspect, cls, wstat, blocked, g1, g2, g3, g4, status = row
    r = integrator.run(make_case(cid, codes, suspect))
    combined = [r.classification.status] + [c["classification_status"] for c in r.component_results]
    assert cls in combined
    assert r.status == status
    assert plan(r) == [g1, g2, g3, g4]
    assert r.case_version == 1 and r.schema_version == "1.0"
    if wstat:
        assert r.waste_status == wstat
        assert r.restriction.recycling_blocked is blocked


def test_restrict_rule_keeps_allowed_rcodes(integrator):
    r = integrator.run(make_case("R", ["10-12-06"], False))
    assert r.restriction.allowed_rcodes == ["R-4-10"]


def test_needs_info_rule_question_recorded(integrator):
    r = integrator.run(make_case("P", ["06-01-07"], False))
    assert any("PCB" in q for q in r.questions)
    assert "analysis.pcb" in r.required_information


def test_result_serializes(integrator, sample_cases):
    d = integrator.run(sample_cases["DEMO-PART2-02"]).to_dict()
    json.dumps(d, ensure_ascii=False)
    assert d["waste_status"] == "designated"
    assert d["gate_plan"]["gate1"]["action"] == "SKIP"
