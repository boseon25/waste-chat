"""graph.py (LangGraph) 가 loop.py 와 같은 결과를 내는지 — 가짜 저장소 + dry LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from law_agent import graph  # noqa: E402
from law_agent.agents import loop  # noqa: E402
from law_agent.llm import client  # noqa: E402

CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def dry(monkeypatch):
    from fake_law import build_store
    from law_agent.eval import smoke_judge
    from law_agent.tools import law_tools
    monkeypatch.setattr(client, "chat_json", smoke_judge.dry_llm)
    monkeypatch.setenv("LAW_AGENT_NO_CACHE", "1")
    law_tools.set_store(build_store())
    yield
    law_tools.set_store(None)


@pytest.mark.parametrize("i", range(len(CASES)))
def test_graph_equals_loop(dry, i):
    st_l, r_l = loop.run_law(CASES[i])
    seen = []
    st_g, r_g = graph.run_law_graph(CASES[i], progress=lambda r, s: seen.append(r["round"]))
    strip = lambda rs: [{k: v for k, v in r.items() if k != "seconds"} for r in rs]   # noqa: E731
    assert strip(r_g) == strip(r_l) and seen == [r["round"] for r in r_g]
    assert {k: (v.status, v.answer) for k, v in st_g["verdicts"].items()} == \
           {k: (v.status, v.answer) for k, v in st_l["verdicts"].items()}
    assert [q.id for q in st_g["questions"]] == [q.id for q in st_l["questions"]]


def test_mermaid_has_nodes():
    text = graph.mermaid()
    for n in ("planner", "table_answer", "retriever", "evidence", "judge", "verifier", "round_end"):
        assert n in text


def test_router_branches(monkeypatch):
    from law_agent.state import EvidenceItem, EvidencePack, Question
    qs = [Question(id="D-1", stage="D", text="R코드", answer_type="list"),
          Question(id="D-3", stage="D", text="기준", answer_type="text", attach=True),
          Question(id="B-1", stage="B", text="금지", answer_type="bool")]
    monkeypatch.setattr(graph, "_ready", lambda st: st["_q"])
    assert graph.route_round({"_q": qs}) == "판정표"
    assert graph.route_round({"_q": qs[1:]}) == "검색"
    assert graph.route_round({"_q": []}) == "정리"
    it = EvidenceItem(ref="r", law="WMA", key="1", title="t", effective="1", text="x")
    st = {"_q": qs[1:], "packs": {"D-3": EvidencePack(question_id="D-3", items=[it])}}
    assert graph.route_after_evidence(st) == "원문 첨부"
    assert graph.route_after_evidence({"_q": qs[2:], "packs": {}}) == "모델 판단"
    assert graph.route_after_attach({"_q": []}) == "정리"
