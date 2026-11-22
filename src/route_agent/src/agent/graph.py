"""업체 미확보 경로 대안 찾기 에이전트 (LangGraph).

  llm ──(도구 호출)──▶ tools ──▶ llm … ──(finish 또는 최대 횟수)──▶ 끝

- LLM: OpenAI 호환 주소(타임리 AI 등). 환경변수 TIMELY_API_KEY, TIMELY_BASE_URL, AGENT_MODEL
  (기본 anthropic/claude-sonnet-5.5). 함수 호출 강제를 받지 않는 모델이 있어 tool_choice는 auto.
- AGENT_MOCK=1 이면 LLM 대신 정해진 순서로 도구를 부른다 (테스트·시연용, 네트워크 없음).
- 결과는 경로 판정·업체 목록을 바꾸지 않고 route["alternatives"]에만 붙는다. status는 "candidate"(확인 전 후보), 실패하면 "failed".
- LangGraph가 설치되어 있지 않으면 같은 반복을 단순 루프로 돈다 (결과 같음).
"""
from __future__ import annotations

import json
import os
from typing import TypedDict

from . import tools as T

MAX_STEPS = 8
SYSTEM = """당신은 폐기물 처리 경로의 업체 매칭 보조 에이전트다.
규칙 판정으로 업체를 찾지 못한 경로 하나에 대해, 도구로 대안을 조사한다.
반드시 지킬 것:
1. 사실은 도구 결과에서만 가져온다. 업체명·경로·조문은 도구 결과에 나온 것만 쓴다.
2. 처리방법 코드는 allowed_methods 목록 안에서만 rematch 한다 (판정표가 법 기준).
3. 순서: route_info → allowed_methods → (rematch를 코드 묶음을 바꿔 1~3번) → other_routes → 필요하면 search_law → finish.
4. 대안 종류(kind): change_method(rematch로 업체를 찾은 경우), other_route(같은 폐기물의 다른 확보된 경로), need_info(배출업체·업체에 물어야 할 것).
5. 대안이 없으면 alternatives를 빈 배열로 finish. 모든 대안은 사람이 확인하기 전의 후보다.
6. reason은 한국어 한두 문장. 도구 호출 외의 긴 글은 쓰지 않는다."""


class State(TypedDict):
    messages: list
    steps: int
    result: dict | None
    log: list


# ── LLM ──────────────────────────────────────────────────────────────
_client = None


def _llm():
    global _client
    if _client is None:
        for k in ("TIMELY_API_KEY", "TIMELY_BASE_URL"):
            if not os.environ.get(k):
                raise RuntimeError(f"환경변수 {k} 없음")
        from openai import OpenAI
        _client = OpenAI(api_key=os.environ["TIMELY_API_KEY"], base_url=os.environ["TIMELY_BASE_URL"])
    return _client


def _mock_reply(state: State, ctx: T.Ctx) -> dict:
    """AGENT_MOCK=1: 정해진 순서로 도구를 부르는 가짜 LLM"""
    n = state["steps"]
    plan = [("route_info", {}), ("allowed_methods", {})]
    codes = sorted(T.allowed(ctx))
    cur = set(next((s.get("treatment_codes") or [] for s in ctx.route["steps"] if s["role"] != "transporter"), []))
    others = [c for c in codes if c not in cur]
    if others:
        plan.append(("rematch", {"treatment_codes": others}))
    plan.append(("other_routes", {}))
    if n < len(plan):
        name, args = plan[n]
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": f"m{n}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
    alts = []
    found = [p for p in ctx.seen_partners][:3]
    if found:
        alts.append({"kind": "change_method", "treatment_codes": others, "partners": found,
                     "reason": "판정표가 허용한 다른 처리방법으로 업체가 있다."})
    sec = next((r for i, r in enumerate(ctx.route_dicts) if i != ctx.index and (r.get("partners") or {}).get("secured")
                and r["gate"] == ctx.route["gate"]), None)
    if sec:
        alts.append({"kind": "other_route", "route_title": sec["title"], "reason": "같은 폐기물의 확보된 경로가 있다."})
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"m{n}", "type": "function", "function": {
                "name": "finish", "arguments": json.dumps({"summary": "mock", "alternatives": alts}, ensure_ascii=False)}}]}


