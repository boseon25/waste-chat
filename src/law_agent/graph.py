"""법률 판단 루프를 LangGraph StateGraph 로 (agents/loop.py 와 같은 결과).

    START → planner ─(물을 질문 없음)→ END
              └→ round_start ─[router: 질문 종류·상태로 갈래]
                    ├ 판정표 질문 있음 → table_answer ─(남은 질문)→ retriever
                    ├ 검색할 질문(첫 검색·재검색) → retriever → evidence
                    │      evidence ├ 설명형 → attach ─(판단 남음)→ judge
                    │               └ 판단 필요 → judge → verifier
                    └ 할 일 없음 → verifier            (verifier 는 차례마다: 막힌 질문 정리·차례 세기)
              verifier → round_end ─(남은 질문·재검색, 차례 < MAX_ROUNDS)→ round_start
                                    └ 그 밖 → END  (그다음 composer·업체 매칭은 run.py)

상태: AgentState (state.py) + 차례 칸 3개. 상태 합치는 규칙(reducer)은 AgentState 의 Annotated 그대로
      (candidates·packs·answers·verdicts·retry_count = dict 합치기, audit·errors = 덧붙이기).
엔진 고르기: settings.yaml llm.law_engine = langgraph | loop (기본 langgraph). langgraph 가 없으면 loop.

  PYTHONPATH=src python3 -m law_agent.graph --mermaid            # 구조도(mermaid) 출력 → 발표 그림
  PYTHONPATH=src python3 -m law_agent.graph --mermaid --out graph.md
"""
from __future__ import annotations

import argparse
import operator
import time
from typing import Annotated, Any

from law_agent.agents import attach, evidence, judge, planner, retriever, table_answer, verifier
from law_agent.agents.schedule import MAX_ROUNDS, ready_ids, ready_questions
from law_agent.state import AgentState, initial_state


class LawGraphState(AgentState, total=False):
    round: int                                        # 지금 몇 번째 차례
    round_ids: list[str]                              # 이번 차례에 처리하는 질문 id
    round_t0: float
    round_audit_from: int                             # 이번 차례 audit 시작 위치 (건너뛴 노드는 기록 없음)
    rounds: Annotated[list[dict], operator.add]      # 차례별 요약 (loop.run_law 의 rounds 와 같은 모양)


# ── 갈래 고르기 (router) ─ 질문의 종류·상태를 보고 다음 노드를 정한다 ──────────────
# 판정표 질문(D-1 등)        → table_answer
# 판단·설명 질문, 재검색     → retriever → evidence
#   설명형(attach)           → attach
#   판단이 필요한 질문       → judge → verifier
# 할 일이 없는 노드는 건너뛴다. verifier 는 차례마다 꼭 지난다 (앞 판단이 막힌 질문 정리·차례 세기).

def _ready(st) -> list:
    return ready_questions(st)


def route_round(st) -> str:
    qs = _ready(st)
    if any(q.base_id in table_answer.TABLE_QUESTIONS for q in qs):
        return "판정표"
    return "검색" if qs else "정리"


def route_after_table(st) -> str:
    return "검색" if _ready(st) else "정리"


def route_after_evidence(st) -> str:
    qs, packs = _ready(st), st.get("packs") or {}
    if any(q.attach and packs.get(q.id) is not None and packs[q.id].items for q in qs):
        return "원문 첨부"
    return "모델 판단" if qs else "정리"


def route_after_attach(st) -> str:
    return "모델 판단" if _ready(st) else "정리"


def _round_audit(st) -> list[dict]:
    return (st.get("audit") or [])[st.get("round_audit_from") or 0:]


def _last(audits: list[dict], node: str) -> dict:
    return next((a["summary"] for a in reversed(audits) if a["node"] == node), {})


NODE_SUMMARY_KEYS = {
    "retriever": ("questions", "empty"), "table_answer": ("answered",), "attach": ("attached",),
    "judge": ("questions", "calls", "cached", "escalated"),
}


def _trace_line(name: str, st: dict, upd: dict, secs: float) -> str:
    if name == "round_start":
        return f"  ▶ 차례 {upd.get('round')}: 질문 {len(upd.get('round_ids') or [])}개 {', '.join((upd.get('round_ids') or [])[:8])}"
    au = (upd.get("audit") or [{}])[-1].get("summary") or {}
    if name == "planner":
        qs = upd.get("questions") or st.get("questions") or []
        skipped = sum(1 for v in (upd.get("verdicts") or {}).values() if v.status == "not_applicable")
        return f"  · planner: 질문 {len(qs)}개 (묻지 않음 {skipped})"
    if name == "verifier":
        txt = ", ".join(f"{k} {v}" for k, v in au.items()) if isinstance(au, dict) else str(au)
    else:
        keys = NODE_SUMMARY_KEYS.get(name)
        txt = ", ".join(f"{k} {au.get(k)}" for k in keys if k in au) if keys else ""
    return f"    · {name:<12} {secs:5.1f}초  {txt}"


