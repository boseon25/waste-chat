"""파일 하나를 파트 2로 판단 (사용자 화면의 '검토 요청'이 별도 프로세스로 부른다). 화면 출력 = 쉬운 말 진행 로그.

  python3 -m law_agent.bridge.run_one 파트1_내보내기.json --outbox data/bridge/outbox
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--outbox", required=True)
    a = ap.parse_args(argv)
    from law_agent.bridge.watch import process_file
    from law_agent.run import load_partners, use_dry_llm
    if os.environ.get("WASTE_DRY") == "1":
        use_dry_llm()
    if os.environ.get("WASTE_TEST_FAKE_STORE"):         # 시험용: 가짜 법령 저장소 (tests/fake_law.py)
        sys.path.insert(0, os.environ["WASTE_TEST_FAKE_STORE"])
        from fake_law import build_store
        from law_agent.tools import law_tools
        law_tools.set_store(build_store())
    out = Path(a.outbox)
    out.mkdir(parents=True, exist_ok=True)
    print("파트 2 준비: 업체 자료 불러오는 중…", flush=True)
    pd, fa = load_partners(os.environ.get("WASTE_NO_PARTNERS") == "1")
    try:
        process_file(Path(a.file), out, pd, fa, verbose=1)
    except Exception as e:                              # noqa: BLE001
        print(f"✘ 판단 실패: {type(e).__name__}: {e}", flush=True)
        return 1
    print("■ 완료", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
