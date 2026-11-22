#!/usr/bin/env bash
# 플랫폼(waste-chat) 준비·실행
#   ./waste-chat.sh setup            처음 한 번: 가상환경(.venv) 만들고 패키지 설치, 키 파일(src/waste_chat/.env) 준비
#   ./waste-chat.sh [--port 8080]    실행 (브라우저가 열림. --lan 이면 같은 망의 다른 컴퓨터에서도 접속)
# 법령 엔진(src/law_agent)·파트 1(src/part1)·data/ 는 이 폴더 것을 그대로 쓴다.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/src/waste_chat"

find_python() {   # Python 3.10 이상
  for c in "$ROOT/.venv/bin/python" python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

if [ "$1" = "setup" ]; then
  PY="$(find_python)" || { echo "Python 3.10 이상이 필요합니다. https://www.python.org 에서 설치한 뒤 다시 실행하세요."; exit 1; }
  if [ ! -e "$ROOT/.venv" ]; then
    echo "가상환경 만들기: $ROOT/.venv"
    "$PY" -m venv "$ROOT/.venv"
  fi
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip >/dev/null
  "$ROOT/.venv/bin/python" -m pip install -r "$APP/requirements.txt"
  if [ ! -f "$APP/.env" ]; then
    cp "$APP/.env.example" "$APP/.env" && chmod 600 "$APP/.env"
    echo "키 파일을 만들었습니다: src/waste_chat/.env — TIMELYGPT_API_KEY= 뒤에 키를 넣으세요(없으면 AI 없이 시험용 답으로 동작)."
  fi
  echo "준비 끝. 실행: ./waste-chat.sh"
  exit 0
fi

PY="$(find_python)" || { echo "Python 3.10 이상이 없습니다. 먼저 ./waste-chat.sh setup 을 실행하세요."; exit 1; }
if ! "$PY" -c 'import pypdf, reportlab, langgraph' 2>/dev/null; then
  echo "필요한 패키지가 없습니다. 먼저 ./waste-chat.sh setup 을 실행하세요."; exit 1
fi
cd "$APP" && exec "$PY" app.py "$@"
