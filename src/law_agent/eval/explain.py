"""판단 설명 — 사례 하나의 질문 몇 개를 돌려, 질문마다 근거 목록·모델별 답·이유·버린 이유·최종 판단을 보여준다.
결과가 기대와 다를 때 원인을 찾는 용도. 캐시가 켜져 있으면(LAW_AGENT_DATA_DIR) 같은 판단은 다시 부르지 않는다.

  PYTHONPATH=src python3 -m law_agent.eval.explain PRE-03 --only A-1,A-2,B-1 [--path 사례.json] [--text]
  --text : 근거 원문 앞부분(300자)도 보여준다
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from law_agent.agents.verifier import VerifierConfig, _default_checker, drop_reason
from law_agent.eval import smoke_judge
from law_agent.eval.testset_eval import TESTSET


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--only", default="A-1,A-2,B-1")
    ap.add_argument("--path", default=str(TESTSET))
    ap.add_argument("--text", action="store_true")
    a = ap.parse_args(argv)
    case = next((c for c in json.loads(Path(a.path).read_text(encoding="utf-8"))["cases"]
                 if c["case_id"] == a.case_id), None)
    if case is None:
        print(f"사례 없음: {a.case_id}")
        return 1
    only = a.only.split(",")
    st, rounds = smoke_judge.run_case(case, None, only)
    print(f"■ {a.case_id} {case.get('name')}  (호출 {sum(r['calls'] for r in rounds)}회, "
          f"캐시 {sum(r['cached'] for r in rounds)})")
    qmap = {q.id: q for q in st["questions"]}
    for qid in [q.id for q in st["questions"]]:
        q, v, pack = qmap[qid], st["verdicts"].get(qid), st["packs"].get(qid)
        print(f"\n━━ {qid}  {q.text}")
        if pack is not None:
            print(f"  근거 {len(pack.items)}개{' (잘림)' if pack.truncated else ''}:")
            for it in pack.items:
                warn = f"  ⚠ {'; '.join(it.problems)}" if it.problems else ""
                print(f"    [{it.source}] {it.law}:{it.key} {it.title[:40]} ({len(it.text)}자){warn}")
                if a.text:
                    print("      " + it.text[:300].replace("\n", "\n      "))
        for ans in st["answers"].get(qid, []):
            why = drop_reason(ans, q, pack, _default_checker) if pack is not None else ""
            print(f"  ▷ {ans.model}: {ans.answer} {json.dumps(ans.value, ensure_ascii=False)}"
                  f"{'  ✗ 버림: ' + why if why else ''}")
            print(f"      이유: {ans.reason[:300]}")
            if ans.missing_facts:
                print(f"      빠진 사실: {ans.missing_facts}")
            for c in ans.conditions:
                print(f"      조건: {c.get('text', '')[:120]}")
            for x in ans.quotes[:2]:
                print(f"      인용 [{x.get('ref', '')[:40]}]: {x.get('text', '')[:120]}")
        errs = [e for e in st.get("errors") or [] if e.get("question_id") == qid]
        for e in errs[-3:]:
            print(f"  ✗ 오류 {e.get('node')} {e.get('model') or ''}: {e.get('message', '')[:100]}")
        if v is not None:
            print(f"  ⇒ 최종 {v.status} / {v.answer} {json.dumps(v.value, ensure_ascii=False)} "
                  f"재검색 {v.retries}회 {v.flags or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
