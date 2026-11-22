#!/usr/bin/env bash
# 주 1회 자동 실행용 (cron). 법령 개정 확인 → staged 까지. promote 는 사람이.
#   키 파일: src/law_api/.env (LAW_OC=아이디) 또는 ~/.config/waste_agent/law_oc (아이디만), chmod 600
#   이 스크립트·env.sh·cron 에 키를 쓰지 않는다. .env 는 zip·공유·git 에서 빼기
#   가상환경: VENV (기본: 프로젝트 두 단계 위의 .venv-ai-agent)
#   로그: data/law/reports/cron.log, 결과: data/law/reports/{날짜}_update.md, data/law/update_status.json
set -euo pipefail
set +x                                                    # 명령이 화면·로그에 찍히지 않게 (키 보호)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # src/law_api
ROOT_DIR="$(cd "$HERE/../.." && pwd)"                     # waste_agent
source "$ROOT_DIR/env.sh"

# 키 파일 찾는 순서: LAW_OC_FILE → src/law_api/.env → ~/.config/waste_agent/law_oc
#   .env 형식: LAW_OC=아이디   (한 줄, 따옴표 있어도 됨. source 하지 않고 이 줄만 읽음)
#   law_oc 형식: 아이디만 한 줄
KEYFILE="${LAW_OC_FILE:-}"
[ -z "$KEYFILE" ] && [ -f "$HERE/.env" ] && KEYFILE="$HERE/.env"
[ -z "$KEYFILE" ] && KEYFILE="$HOME/.config/waste_agent/law_oc"
if [ ! -f "$KEYFILE" ]; then echo "키 파일 없음: $KEYFILE" >&2; exit 1; fi
if [ "$(stat -c %a "$KEYFILE")" != "600" ]; then echo "키 파일 권한이 600이 아닙니다: chmod 600 $KEYFILE" >&2; exit 1; fi
if grep -q '^[[:space:]]*\(export[[:space:]]\+\)\?LAW_OC=' "$KEYFILE"; then
  LAW_OC="$(grep -m1 '^[[:space:]]*\(export[[:space:]]\+\)\?LAW_OC=' "$KEYFILE" | sed -E 's/^[[:space:]]*(export[[:space:]]+)?LAW_OC=//; s/^["'"'"']//; s/["'"'"'][[:space:]]*$//' | tr -d '[:space:]')"
else
  LAW_OC="$(head -n1 "$KEYFILE" | tr -d '[:space:]')"
fi
if [ -z "$LAW_OC" ]; then echo "키 파일에 LAW_OC 값이 없습니다: $KEYFILE" >&2; exit 1; fi
export LAW_OC

VENV="${VENV:-$ROOT/../../.venv-ai-agent}"
if [ -f "$VENV/bin/activate" ]; then source "$VENV/bin/activate"; fi

LOGDIR="$(dirname "${WASTE_LAW_DIR:-$ROOT/data/law/chunk_v2}")/reports"
mkdir -p "$LOGDIR"
cd "$HERE"
set +e
{ echo "===== $(date '+%F %T') update 시작"; python3 update.py "$@"; code=$?; echo "===== $(date '+%F %T') 종료 코드 $code"; } >> "$LOGDIR/cron.log" 2>&1
unset LAW_OC
exit $code
