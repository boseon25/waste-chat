"""근거 묶음 점검 (LLM 호출 없음) — 실제 원문으로 planner → retriever → evidence 1차 차례를 돌려 표로 보여준다.

  source env.sh
  PYTHONPATH=src python3 -m law_agent.eval.smoke_evidence [사례.json] [--case 사례id] [--json 저장경로]

기본 사례 파일: src/route_agent/data/test_data/start/part1_sample.json (데모 2건)
설계 문서 10절 순서 1의 완료 기준: "근거 묶음이 체크리스트 최소 조문을 모두 포함, 호 검증 통과"
  → 표의 '최소 조문' 칸이 a/b 에서 a == b, '불완전' 칸이 비어 있어야 함.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from law_agent.agents import evidence, planner, retriever
from law_agent.agents.schedule import ready_ids
from law_agent.state import initial_state, to_jsonable

DEFAULT = Path(os.environ.get("ROOT", ".")) / "src/route_agent/data/test_data/start/part1_sample.json"


def check_case(case: dict) -> list[dict]:
    st = initial_state(case)
    st.update(planner.node(st))
    r = retriever.node(st)
    st.update({k: v for k, v in r.items() if k in ("candidates",)})
    e = evidence.node(st)
    r_out, e_out = r["audit"][0]["output"], e["audit"][0]["output"]
    qmap = {q.id: q for q in st["questions"]}
    rows = []
    for qid in ready_ids(st):
        pack = e["packs"][qid]
        mins_want = len(qmap[qid].min_laws) - len(r_out[qid]["title_mismatch"]) - len(r_out[qid]["missing_min_laws"])
        mins = [c for c in st["candidates"].get(qid, []) if c.source == "min_laws"]
        got_items = [i for i in pack.items if i.source == "min_laws"]
        mins_got = sum(1 for c in mins if any(
            i.law == c.law and (c.key == "전체" or i.key == c.key or i.key.startswith(c.key + ":")) for i in got_items))
        rows.append({
            "qid": qid, "stage": e_out[qid]["stage"], "min_laws": f"{mins_got}/{mins_want}", "items": len(pack.items),
            "chars": e_out[qid]["chars"], "truncated": pack.truncated,
            "incomplete": e_out[qid]["incomplete"], "missing_min": r_out[qid]["missing_min_laws"],
            "moved": r_out[qid]["moved"], "title_mismatch": r_out[qid]["title_mismatch"],
            "notes": e_out[qid]["notes"], "keys": [f"{i.source[0]}:{i.law}:{i.key}" for i in pack.items],
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT))
    ap.add_argument("--case")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    cases = json.loads(Path(a.path).read_text(encoding="utf-8"))["cases"]
    if a.case:
        cases = [c for c in cases if c.get("case_id") == a.case]
    report = {}
    bad = 0
    for case in cases:
        rows = check_case(case)
        report[case["case_id"]] = rows
        print(f"\n■ {case['case_id']} {case.get('name', '')}  (1차 차례 질문 {len(rows)}개)")
        print(f"{'질문':10} {'단계':6} {'최소조문':8} {'근거':>4} {'글자':>6} 잘림  불완전·못찾음·이동·검증안됨")
        for r in rows:
            want_ok = r["min_laws"].split("/")[0] == r["min_laws"].split("/")[1]
            flag = "" if want_ok and not r["incomplete"] and not r["missing_min"] else "  ◀"
            bad += bool(flag)
            extra = "; ".join(filter(None, [
                f"불완전 {r['incomplete']}" if r["incomplete"] else "",
                f"못찾음 {r['missing_min']}" if r["missing_min"] else "",
                f"이동 {r['moved']}" if r["moved"] else "",
                f"검증안됨(검색어로) {len(r['title_mismatch'])}" if r["title_mismatch"] else ""]))
            print(f"{r['qid']:10} {r['stage']:6} {r['min_laws']:8} {r['items']:>4} {r['chars']:>6} {'예' if r['truncated'] else '  '}   {extra}{flag}")
        chars = [r["chars"] for r in rows]
        print(f"  글자 평균 {sum(chars) // max(1, len(chars))} / 합계 {sum(chars)}")
    print(f"\n확인할 질문 (◀): {bad}개")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(to_jsonable(report), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"자세한 결과: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
