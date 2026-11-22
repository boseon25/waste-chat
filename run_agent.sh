#!/usr/bin/env bash
# 경로 에이전트 한 번에 실행
#
#   ./run_agent.sh 파트1.json              입력 점검 → 경로 판정 → 결과 요약
#   ./run_agent.sh 파트1.json --agent      업체 못 찾은 경로에 대안까지 (LLM)
#   ./run_agent.sh 파트1.json --feedback 회신.json   업체 회신 반영
#   ./run_agent.sh                         샘플로 실행
#   ./run_agent.sh 파트1출력.json          파트 1 출력(schema 2.0)이면 자동으로 변환 후 실행
#      변환 옵션: CONVERT_OPTS="--assume-lot-monthly --density 1.0 --code 사례ID=분류번호" ./run_agent.sh 파트1출력.json
#
# 결과: src/route_agent/results/latest/  (파트 3 ← route_proposals.json, 파트 1 ← information_requests.json)
# 로그: 화면에 그대로 나오고 results/latest/run.log 에도 저장 (전체 기록 남기기: ./run_agent.sh 입력.json 2>&1 | tee 내로그.txt)
set -uo pipefail
set +x

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # waste_agent
source "$ROOT_DIR/env.sh"
VENV="${VENV:-$ROOT_DIR/../../.venv-ai-agent}"
[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"

IN="${1:-$ROOT/src/route_agent/data/test_data/start/part1_sample.json}"
[ $# -gt 0 ] && shift
IN="$(cd "$(dirname "$IN")" 2>/dev/null && pwd)/$(basename "$IN")"
if [ ! -f "$IN" ]; then echo "입력 파일이 없습니다: $IN"; exit 1; fi

# --agent 를 쓰면 .env 의 키를 불러온다 (값은 출력하지 않음)
if [[ " $* " == *" --agent "* ]] && [ -f "$ROOT/src/law_api/.env" ]; then
  set -a; source "$ROOT/src/law_api/.env"; set +a
fi
# --feedback 파일 경로도 절대 경로로
ARGS=()
while [ $# -gt 0 ]; do
  if [ "$1" = "--feedback" ] && [ $# -gt 1 ]; then
    ARGS+=("--feedback" "$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"); shift 2
  else
    ARGS+=("$1"); shift
  fi
done

cd "$ROOT/src/route_agent/src" || exit 1

# 파트 1 출력(WasteCase 2.0)이면 파트 2 입력(1.0)으로 변환
if python3 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); sys.exit(0 if ('case' in d or str(d.get('schema_version','')).startswith(('2','0.')) or any(str(c.get('schema_version','')).startswith('2') for c in d.get('cases',[]))) else 1)" "$IN" 2>/dev/null; then
  echo "== 0. 파트 1 출력 변환 (2.0 → 1.0) =="
  CONV="$(mktemp /tmp/part2_input_XXXX.json)"
  # shellcheck disable=SC2086
  python3 convert_part1.py "$IN" --out "$CONV" ${CONVERT_OPTS:-} || exit 1
  IN="$CONV"
  echo
fi

echo "== 1. 입력 점검 =="
python3 check_input.py "$IN" || { echo "필수 칸이 빠져 있어 멈춥니다. 파트 1에 다시 요청하세요."; exit 1; }

echo; echo "== 2. 경로 판정 =="
LOG="$(mktemp /tmp/route_agent_XXXX.log)"
PYTHONPATH=.:pipline python3 -u pipline/pipeline.py "$IN" "${ARGS[@]}" 2>&1 | tee "$LOG"     # 화면에 그대로 보이고 파일에도 저장
code=${PIPESTATUS[0]}
if [ $code -ne 0 ]; then echo "실패 (로그: $LOG)"; exit $code; fi
cp "$LOG" "$ROOT/src/route_agent/results/latest/run.log" 2>/dev/null && rm -f "$LOG"

echo; echo "== 3. 결과 =="
python3 show_result.py

OUT="$(readlink -f "$ROOT/src/route_agent/results/latest")"
echo
echo "보낼 파일"
echo "  파트 3 ← $OUT/route_proposals.json"
echo "  파트 1 ← $OUT/information_requests.json"
echo "  로그   : $OUT/run.log"
