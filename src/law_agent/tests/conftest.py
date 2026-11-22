"""src/ 를 import 경로에 넣는다 (python3 -m pytest -q src/law_agent/tests 로 어디서 돌려도 되게)."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 테스트 폴더 자체도 import 경로에 (fake_law 같은 테스트 도움 모듈)
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_tables(monkeypatch, tmp_path_factory):
    """서버의 실제 표(WASTE_DATA_DIR)를 읽지 않게 빈 폴더로 (서버에서 표 답·잠정 분류번호가 켜져 결과가 달라졌음).
    표가 필요한 시험은 자기 fixture 에서 다시 정한다."""
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path_factory.mktemp("no_tables")))
    from law_agent.tools import fact_tools
    fact_tools._read.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_road(monkeypatch):
    """서버에 카카오 키가 있어도 테스트는 실제 길찾기를 부르지 않는다 (test_road 는 자기가 다시 켬)."""
    monkeypatch.setenv("WASTE_NO_ROAD", "1")


@pytest.fixture(autouse=True)
def _no_review_llm(monkeypatch):
    """서류 검토의 글 칸 AI 검토는 기본으로 끈다 (test_text_check 가 자기가 켬)."""
    monkeypatch.setenv("LAW_AGENT_REVIEW_LLM", "0")
