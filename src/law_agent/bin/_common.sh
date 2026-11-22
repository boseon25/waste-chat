# waste.sh · waste-admin.sh 공통 준비. waste_agent 폴더(ROOT)에 두고 쓴다. 키 값은 출력하지 않는다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -d "$ROOT/src/law_agent" ] || ROOT="$(cd "$ROOT/../../.." && pwd)"   # src/law_agent/bin 에서 바로 부를 때
export ROOT
VENV="${WASTE_VENV:-$HOME/5.Agent_System/.venv-ai-agent}"
[ -z "$WASTE_VENV" ] && [ -f "$ROOT/.venv/bin/activate" ] && VENV="$ROOT/.venv"   # setup 으로 만든 가상환경
if [ -z "$VIRTUAL_ENV" ] && [ -f "$VENV/bin/activate" ]; then . "$VENV/bin/activate"; fi
[ -f "$ROOT/env.sh" ] && . "$ROOT/env.sh"
if [ -f "$ROOT/waste.env" ]; then set -a; . "$ROOT/waste.env"; set +a; fi   # 윈도우와 같이 쓰는 경로 설정 (있으면)
if [ -f "$ROOT/src/law_agent/llm/.env" ]; then set -a; . "$ROOT/src/law_agent/llm/.env"; set +a; fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
export PART1_DIR="${PART1_DIR:-$ROOT/src/part1}"
cd "$ROOT"
