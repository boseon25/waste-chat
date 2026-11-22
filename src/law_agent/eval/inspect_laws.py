"""법령 저장소 점검 (LLM 안 씀).

  PYTHONPATH=src python3 -m law_agent.eval.inspect_laws                      # 체크리스트 min_laws 검증 결과 전부
  PYTHONPATH=src python3 -m law_agent.eval.inspect_laws --outline WMA_RULE B5  # 긴 별표의 목차(호·목)와 조각 목록
  PYTHONPATH=src python3 -m law_agent.eval.inspect_laws --find CEA 순환자원     # 제목·첫 줄에 말이 든 조문

1) min_laws 검증: 질문마다 체크리스트의 (법, 번호, 제목)이 저장소와 맞는지.
   OK      번호·제목 일치 → 근거에 강제로 넣음
   MOVED   번호는 다르지만 제목으로 하나 찾음 → 그 조문을 넣음
   MISMATCH 번호의 조문 제목이 다름 → 강제하지 않고 제목을 검색어로만 씀 (근거에서 빠질 수 있음!)
   NONE    번호·제목 모두 못 찾음
2) --outline: 별표 길이, "1." "가." 수준 머리 줄과 글자 위치, 저장소 조각(키·길이·첫 줄).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from law_agent.state import load_checklist
from law_agent.tools import law_tools

CHECKLIST = Path(__file__).resolve().parents[1] / "config" / "checklist.yaml"
RX_HEAD = re.compile(r"^\s*(\d{1,2}\.|[가-하]\.)\s*\S")


def check_min_laws() -> int:
    s = law_tools.get_store()
    bad = 0
    for q in load_checklist(CHECKLIST):
        for m in q.min_laws:
            r = law_tools.find_by_title(m.law, m.no, m.title, store=s)
            by_no = None
            for k in law_tools._key_candidates(m.no):
                c = s.get(m.law, k)
                if c is not None:
                    by_no = c
                    break
            if r is None:
                st = "NONE"
            elif not r["title_match"]:
                st = "MISMATCH"
            elif r["moved"]:
                st = "MOVED"
            else:
                st = "OK"
            bad += st in ("NONE", "MISMATCH")
            have = f" | 저장소 {m.no}: {by_no.title!r}" if by_no is not None and st != "OK" else ""
            moved = f" → {r['key']} {r['title']!r}" if r and st == "MOVED" else ""
            print(f"{st:8} {q.id:5} {m.law}:{m.no or '(없음)'} {m.title!r}{moved}{have}")
    print(f"\n문제 {bad}개 (MISMATCH·NONE)")
    return 0


def outline(law: str, key: str, max_lines: int = 120) -> int:
    s = law_tools.get_store()
    c = s.get(law, key)
    if c is None:
        print(f"{law}:{key} 없음")
        return 1
    text = c.text
    print(f"■ {law}:{key} {c.title!r}  {len(text)}자  (긴 별표: {law_tools.is_long_annex(law, key, store=s)})")
    pos, n = 0, 0
    for line in text.split("\n"):
        if RX_HEAD.match(line) and n < max_lines:
            print(f"   @{pos:>6}  {line.strip()[:70]}")
            n += 1
        pos += len(line) + 1
    kids = sorted(s.children(law, key), key=lambda p: p.key)
    print(f"\n   조각 {len(kids)}개")
    for p in kids[:80]:
        first = next((ln.strip() for ln in p.text.split("\n") if ln.strip()), "")
        print(f"   {p.key:16} {len(p.text):>6}자  {first[:60]}")
    return 0


def find(law: str, word: str) -> int:
    """제목이나 첫 줄에 word 가 든 조문·별표 (체크리스트 번호가 틀렸을 때 맞는 번호 찾기)."""
    s = law_tools.get_store()
    w = re.sub(r"\s+", "", word)
    for (l, k), c in sorted(s.chunks.items()):
        if l == law and law_tools._is_top(k) and (w in re.sub(r"\s+", "", c.title or "")
                                                 or w in re.sub(r"\s+", "", c.text[:200])):
            print(f"   {l}:{k:10} {c.title!r}  {len(c.text)}자")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outline", nargs=2, metavar=("LAW", "KEY"))
    ap.add_argument("--find", nargs=2, metavar=("LAW", "WORD"))
    a = ap.parse_args(argv)
    if a.find:
        return find(*a.find)
    return outline(*a.outline) if a.outline else check_min_laws()


if __name__ == "__main__":
    sys.exit(main())
