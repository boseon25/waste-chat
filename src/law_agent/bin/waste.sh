#!/usr/bin/env bash
# 사용자용: 파트 1 입력서 → 파트 2 결과
#   ./waste.sh new 입력서.docx     입력서 접수 (폐기물 목록)
#   ./waste.sh cases | use 번호     목록 / 작업할 폐기물 고르기
#   ./waste.sh next                 파트 1이 더 묻는 질문
#   ./waste.sh answer 질문ID "답"   답하기
#   ./waste.sh code 51-03-01 "근거" 분류번호 기록
#   ./waste.sh send [-v]            파트 2 판단까지 (결과 바로 표시)
#   ./waste.sh result               결과 다시 보기
set -e
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
. "$(dirname "$SELF")/_common.sh"
if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "help" ]; then sed -n '2,9p' "$SELF"; exit 0; fi
exec python3 -m law_agent.cli user "$@"
