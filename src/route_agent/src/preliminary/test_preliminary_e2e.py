import json
import logging
from pathlib import Path
from typing import Any

from preliminary import InputAdapter, PreliminaryIntegrator

# Logger 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


# 실제 프로젝트 루트
PROJECT_ROOT = Path(
    "/home/a202192012/5.Agent_System/프로젝트/산업자원_순환·재활용_에이전트(MS)"
)

# CSV 데이터 경로
DATA_DIR = PROJECT_ROOT / "data" / "tables" / "active"

# 경로 확인
if not DATA_DIR.is_dir():
    raise FileNotFoundError(
        f"데이터 디렉터리가 없습니다: {DATA_DIR}"
    )
OUTPUT_DIR = (
    PROJECT_ROOT / "reports" / "preliminary"
)


def create_agent1_json() -> dict[str, Any]:
    """
    Agent 1에서 전달하는 JSON 입력 예시.
    실제 데이터에 맞게 필드를 변경할 수 있습니다.
    """

    return {
        "case_id": "TEST-001",
        "waste_name": "폐플라스틱",
        "waste_code": "07-01-01",
        "generation_process": "플라스틱 제조 공정",

        "is_mixed": False,
        "separable": None,

        "hazardous_suspect": False,

        "components": [],

        "analysis": {},

        "properties": {
            "physical_state": "SOLID",
            "contamination": False
        },

        "quantity": {
            "amount": 500,
            "unit": "kg",
            "frequency": "MONTHLY"
        },

        "location": {
            "address": "전북특별자치도 전주시"
        }
    }


def serialize_result(result: Any) -> dict[str, Any]:
    """Preliminary 결과를 JSON 직렬화 가능한 형태로 변환합니다."""

    if hasattr(result, "to_dict"):
        return result.to_dict()

    raise TypeError(
        "Preliminary 결과에 to_dict() 메서드가 없습니다."
    )


def run_end_to_end_test() -> dict[str, Any]:
    """Agent 1 JSON부터 Preliminary 전체 실행까지 테스트합니다."""

    logger.info("===== Preliminary 통합 테스트 시작 =====")

    # 1. 데이터 디렉터리 확인
    if not DATA_DIR.exists():
        raise FileNotFoundError(
            f"데이터 디렉터리가 없습니다: {DATA_DIR}"
        )

    # 2. Agent 1 JSON 생성
    agent1_json = create_agent1_json()

    logger.info("[1/4] Agent 1 JSON 생성 완료")

    # 3. JSON → WasteCase 변환
    adapter = InputAdapter()
    waste_case = adapter.adapt(
        json.dumps(agent1_json, ensure_ascii=False)
    )

    logger.info("[2/4] JSON → WasteCase 변환 완료")

    # 4. Preliminary 전체 실행
    integrator = PreliminaryIntegrator(
        data_dir=str(DATA_DIR)
    )

    result = integrator.run(waste_case)

    logger.info("[3/4] Preliminary 검사 완료")

    # 5. 결과 출력
    output = serialize_result(result)

    logger.info("[4/4] 결과 직렬화 완료")

    print("\n" + "=" * 60)
    print("AGENT 1 INPUT")
    print("=" * 60)
    print(
        json.dumps(
            agent1_json,
            ensure_ascii=False,
            indent=2
        )
    )

    print("\n" + "=" * 60)
    print("PRELIMINARY OUTPUT")
    print("=" * 60)
    print(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
            default=str
        )
    )

    # 6. 결과 파일 저장
    output_dir = Path("reports/preliminary")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "test_result.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "input": agent1_json,
                "output": output
            },
            file,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    logger.info("결과 저장: %s", output_path)

    return output


if __name__ == "__main__":
    try:
        result = run_end_to_end_test()

        print("\n[성공] Preliminary 통합 테스트 실행 완료")

    except Exception:
        logger.exception(
            "Preliminary 통합 테스트 실패"
        )
        raise
