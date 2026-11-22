from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import pytest

from preliminary.hazardous_checker import HazardousChecker
from preliminary.models import GateAction, WasteCase
from preliminary.preliminary_integrator import PreliminaryIntegrator
from preliminary.restriction_checker import RestrictionChecker
from preliminary.waste_classifier import WasteClassifier, normalize_waste_code


# ---------------------------------------------------------------------------
# 0. 테스트 설정
# ---------------------------------------------------------------------------
# 실제 프로젝트의 CSV 디렉터리를 환경변수로 지정합니다.
DATA_DIR = Path(
    os.environ.get(
        "WASTE_DATA_DIR",
        Path(__file__).resolve().parent / "data" / "tables" / "active",
    )
).resolve()

# 테스트 결과 저장 디렉터리
RESULT_DIR = Path(__file__).resolve().parent / "test_results"
WASTE_CODES = DATA_DIR / "waste_codes_4.csv"
CROSSWALK = DATA_DIR / "waste_code_crosswalk.csv"
HAZARDOUS = DATA_DIR / "hazardous_codes.csv"
RESTRICTIONS = DATA_DIR / "prohibited_restricted.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    """UTF-8-SIG CSV 파일을 읽습니다."""
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def json_payload(raw_json: str) -> dict[str, Any]:
    """테스트 JSON 문자열을 Agent 입력용 dict로 변환합니다."""
    payload = json.loads(raw_json)
    assert isinstance(payload, dict), "테스트 JSON 최상위 값은 객체여야 합니다."
    return payload


# ---------------------------------------------------------------------------
# 1. JSON 테스트 입력 데이터
#    실제 Agent 1이 PreliminaryIntegrator.run()에 전달하는 입력 형태입니다.
# ---------------------------------------------------------------------------
JSON_TEST_CASES: dict[str, str] = {
    # 4자리 폐기물코드: Crosswalk를 통해 중분류로 연결되어야 합니다.
    "category_code": r'''{
        "waste_case_id": "JSON-CASE-001",
        "waste_code": "0101",
        "waste_name": "폐합성고분자화합물",
        "generation_process": "플라스틱 사출 공정",
        "is_mixed": false,
        "separable": true,
        "components": [],
        "hazardous_suspect": false,
        "analysis": {},
        "properties": {
            "physical_state": "solid",
            "material": "합성수지"
        },
        "quantity": {
            "value": 120,
            "unit": "kg/day"
        },
        "location": {
            "region": "전북특별자치도"
        }
    }''',

    # 공식 말단 분류번호: 분류 검증 이후 후속 검사가 진행되어야 합니다.
    "terminal_code": r'''{
        "waste_case_id": "JSON-CASE-002",
        "waste_code": "01-01-01",
        "generation_process": "플라스틱 압출 공정",
        "is_mixed": false,
        "separable": true,
        "components": [],
        "hazardous_suspect": false,
        "analysis": {},
        "properties": {
            "physical_state": "solid",
            "material": "폴리에틸렌"
        },
        "quantity": {
            "value": 50,
            "unit": "kg/day"
        },
        "location": {
            "region": "전북특별자치도"
        }
    }''',

    # 공식 명칭과 입력 명칭이 다르면 MISMATCH가 되어야 합니다.
    "wrong_name": r'''{
        "waste_case_id": "JSON-CASE-003",
        "waste_code": "01-01-01",
        "waste_name": "존재하지 않는 폐기물명",
        "generation_process": "플라스틱 압출 공정",
        "is_mixed": false,
        "components": [],
        "hazardous_suspect": false
    }''',

    # 석면 코드: 제한 CSV의 BLOCK_TO_GATE4 규칙이 적용되어야 합니다.
    "asbestos": r'''{
        "waste_case_id": "JSON-CASE-004",
        "waste_code": "07-01-01",
        "generation_process": "건축물 해체 공정",
        "waste_name": "흩날릴 우려가 없는 폐석면",
        "is_mixed": false,
        "components": [],
        "hazardous_suspect": true,
        "analysis": {},
        "properties": {
            "physical_state": "solid",
            "contains_asbestos": true
        }
    }''',

    # 혼합폐기물 JSON 구조: 성분 배열 파싱을 확인합니다.
    "mixed_waste": r'''{
        "waste_case_id": "JSON-CASE-005",
        "waste_code": "01-01-01",
        "is_mixed": true,
        "separable": true,
        "components": [
            {
                "name": "폐폴리에틸렌",
                "waste_code": "01-01-01",
                "share_pct": 70.0,
                "properties": {
                    "physical_state": "solid"
                }
            },
            {
                "name": "기타 합성수지",
                "waste_code": "01-01-02",
                "share_pct": 30.0,
                "properties": {
                    "physical_state": "solid"
                }
            }
        ],
        "hazardous_suspect": false,
        "analysis": {
            "moisture_pct": 2.5
        }
    }''',

    # 코드가 없으면 NEEDS_INFO가 되어야 합니다.
    "missing_code": r'''{
        "waste_case_id": "JSON-CASE-006",
        "waste_name": "폐합성고분자화합물",
        "generation_process": "플라스틱 가공 공정",
        "is_mixed": false,
        "components": []
    }''',

    # 지원하지 않는 코드 형식은 자동 추정하지 않고 검토 대상으로 분류합니다.
    "invalid_code": r'''{
        "waste_case_id": "JSON-CASE-007",
        "waste_code": "01-01-XX",
        "is_mixed": false,
        "components": []
    }''',

    # Crosswalk에 없는 정상 형식 코드는 NOT_FOUND가 되어야 합니다.
    "unknown_code": r'''{
        "waste_case_id": "JSON-CASE-008",
        "waste_code": "9999",
        "is_mixed": false,
        "components": []
    }''',
}


@pytest.fixture(scope="session", autouse=True)
def validate_data_files() -> None:
    missing = [
        str(path)
        for path in (WASTE_CODES, CROSSWALK, HAZARDOUS, RESTRICTIONS)
        if not path.is_file()
    ]
    if missing:
        pytest.fail(
            "필수 CSV 파일을 찾지 못했습니다. WASTE_DATA_DIR을 확인하세요.\n"
            + "\n".join(missing)
        )


@pytest.fixture(scope="session")
def classifier() -> WasteClassifier:
    return WasteClassifier(WASTE_CODES, CROSSWALK)


@pytest.fixture(scope="session")
def hazardous_checker() -> HazardousChecker:
    return HazardousChecker(HAZARDOUS)


@pytest.fixture(scope="session")
def restriction_checker() -> RestrictionChecker:
    return RestrictionChecker(RESTRICTIONS)


@pytest.fixture(scope="session")
def integrator() -> PreliminaryIntegrator:
    return PreliminaryIntegrator(DATA_DIR)


# ---------------------------------------------------------------------------
# 2. JSON 입력 자체의 구조 및 파싱 테스트
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name", list(JSON_TEST_CASES))
def test_json_payload_is_valid(case_name: str) -> None:
    payload = json_payload(JSON_TEST_CASES[case_name])
    assert payload.get("waste_case_id")
    assert isinstance(payload.get("components", []), list)


def test_json_mixed_components_are_parsed() -> None:
    payload = json_payload(JSON_TEST_CASES["mixed_waste"])
    case = WasteCase.from_mapping(payload)

    assert case.is_mixed is True
    assert len(case.components) == 2
    assert case.components[0].name == "폐폴리에틸렌"
    assert case.components[0].share_pct == 70.0


# ---------------------------------------------------------------------------
# 3. 코드 형식 및 분류기 단위 테스트
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0101", "0101"),
        ("01", "01"),
        ("01-01", "01-01"),
        ("01-01-01", "01-01-01"),
        ("010101", "01-01-01"),
        (None, None),
        ("01-01-XX", None),
        ("12345", None),
    ],
)
def test_normalize_waste_code(raw: str | None, expected: str | None) -> None:
    assert normalize_waste_code(raw) == expected


@pytest.mark.parametrize(
    ("case_name", "expected_status"),
    [
        ("category_code", "PARENT_CLASS"),
        ("terminal_code", "MATCHED"),
        ("wrong_name", "MISMATCH"),
        ("missing_code", "NEEDS_INFO"),
        ("invalid_code", "REVIEW"),
        ("unknown_code", "NOT_FOUND"),
    ],
)
def test_classifier_from_json(
    classifier: WasteClassifier,
    case_name: str,
    expected_status: str,
) -> None:
    payload = json_payload(JSON_TEST_CASES[case_name])
    result = classifier.classify(
        payload.get("waste_code"),
        payload.get("waste_name"),
    )
    assert result.status == expected_status


def test_single_level_terminal_code_is_valid(classifier: WasteClassifier) -> None:
    result = classifier.classify("03-02-00")
    assert result.status == "MATCHED"
    assert result.is_terminal is True


# ---------------------------------------------------------------------------
# 4. CSV 전체 데이터 무결성 테스트
# ---------------------------------------------------------------------------
def test_all_official_codes_have_expected_classification(
    classifier: WasteClassifier,
) -> None:
    rows = read_csv(WASTE_CODES)
    assert rows

    for row in rows:
        code = row["분류번호"].strip()
        result = classifier.classify(code)
        expected = "MATCHED" if row["말단"].strip().upper() == "Y" else "PARENT_CLASS"
        assert result.status == expected, (
            f"{code}: expected={expected}, actual={result.status}"
        )


def test_all_crosswalk_terminal_codes_are_valid(
    classifier: WasteClassifier,
) -> None:
    rows = read_csv(CROSSWALK)
    assert rows

    for row in rows:
        payload = {
            "waste_case_id": f"CROSSWALK-{row['폐기물코드']}",
            "waste_code": row["폐기물코드"],
        }
        result = classifier.classify(payload["waste_code"])
        assert result.status == "PARENT_CLASS"
        assert len(result.terminal_codes) == int(row["말단_수"])
        assert set(result.terminal_codes) == {
            item.strip() for item in row["말단_분류번호"].split(";") if item.strip()
        }


def test_hazardous_csv_matches_official_designation(
    classifier: WasteClassifier,
) -> None:
    rows = read_csv(HAZARDOUS)
    assert rows

    for row in rows:
        result = classifier.classify(row["분류번호"].strip())
        assert result.status == "MATCHED", row["분류번호"]
        assert result.hazardous_designation == "Y", row["분류번호"]


# ---------------------------------------------------------------------------
# 5. 지정폐기물 및 제한 규칙 테스트
# ---------------------------------------------------------------------------
def test_all_hazardous_codes_are_detected(
    classifier: WasteClassifier,
    hazardous_checker: HazardousChecker,
) -> None:
    for row in read_csv(HAZARDOUS):
        code = row["분류번호"].strip()
        payload = {
            "waste_case_id": f"HAZ-{code}",
            "waste_code": code,
            "hazardous_suspect": True,
        }
        case = WasteCase.from_mapping(payload)
        classification = classifier.classify(code)
        result = hazardous_checker.evaluate(case, classification)
        assert result.status == "HAZARDOUS", (
            f"{code}: status={result.status}, warnings={result.warnings}"
        )


def test_non_hazardous_terminal_code_is_not_hazardous(
    classifier: WasteClassifier,
    hazardous_checker: HazardousChecker,
) -> None:
    candidate = next(
        row
        for row in read_csv(WASTE_CODES)
        if row["말단"].strip().upper() == "Y"
        and row["지정폐기물"].strip().upper() == "N"
    )
    code = candidate["분류번호"].strip()
    payload = {"waste_case_id": "NON-HAZ", "waste_code": code}
    case = WasteCase.from_mapping(payload)
    classification = classifier.classify(code)

    result = hazardous_checker.evaluate(case, classification)
    assert result.status == "NON_HAZARDOUS"


def test_parent_class_is_not_evaluated_by_hazardous_checker(
    classifier: WasteClassifier,
    hazardous_checker: HazardousChecker,
) -> None:
    payload = json_payload(JSON_TEST_CASES["category_code"])
    case = WasteCase.from_mapping(payload)
    classification = classifier.classify(case.waste_code, case.waste_name)

    result = hazardous_checker.evaluate(case, classification)
    assert result.status == "NOT_EVALUATED"


def test_asbestos_json_activates_block_rule(
    restriction_checker: RestrictionChecker,
) -> None:
    payload = json_payload(JSON_TEST_CASES["asbestos"])
    case = WasteCase.from_mapping(payload)
    result = restriction_checker.evaluate(case)

    assert result.recycling_blocked is True
    assert result.status == "BLOCKED"
    assert any(rule.get("rule_id") == "PR-L1" for rule in result.matched_rules)


# ---------------------------------------------------------------------------
# 6. 통합기 및 Gate 테스트: JSON을 그대로 run()에 전달
# ---------------------------------------------------------------------------
def test_parent_json_stops_downstream_checks(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["category_code"])
    result = integrator.run(payload)

    assert result.classification.status == "PARENT_CLASS"
    assert result.hazardous.status == "NOT_EVALUATED"
    assert result.restriction.status == "NOT_EVALUATED"
    assert result.status == "NEEDS_INFO"
    assert all(item.action == GateAction.NEEDS_INFO for item in result.gate_plan.values())


def test_wrong_name_json_requires_review(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["wrong_name"])
    result = integrator.run(payload)

    assert result.classification.status == "MISMATCH"
    assert result.status == "NEEDS_REVIEW"
    assert all(item.action == GateAction.REVIEW for item in result.gate_plan.values())


def test_asbestos_json_skips_gate2(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["asbestos"])
    result = integrator.run(payload)

    assert result.classification.status == "MATCHED"
    assert result.restriction.recycling_blocked is True
    assert result.gate_plan["gate2"].action == GateAction.SKIP


def test_terminal_json_reaches_downstream_checks(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["terminal_code"])
    result = integrator.run(payload)

    assert result.classification.status == "MATCHED"
    assert result.hazardous.status != "NOT_EVALUATED"
    assert result.restriction.status != "NOT_EVALUATED"


def test_missing_code_json_requires_information(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["missing_code"])
    result = integrator.run(payload)

    assert result.classification.status == "NEEDS_INFO"
    assert result.status == "NEEDS_INFO"
    assert all(item.action == GateAction.NEEDS_INFO for item in result.gate_plan.values())


def test_invalid_code_json_requires_review(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["invalid_code"])
    result = integrator.run(payload)

    assert result.classification.status == "REVIEW"
    assert result.status == "NEEDS_REVIEW"
    assert all(item.action == GateAction.REVIEW for item in result.gate_plan.values())


def test_json_result_gate2_action_is_valid(integrator: PreliminaryIntegrator) -> None:
    payload = json_payload(JSON_TEST_CASES["terminal_code"])
    result = integrator.run(payload)

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    restored = json.loads(serialized)
    assert restored["waste_case_id"] == payload["waste_case_id"]
    assert restored["classification"]["status"] == "MATCHED"
    assert restored["gate_plan"]["gate2"]["action"] in {
        "RUN", "SKIP", "NEEDS_INFO", "REVIEW"
    }

# ---------------------------------------------------------------------------
# 7. InputAdapter 통합 테스트: JSON 문자열을 그대로 run()에 전달
# ---------------------------------------------------------------------------

def test_json_string_directly_passed_to_integrator(
    integrator: PreliminaryIntegrator,
) -> None:
    """JSON 문자열이 InputAdapter를 거쳐 정상 처리되는지 확인합니다."""

    raw_json = JSON_TEST_CASES["terminal_code"]

    # json.loads() 또는 json_payload()를 거치지 않고 직접 전달
    result = integrator.run(raw_json)

    assert result.waste_case_id == "JSON-CASE-002"
    assert result.classification.status == "MATCHED"
    assert result.hazardous.status != "NOT_EVALUATED"
    assert result.restriction.status != "NOT_EVALUATED"


def test_json_bytes_directly_passed_to_integrator(
    integrator: PreliminaryIntegrator,
) -> None:
    """UTF-8 JSON bytes 입력도 정상 처리되는지 확인합니다."""

    raw_json = JSON_TEST_CASES["terminal_code"].encode("utf-8")

    result = integrator.run(raw_json)

    assert result.waste_case_id == "JSON-CASE-002"
    assert result.classification.status == "MATCHED"


def test_json_string_parent_class_requires_information(
    integrator: PreliminaryIntegrator,
) -> None:
    """부모 분류 코드가 JSON 문자열 입력에서도 올바르게 판정되는지 확인합니다."""

    raw_json = JSON_TEST_CASES["category_code"]

    result = integrator.run(raw_json)

    assert result.classification.status == "PARENT_CLASS"
    assert result.hazardous.status == "NOT_EVALUATED"
    assert result.restriction.status == "NOT_EVALUATED"
    assert result.status == "NEEDS_INFO"


def test_invalid_json_string_raises_adapter_error(
    integrator: PreliminaryIntegrator,
) -> None:
    """잘못된 JSON 문자열은 입력 변환 단계에서 예외가 발생해야 합니다."""

    from preliminary.input_adapter import InputAdapterError

    raw_json = '{"waste_case_id": "INVALID", "waste_code": '

    with pytest.raises(InputAdapterError):
        integrator.run(raw_json)


def test_json_result_can_be_serialized(integrator):
    payload = json_payload(JSON_TEST_CASES["terminal_code"])
    result = integrator.run(payload)

    result_dict = result.to_dict()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    output_path = RESULT_DIR / "preliminary_result.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            result_dict,
            file,
            ensure_ascii=False,
            indent=2,
        )

    assert output_path.exists()

    with output_path.open("r", encoding="utf-8") as file:
        saved_result = json.load(file)

    assert saved_result["waste_case_id"] == result.waste_case_id