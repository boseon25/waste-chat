"""판단 점검 — 사례 하나를 planner 부터 차례대로 (retriever → evidence → judge → verifier) 끝까지 돌린다.
graph.py 가 생기기 전 확인용. 경로 구성·업체 매칭은 하지 않는다.

  source env.sh   (그리고 TIMELY_API_KEY·TIMELY_BASE_URL 환경변수)
  PYTHONPATH=src python3 -m law_agent.eval.smoke_judge --case DEMO-PART2-01 [--dry] [--mode single|escalate|all]
                                                       [--json 결과.json] [사례.json]

  --dry   : LLM 을 부르지 않는다. 모든 모델이 "unknown" 이라고 답하는 가짜 → 흐름(차례·재검색·사람에게)만 확인. 비용 0
  --mode  : settings 의 judge_mode 대신 (single 이면 모델 1개)

출력: 차례별 요약(질문 수, 호출 수, 3번째 모델을 부른 질문 수, 상태) → 질문별 최종 판단 표 → 호출·토큰 합계.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from law_agent.state import AgentState, to_jsonable

DEFAULT = Path(os.environ.get("ROOT", ".")) / "src/route_agent/data/test_data/start/part1_sample.json"
from law_agent.agents.loop import apply  # noqa: F401  (예전 이름 — 테스트·다른 스크립트 호환)


def dry_llm(model, system, user, *, question_id, counter=None, schema=None):
    from law_agent.llm.client import JsonReply
    if counter is not None:
        counter.add()
    data = {"question_id": question_id, "answer": "unknown", "value": {}, "conditions": [], "quotes": [],
            "missing_facts": [], "reason": "dry run"}
    return JsonReply(data=data, raw=json.dumps(data), model=model, attempts=1, seconds=0.0)


def run_case(case: dict, mode: str | None = None, only: list[str] | None = None) -> tuple[AgentState, list[dict]]:
    """판단 루프 (agents/loop.run_law). only: 이 질문(과 하위 질문)만 — 비용을 줄인 부분 검증."""
    from law_agent.graph import run_law
    return run_law(case, mode, only)


def fmt_value(v) -> str:
    val = v.value or {}
    if val.get("items"):
        return ",".join(val["items"])[:40]
    if val.get("result"):
        return f"{val.get('result')} ({val.get('case_value')})"
    if val.get("text"):
        return val["text"][:40]
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT))
    ap.add_argument("--case", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--mode", choices=["single", "escalate", "all"])
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    case = next((c for c in json.loads(Path(a.path).read_text(encoding="utf-8"))["cases"]
                 if c.get("case_id") == a.case), None)
    if case is None:
        print(f"사례 없음: {a.case}")
        return 1
    from law_agent.input_check import check_input
    chk = check_input(case)
    for w in chk["warnings"]:
        print(f"경고: {w}")
    if chk["status"] != "ok":
        print(f"입력 검사 {chk['status']} — judge 를 부르지 않음:")
        for i in chk["issues"]:
            print(f"  {i['field']}: {i['text']}")
        return 0
    if a.dry:
        import os
        os.environ["LAW_AGENT_NO_CACHE"] = "1"   # 가짜 답을 캐시에 쓰지 않게 (2026.11.18 dry 답이 실제 실행에 쓰였음)
        from law_agent.llm import client
        client.chat_json = dry_llm
    t0 = time.time()
    st, rounds = run_case(case, a.mode)

    print(f"\n■ {case['case_id']} {case.get('name', '')}  {'(dry — LLM 안 부름)' if a.dry else ''}")
    print(f"{'차례':4} {'질문':>4} {'호출':>4} {'캐시':>4} {'3번째':>5} {'초':>6}  상태")
    for r in rounds:
        print(f"{r['round']:4} {r['ready']:>4} {r['calls']:>4} {r['cached']:>4} {r['escalated']:>5} {r['seconds']:>6}  "
              f"{dict(r['statuses'])}")
    print(f"\n{'질문':10} {'상태':13} {'답':11} {'값':40} 표")
    qorder = [q.id for q in st["questions"]]
    for qid in qorder:
        v = st["verdicts"].get(qid)
        if v is None:
            print(f"{qid:10} (판단 없음)")
            continue
        votes = " ".join(f"{x['model'].split('/')[-1][:10]}:{x['answer'] or '실패'}{'' if x['valid'] else '✗'}"
                         for x in v.votes)
        flags = f" [{','.join(v.flags)}]" if v.flags else ""
        print(f"{qid:10} {v.status:13} {v.answer:11} {fmt_value(v):40} {votes}{flags}")
    usage = Counter()
    for r in rounds:
        usage.update(r["usage"])
    statuses = Counter(v.status for v in st["verdicts"].values())
    total_calls = sum(r["calls"] for r in rounds)
    print(f"\n호출 {total_calls}회, 토큰 {dict(usage)}, {round(time.time() - t0, 1)}초")
    print(f"최종 상태: {dict(statuses)}")
    asked = sum(n for s, n in statuses.items() if s != "not_applicable")
    if asked:
        print(f"needs_human 비율: {statuses.get('needs_human', 0) / asked:.0%} (전환 기준 ≤ 15%)")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(to_jsonable({"rounds": rounds, "state": dict(st)}), ensure_ascii=False,
                                           indent=1), encoding="utf-8")
        print(f"자세한 결과(근거·모델 원답 포함): {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
