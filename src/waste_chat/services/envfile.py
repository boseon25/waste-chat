"""프로젝트 폴더의 .env를 읽어 환경변수로 올린다. 이미 설정된 환경변수는 덮어쓰지 않는다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path=None):
    """KEY=VALUE 줄만 읽는다. 프로젝트 폴더에 없으면 상위 폴더의 .env도 확인한다."""
    candidates = [Path(path)] if path else [ROOT / ".env", ROOT.parent / ".env"]
    for file in candidates:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if name and value and name not in os.environ:
                os.environ[name] = value
        return str(file)
    return None


def api_key():
    return os.environ.get("TIMELYGPT_API_KEY") or None


def model():
    return os.environ.get("TIMELYGPT_MODEL") or None


def agent_model():
    """서류 작성 에이전트가 법령·실무 질문에 답할 때 쓰는 모델. 근거 등급을 지키는 데 더 정확하고 느리다."""
    return os.environ.get("TIMELYGPT_AGENT_MODEL") or "openai/gpt-6.1-sol"
