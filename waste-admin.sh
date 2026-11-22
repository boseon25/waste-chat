#!/usr/bin/env bash
# 관리자용: 운영·점검
#   ./waste-admin.sh start [-v]      파트 2 감시 켜기 (inbox → outbox)
#   ./waste-admin.sh log             실시간 로그 (tail -f)
#   ./waste-admin.sh status          대기·완료·실패·결과 목록, 작업 중 사례
#   ./waste-admin.sh detail [--q B-1] 판단 과정·근거·모델 답
#   ./waste-admin.sh p1 part1.py status   파트 1 명령 그대로 (--db·--case 자동)
#   ./waste-admin.sh questions       질문 번호표 (A-1 … J-4 가 무슨 질문인지)
#   ./waste-admin.sh test | eval | models | cache
set -e
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
. "$(dirname "$SELF")/_common.sh"
if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "help" ]; then sed -n '2,9p' "$SELF"; exit 0; fi
exec python3 -m law_agent.cli admin "$@"
