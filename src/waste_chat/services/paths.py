"""waste_agent 폴더 구조 안에서의 위치. waste-chat은 waste_agent/src/waste_chat 에 있고,
법령 엔진(src/law_agent)·파트 1(src/part1)·표·법령 원문(data/)은 waste_agent 것을 그대로 쓴다(복사본을 두지 않음).
다른 곳에 둔 waste_agent를 쓰려면 환경변수 WASTE_AGENT_ROOT."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                       # waste_agent/src/waste_chat
AGENT = Path(os.environ.get("WASTE_AGENT_ROOT") or ROOT.parents[1]).resolve()   # waste_agent
SRC = AGENT / "src"
PART1 = SRC / "part1"
TABLES = AGENT / "data" / "tables" / "active"
REFERENCE = AGENT / "data" / "reference"
LAW_DIR = AGENT / "data" / "law" / "chunk_v2"
LAW_AGENT_DATA = AGENT / "data" / "law_agent"
