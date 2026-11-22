#!/usr/bin/env bash
# 플랫폼(waste-chat) 실행: ./waste-chat.sh [--port 8080] [--lan]
# 법령 엔진(src/law_agent)·파트 1(src/part1)·data/ 는 이 폴더 것을 그대로 쓴다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="/Users/boseon/code/agent/.venv-waste/bin/python"
cd "$ROOT/src/waste_chat" && exec "$PY" app.py "$@"