def _ask_llm(state: State, ctx: T.Ctx) -> dict:
    if os.environ.get("AGENT_MOCK") == "1":
        return _mock_reply(state, ctx)
    resp = _llm().chat.completions.create(
        model=os.environ.get("AGENT_MODEL", "anthropic/claude-sonnet-5.5"), temperature=0, max_tokens=2000,
        tools=T.SPECS, tool_choice="auto", messages=state["messages"])
    m = resp.choices[0].message
    out = {"role": "assistant", "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [{"id": tc.id, "type": "function",
                              "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                             for tc in m.tool_calls]
    return out


# ── 노드 ─────────────────────────────────────────────────────────────
def _node_llm(ctx):
    def run(state: State) -> State:
        msg = _ask_llm(state, ctx)
        return {**state, "messages": state["messages"] + [msg], "steps": state["steps"] + 1}
    return run


def _node_tools(ctx):
    def run(state: State) -> State:
        msg = state["messages"][-1]
        new, result, log = [], state["result"], list(state["log"])
        for tc in msg.get("tool_calls") or []:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            if name == "finish":
                kept, dropped = T.verify(ctx, args.get("alternatives"))
                result = {"summary": args.get("summary"), "items": kept, "dropped": dropped}
                out = {"ok": True}
            else:
                out = T.call(ctx, name, args)
            log.append({"tool": name, "args": args, "result_keys": list(out)[:6] if isinstance(out, dict) else None})
            new.append({"role": "tool", "tool_call_id": tc["id"], "content": T.dumps(out)})
        return {**state, "messages": state["messages"] + new, "result": result, "log": log}
    return run


def _route_after_llm(state: State) -> str:
    last = state["messages"][-1]
    if last.get("tool_calls") and state["steps"] <= MAX_STEPS:
        return "tools"
    return "end"


def _route_after_tools(state: State) -> str:
    return "end" if state["result"] is not None or state["steps"] >= MAX_STEPS else "llm"


def build(ctx: T.Ctx):
    """LangGraph 그래프. 없으면 None."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None
    g = StateGraph(State)
    g.add_node("llm", _node_llm(ctx))
    g.add_node("tools", _node_tools(ctx))
    g.set_entry_point("llm")
    g.add_conditional_edges("llm", _route_after_llm, {"tools": "tools", "end": END})
    g.add_conditional_edges("tools", _route_after_tools, {"llm": "llm", "end": END})
    return g.compile()


def _loop(ctx: T.Ctx, state: State) -> State:
    llm, tl = _node_llm(ctx), _node_tools(ctx)
    while True:
        state = llm(state)
        if _route_after_llm(state) == "end":
            return state
        state = tl(state)
        if _route_after_tools(state) == "end":
            return state


def run(ctx: T.Ctx) -> dict:
    """경로 하나의 대안 조사. 실패해도 예외를 밖으로 내지 않는다 (pipeline 결과는 그대로)."""
    r = ctx.route
    user = (f"업체를 찾지 못한 경로: {r['title']} (관문 {r['gate']}, 분류번호 {', '.join(r.get('waste_codes') or [])}, "
            f"{r.get('waste_status')}). 대안을 조사하세요.")
    state: State = {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                    "steps": 0, "result": None, "log": []}
    engine = "langgraph"
    try:
        g = build(ctx)
        if g is None:
            engine = "loop"
            state = _loop(ctx, state)
        else:
            state = g.invoke(state, {"recursion_limit": MAX_STEPS * 2 + 4})
    except Exception as e:                                  # LLM·네트워크 실패 → 대안 없음으로
        return {"status": "failed", "error": str(e)[:300], "items": [], "engine": engine}
    res = state["result"] or {"summary": "최대 횟수 안에 끝내지 못함", "items": [], "dropped": []}
    return {"status": "candidate", "engine": engine, "steps": state["steps"],
            "summary": res.get("summary"), "items": res["items"], "dropped": res.get("dropped", []),
            "tools_used": [x["tool"] for x in state["log"]]}