def build_graph(jcfg: judge.JudgeConfig | None = None, vcfg: verifier.VerifierConfig | None = None,
                only: list[str] | None = None, progress: Any = None, trace: Any = None):
    """컴파일한 그래프. jcfg·vcfg 가 None 이면 settings 에서.
    trace(줄) 를 주면 노드가 끝날 때마다 한 줄씩 넘긴다 (실시간 로그, run.py -v · bridge.watch -v)."""
    from langgraph.graph import END, START, StateGraph

    jcfg = jcfg or judge.JudgeConfig.from_settings()
    vcfg = vcfg or verifier.VerifierConfig.from_settings()

    def n_planner(st):
        upd = planner.node(st)
        if only:
            upd["questions"] = [q for q in upd.get("questions") or st.get("questions") or [] if q.base_id in only]
        return upd

    def n_round_start(st):
        return {"round": (st.get("round") or 0) + 1, "round_ids": ready_ids(st), "round_t0": time.time(),
                "round_audit_from": len(st.get("audit") or [])}

    def n_round_end(st):
        au = _round_audit(st)
        js, vs, ts = _last(au, "judge"), _last(au, "verifier"), _last(au, "table_answer")
        summary = {"round": st["round"], "ready": len(st["round_ids"]), "table": len(ts.get("answered") or []),
                   "calls": js.get("calls", 0), "cached": js.get("cached", 0), "escalated": js.get("escalated", 0),
                   "usage": js.get("usage", {}), "statuses": vs, "seconds": round(time.time() - st["round_t0"], 1),
                   "ids": st["round_ids"]}
        if progress is not None:
            progress(summary, st)
        return {"rounds": [summary]}

    def after_planner(st):
        return "차례 시작" if ready_ids(st) else "끝"

    def after_round(st):
        if verifier.route_after_verify(st) == "composer" or (st.get("round") or 0) >= MAX_ROUNDS:
            return "끝"
        return "다음 차례 (남은 질문·재검색)"

    def traced(name, fn):
        if trace is None:
            return fn

        def run(st):
            t0 = time.time()
            upd = fn(st)
            try:
                if getattr(trace, "friendly", False):          # -v: 사람이 읽는 말
                    from law_agent.agents import narrate
                    view = {**st, **{k: v for k, v in upd.items() if k == "questions"}}
                    txt = narrate.line(name, view, upd)
                    if txt:
                        trace(txt)
                else:                                          # -vv: 노드·시간·호출 수
                    trace(_trace_line(name, st, upd, time.time() - t0))
            except Exception:                          # noqa: BLE001 — 로그 때문에 판단이 멈추지 않게
                pass
            return upd
        return run

    g = StateGraph(LawGraphState)
    for name, fn in [("planner", n_planner), ("round_start", n_round_start), ("table_answer", table_answer.node),
                     ("retriever", retriever.node), ("evidence", evidence.node), ("attach", attach.node),
                     ("judge", lambda st: judge.node(st, jcfg)), ("verifier", lambda st: verifier.node(st, vcfg)),
                     ("round_end", n_round_end)]:
        g.add_node(name, traced(name, fn) if name not in ("round_end", "evidence") else fn)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", after_planner, {"차례 시작": "round_start", "끝": END})
    g.add_conditional_edges("round_start", route_round,
                            {"판정표": "table_answer", "검색": "retriever", "정리": "verifier"})
    g.add_conditional_edges("table_answer", route_after_table, {"검색": "retriever", "정리": "verifier"})
    g.add_edge("retriever", "evidence")
    g.add_conditional_edges("evidence", route_after_evidence,
                            {"원문 첨부": "attach", "모델 판단": "judge", "정리": "verifier"})
    g.add_conditional_edges("attach", route_after_attach, {"모델 판단": "judge", "정리": "verifier"})
    g.add_edge("judge", "verifier")
    g.add_edge("verifier", "round_end")
    g.add_conditional_edges("round_end", after_round,
                            {"다음 차례 (남은 질문·재검색)": "round_start", "끝": END})
    return g.compile()


def run_law_graph(case: dict, mode: str | None = None, only: list[str] | None = None,
                  jcfg: judge.JudgeConfig | None = None, vcfg: verifier.VerifierConfig | None = None,
                  progress: Any = None, trace: Any = None) -> tuple[AgentState, list[dict]]:
    """loop.run_law 와 같은 입력·출력."""
    jcfg = jcfg or judge.JudgeConfig.from_settings()
    vcfg = vcfg or verifier.VerifierConfig.from_settings()
    if mode:
        jcfg.mode = mode
        if mode == "single":
            vcfg.min_judges = 1
    app = build_graph(jcfg, vcfg, only, progress, trace)
    st0 = {**initial_state(case), "round": 0, "round_ids": [], "round_t0": 0.0, "round_audit_from": 0, "rounds": []}
    out = app.invoke(st0, {"recursion_limit": 12 * MAX_ROUNDS + 10})
    rounds = out.get("rounds") or []
    st = {k: v for k, v in out.items() if k not in ("round", "round_ids", "round_t0", "round_audit_from", "rounds")}
    return st, rounds


def engine() -> str:
    try:
        from law_agent.settings import load_settings
        e = (load_settings().get("llm") or {}).get("law_engine") or "langgraph"
    except Exception:                                  # noqa: BLE001
        e = "langgraph"
    if e == "langgraph":
        try:
            import langgraph  # noqa: F401
        except ImportError:
            return "loop"
    return e


def run_law(case: dict, mode: str | None = None, only: list[str] | None = None, **kw) -> tuple[AgentState, list[dict]]:
    """run.py·eval 이 부르는 입구. settings llm.law_engine 으로 엔진을 고른다."""
    if engine() == "langgraph":
        return run_law_graph(case, mode, only, **kw)
    from law_agent.agents.loop import run_law as loop_run
    kw.pop("trace", None)
    return loop_run(case, mode, only, **kw)


def mermaid() -> str:
    jc = judge.JudgeConfig.from_settings()
    vc = verifier.VerifierConfig.from_settings()
    return build_graph(jc, vc).get_graph().draw_mermaid()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mermaid", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.mermaid:
        text = "```mermaid\n" + mermaid() + "\n```\n"
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text)
            print(f"저장: {a.out}")
        else:
            print(text)
    else:
        print(f"엔진: {engine()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
