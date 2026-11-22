#!/usr/bin/env bash
# 기존 src/폐기물법령api실시간갱신/data 를 <프로젝트>/data/{law,tables} 로 옮긴다. 여러 번 실행해도 안전.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
OLD="$ROOT/src/폐기물법령api실시간갱신"
mkdir -p "$ROOT/data/law" "$ROOT/data/tables" "$ROOT/data/nonlaw/raw" "$ROOT/src/비법령데이터갱신" "$ROOT/src/tests"
mv_if() { [ -e "$1" ] && { [ -e "$2/$(basename "$1")" ] && echo "건너뜀(이미 있음): $2/$(basename "$1")" || { mv "$1" "$2/"; echo "이동: $1 → $2/"; }; } || true; }
for x in raw chunks watched registry.json reports llm_cache; do mv_if "$OLD/data/$x" "$ROOT/data/law"; done
for x in staged active old; do mv_if "$OLD/data/$x" "$ROOT/data/tables"; done
mv_if "$OLD/build_partners.py" "$ROOT/src/비법령데이터갱신"
mv_if "$OLD/regress_tables.py" "$ROOT/src/tests"
mv_if "$OLD/tests/regress_cases.csv" "$ROOT/src/tests"
rmdir "$OLD/data" "$OLD/tests" 2>/dev/null || true
echo "완료. 남은 파일 확인: ls \"$OLD\""
