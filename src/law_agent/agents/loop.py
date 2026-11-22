"""판단 루프 — planner 다음, 차례마다 retriever → evidence → judge → verifier 를 처리할 질문이 없을 때까지.
LangGraph(graph.py)가 생기기 전 같은 순서를 함수로 돌린다. 상태 반영 규칙은 AgentState reducer 와 같다.
"""
from __future__ import annotations

from typing import Any

import operator
import time

from law_agent.agents import attach, evidence, judge, planner, retriever, table_answer, verifier
from law_agent.agents.schedule import MAX_ROUNDS, ready_ids
from law_agent.state import AgentState, initial_state

MERGE = ("candidates", "packs", "answers", "verdicts", "retry_count")
APPEND = ("audit", "errors")


def apply(st: AgentState, upd: dict) -> None:
    """노드 결과를 상태에 반영 (LangGraph reducer 와 같은 규칙)."""
    for k, v in upd.items():
        if k in MERGE:
            st[k] = {**(st.get(k) or {}), **v}
        elif k in APPEND:
            st[k] = operator.add(st.get(k) or [], v)
        else:
            st[k] = v


def run_law(case: dict, mode: str | None = None, only: list[str] | None = None,
            jcfg: judge.JudgeConfig | None = None, vcfg: verifier.VerifierConfig | None = None,
            progress: Any = None) -> tuple[AgentState, list[dict]]:
    """파트 1 case → (판단이 끝난 상태, 차례별 요약).
    mode: judge_mode 바꾸기 (single 이면 verifier 도 1개로 판단). only: 이 질문(과 하위 질문)만."""
    jcfg = jcfg or judge.JudgeConfig.from_settings()
    vcfg = vcfg or verifier.VerifierConfig.from_settings()
    if mode:
        jcfg.mode = mode
        if mode == "single":
            vcfg.min_judges = 1
    st = initial_state(case)
    apply(st, planner.node(st))
    if only:
        st["questions"] = [q for q in st["questions"] if q.base_id in only]
    rounds: list[dict] = []
    for r in range(1, MAX_ROUNDS + 1):
        ready = ready_ids(st)
        if not ready:
            break
        t0 = time.time()
        apply(st, table_answer.node(st))                     # 법이 분류번호별 표인 질문은 판정표로 (모델 호출 없음)
        tbl = next(a for a in reversed(st["audit"]) if a["node"] == "table_answer")["summary"]["answered"]
        apply(st, retriever.node(st))
        apply(st, evidence.node(st))
        apply(st, attach.node(st))                           # 설명형 질문은 원문 첨부 (모델 호출 없음)
        apply(st, judge.node(st, jcfg))
        apply(st, verifier.node(st, vcfg))
        js = next(a for a in reversed(st["audit"]) if a["node"] == "judge")["summary"]
        vs = next(a for a in reversed(st["audit"]) if a["node"] == "verifier")["summary"]
        rounds.append({"round": r, "ready": len(ready), "table": len(tbl), "calls": js["calls"], "cached": js["cached"],
                       "escalated": js["escalated"], "usage": js["usage"], "statuses": vs,
                       "seconds": round(time.time() - t0, 1), "ids": ready})
        if progress is not None:
            progress(rounds[-1], st)
        if verifier.route_after_verify(st) == "composer":
            break
    return st, rounds
