#!/usr/bin/env bash
# waste_agent 폴더에 waste.sh · waste-admin.sh 를 깐다:  bash src/law_agent/bin/install.sh
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
cp "$HERE/_common.sh" "$HERE/waste.sh" "$HERE/waste-admin.sh" "$HERE/waste.bat" "$HERE/waste-admin.bat" "$ROOT/"
chmod +x "$ROOT/waste.sh" "$ROOT/waste-admin.sh"
echo "설치: $ROOT/waste.sh, $ROOT/waste-admin.sh"
