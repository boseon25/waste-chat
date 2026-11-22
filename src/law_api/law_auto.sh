#!/usr/bin/env bash
# 법령 자동 갱신 한 번에 관리
#
#   src/law_api/law_auto.sh install     설치: .env 확인·권한 600 → 시험 실행 → 매주 월 07:13 cron 등록 (여러 번 해도 한 줄만)
#   src/law_api/law_auto.sh run         지금 바로 한 번 실행 (cron 과 같은 방식)
#   src/law_api/law_auto.sh status      마지막 확인일, 반영 대기, cron 등록 여부, 최근 로그
#   src/law_api/law_auto.sh applied     promote 한 뒤 반영 대기 지우기
#   src/law_api/law_auto.sh uninstall   cron 에서 빼기 (파일은 그대로)
#
# 키: src/law_api/.env 의 LAW_OC=아이디 (권한 600). 이 스크립트는 키 값을 출력하지 않는다.
set -uo pipefail
set +x

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # src/law_api
ROOT_DIR="$(cd "$HERE/../.." && pwd)"                     # waste_agent
ENV_FILE="$HERE/.env"
RUNNER="$HERE/run_update.sh"
CRON_TIME="${CRON_TIME:-13 7 * * 1}"                      # 매주 월요일 07:13 (바꾸려면 CRON_TIME="분 시 * * 요일")
CRON_LINE="$CRON_TIME $RUNNER"
TAG="# waste_agent law_auto"

source "$ROOT_DIR/env.sh"
VENV="${VENV:-$ROOT/../../.venv-ai-agent}"
[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"
LOGDIR="$(dirname "${WASTE_LAW_DIR:-$ROOT/data/law/chunk_v2}")/reports"

ok()   { echo "  ✅ $*"; }
bad()  { echo "  ❌ $*"; }
info() { echo "  · $*"; }

check_env() {
  echo "[키 파일] $ENV_FILE"
  if [ ! -f "$ENV_FILE" ]; then
    bad "없음. 아래로 만드세요 (값만 바꿔서):"
    echo "      nano $ENV_FILE      # 내용: LAW_OC=아이디"
    return 1
  fi
  local perm; perm="$(stat -c %a "$ENV_FILE")"
  if [ "$perm" != "600" ]; then chmod 600 "$ENV_FILE"; ok "권한 $perm → 600 으로 바꿈"; else ok "권한 600"; fi
  if grep -qE '^[[:space:]]*(export[[:space:]]+)?LAW_OC=[^[:space:]]' "$ENV_FILE"; then
    local n; n="$(grep -m1 -E '^[[:space:]]*(export[[:space:]]+)?LAW_OC=' "$ENV_FILE" | sed -E 's/^[^=]*=//; s/["'"'"'[:space:]]//g' | tr -d '\n' | wc -c)"
    ok "LAW_OC 있음 (${n}글자)"
    if grep -qE 'LAW_OC=.*(chmod|grep|rm |echo|https?://|OC=)' "$ENV_FILE"; then
      bad "LAW_OC 값에 명령어나 주소가 섞여 있습니다. 아이디만 남기세요"; return 1
    fi
  else
    bad "LAW_OC= 줄이 없거나 값이 비어 있음"; return 1
  fi
  local gi="$HERE/gitignore"
  if [ -f "$gi" ] && ! grep -qx '.env' "$gi"; then echo ".env" >> "$gi"; ok "gitignore 에 .env 추가"; fi
  return 0
}

cron_has() { crontab -l 2>/dev/null | grep -qF "$RUNNER"; }

cmd_run() {
  echo "[실행] $RUNNER"
  "$RUNNER"; local code=$?
  case $code in
    0)  ok "변경 없음 (종료 코드 0)";;
    10) info "법령 개정 있음 → staged 준비됨, 사람 검토 필요 (종료 코드 10)"
        info "보고서: $LOGDIR/$(date +%F)_update.md";;
    *)  bad "실패 (종료 코드 $code). 로그: tail -30 $LOGDIR/cron.log";;
  esac
  local fails; fails="$(grep -c '❌' "$LOGDIR/$(date +%F)_check.md" 2>/dev/null)"; fails="${fails:-0}"
  if [ "${fails:-0}" != "0" ]; then bad "조회 실패 법령 ${fails}개 (22개 전부면 키가 틀림): grep ❌ $LOGDIR/$(date +%F)_check.md"; fi
  return $code
}

cmd_install() {
  echo "== 법령 자동 갱신 설치 =="
  chmod +x "$RUNNER" "$HERE/law_auto.sh" 2>/dev/null
  check_env || { echo "설치 중단: 키 파일부터 고치세요"; return 1; }
  cmd_run; local code=$?
  if [ $code -ne 0 ] && [ $code -ne 10 ]; then echo "설치 중단: 시험 실행 실패 (cron 등록 안 함)"; return 1; fi
  echo "[cron]"
  if cron_has; then
    ok "이미 등록됨: $(crontab -l | grep -F "$RUNNER")"
  else
    (crontab -l 2>/dev/null; echo "$CRON_LINE $TAG") | crontab - && ok "등록: $CRON_LINE"
  fi
  echo "== 완료. 매주 확인: $HERE/law_auto.sh status =="
}

cmd_status() {
  echo "== 법령 자동 갱신 상태 =="
  check_env >/dev/null && ok "키 파일 정상" || bad "키 파일 문제: $HERE/law_auto.sh install 로 확인"
  if cron_has; then ok "cron: $(crontab -l | grep -F "$RUNNER" | sed "s| $TAG||")"; else bad "cron 등록 안 됨 ($HERE/law_auto.sh install)"; fi
  echo "[update 상태]"
  (cd "$HERE" && python3 update.py status) | sed 's/^/  /'
  echo "[최근 실행]"
  grep -E '^=====' "$LOGDIR/cron.log" 2>/dev/null | tail -4 | sed 's/^/  /' || info "기록 없음"
}

cmd_uninstall() {
  if cron_has; then
    { crontab -l 2>/dev/null | grep -vF "$RUNNER" || true; } | crontab - && ok "cron 에서 뺐습니다 (파일·.env 는 그대로)"
  else
    info "등록된 cron 없음"
  fi
}

case "${1:-}" in
  install)   cmd_install;;
  run)       cmd_run;;
  status)    cmd_status;;
  applied)   (cd "$HERE" && python3 update.py applied);;
  uninstall) cmd_uninstall;;
  *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2;;
esac
