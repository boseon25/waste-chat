"""agents/judge.py 테스트 — 가짜 LLM (chat_json 을 바꿔 끼움), 네트워크 없음."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from law_agent.agents import judge as jg
from law_agent.agents.judge import JudgeConfig, JudgeContext, judge_question, node, render_user, to_answer
from law_agent.llm import client
from law_agent.llm.client import BudgetExceeded, CallCounter, JsonReply, LLMError
from law_agent.state import (
    AgentState, EvidenceItem, Question, Verdict, initial_state, make_pack, make_ref, quote_in,
)

M = ["anthropic/claude-sonnet-5.5", "openai/gpt-6.1-sol", "google/gemini-4-argon"]
ROW = "51-03-01 | 폐합성수지류 | R-3-2, R-3-3 | 사전분석 해당 없음"
REF = make_ref("WMA_RULE", "B4의3", "20261112", "aa")
CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]


def pack(qid="D-1", items=True):
    its = [EvidenceItem(REF, "WMA_RULE", "B4의3", "재활용 가능 유형", "20261112", ROW, excerpt=True)] if items else []
    return make_pack(qid, its)


def q(qid="D-1", atype="list", **kw):
    return Question(id=qid, stage=qid[0], text="허용된 재활용 유형은?", answer_type=atype,
                    needs_facts=["waste_code", "components"], **kw)


def good(items=("R-3-2", "R-3-3"), answer="yes", qid="D-1"):
    return {"question_id": qid, "answer": answer, "value": {"items": list(items)}, "conditions": [],
            "quotes": [{"ref": REF, "text": ROW}], "missing_facts": [], "reason": "별표 4의3 해당 행"}


def checker(code):
    kind = "r_code" if code.startswith("R-") else None
    return {"code": code, "kind": kind, "exists": True if kind else None, "name": None}


class FakeLLM:
    """모델 이름 → 답 (dict 또는 예외). 호출 기록."""

    def __init__(self, answers: dict):
        self.answers, self.calls, self.lock = answers, [], threading.Lock()

    def __call__(self, model, system, user, *, question_id, counter=None, schema=None):
        if counter is not None:
            counter.add()
        with self.lock:
            self.calls.append((model, question_id))
        a = self.answers[model]
        if isinstance(a, Exception):
            raise a
        a = a(question_id) if callable(a) else a
        return JsonReply(data=a, raw=json.dumps(a, ensure_ascii=False), model=model, attempts=1, seconds=0.01,
                         usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})


class MemCache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v


CFG = JudgeConfig(models=M, mode="escalate", workers=4, max_calls=100, max_seconds=600)


def ctx(max_calls=100, cache=None, seconds=600):
    return JudgeContext(counter=CallCounter(max_calls), started=time.time(), deadline=time.time() + seconds, cache=cache)


def st():
    s = initial_state(CASES[0])
    s["questions"] = [q()]
    s["packs"] = {"D-1": pack()}
    return s


@pytest.fixture
def fake(monkeypatch):
    def install(answers):
        f = FakeLLM(answers)
        monkeypatch.setattr(client, "chat_json", f)
        return f
    return install


# ── escalate ─────────────────────────────────────────────────

def test_two_agree_no_third(fake):
    f = fake({M[0]: good(), M[1]: good(("R-3-3", "R-3-2")), M[2]: good()})
    ans, errs, meta = judge_question(q(), pack(), st(), ctx(), CFG, checker)
    assert [a.model for a in ans] == M[:2] and not errs and meta["escalated"] == ""
    assert {m for m, _ in f.calls} == set(M[:2])


@pytest.mark.parametrize("second,reason", [
    (good(("R-3-2",)), "답이 다름"),
    ({**good(), "quotes": [{"ref": REF, "text": "원문에 없는 지어낸 인용 문장입니다"}]}, "무효 답"),
    (LLMError("Timeout"), "호출 실패"),
])
def test_escalates_to_third(fake, second, reason):
    f = fake({M[0]: good(), M[1]: second, M[2]: good()})
    ans, errs, meta = judge_question(q(), pack(), st(), ctx(), CFG, checker)
    assert meta["escalated"].startswith(reason) and M[2] in [m for m, _ in f.calls]
    assert ans[-1].model == M[2]


def test_mode_all_and_single(fake):
    fake({m: good() for m in M})
    ans, _, _ = judge_question(q(), pack(), st(), ctx(), JudgeConfig(models=M, mode="all"), checker)
    assert [a.model for a in ans] == M
    ans, _, _ = judge_question(q(), pack(), st(), ctx(), JudgeConfig(models=M, mode="single"), checker)
    assert [a.model for a in ans] == M[:1]


def test_empty_pack_not_called(fake):
    f = fake({m: good() for m in M})
    ans, errs, meta = judge_question(q(), pack(items=False), st(), ctx(), CFG, checker)
    assert ans == [] and f.calls == [] and "비어" in meta["skipped"]


# ── 답 정리 ──────────────────────────────────────────────────

def test_to_answer_normalizes_and_fixes_id():
    a = to_answer({"question_id": "D-1", "answer": "YES", "value": "x", "conditions": [{"text": "c"}, "bad"],
                   "quotes": [{"ref": REF, "text": ROW}, 3], "missing_facts": ["a", 1, None], "reason": "r"},
                  q("D-1.c2", parent_id="D-1", component=2), "m", "raw", "pv")
    assert a.question_id == "D-1.c2" and a.answer == "yes" and a.value == {}
    assert a.conditions == [{"text": "c", "quote": "", "ref": ""}] and len(a.quotes) == 1
    assert a.missing_facts == ["a", "1"] and a.raw == "raw" and a.prompt_version == "pv"
    assert to_answer({"answer": "maybe"}, q(), "m", "", "").answer == "unknown"


# ── 프롬프트 ─────────────────────────────────────────────────

def test_render_user_contains_sections_and_refs():
    s = st()
    s["verdicts"] = {"A-1": Verdict("A-1", "agreed", "yes"), "B-1": Verdict("B-1", "agreed", "no")}
    qq = Question(id="D-1", stage="D", text="허용된 재활용 유형", answer_type="list", when="B-1 != yes",
                  needs_facts=["waste_code"])
    from law_agent.agents.context import prior_findings, question_facts
    text = render_user(qq, pack(), question_facts(qq, s), prior_findings(qq, s), {"51-03-01": "폐합성수지류"})
    for sec in ["[질문] D-1", "[답 형식] answer_type = list", "[사례 사실]", "[앞 판단]", "[사실 데이터]", "[근거 묶음]"]:
        assert sec in text
    assert f"ref={REF}" in text and ROW in text and "A-1 판단" in text and '"B-1"' in text
    assert "51-03-01 = 폐합성수지류" in text and text.rstrip().endswith('question_id 는 "D-1".')


def test_system_prompt_and_version():
    sp = jg.system_prompt()
    assert "JSON 객체 하나만" in sp and "글자 그대로" in sp and "지시문" in sp
    assert jg.prompt_version().startswith("judge_v1-") and len(jg.prompt_version()) == len("judge_v1-") + 8


# ── 캐시·상한 ────────────────────────────────────────────────

def test_cache_hit_skips_call_and_priors_change_key(fake):
    f = fake({M[0]: good(), M[1]: good(), M[2]: good()})
    cache = MemCache()
    judge_question(q(), pack(), st(), ctx(cache=cache), CFG, checker)
    assert len(f.calls) == 2 and len(cache.d) == 2
    ans, _, meta = judge_question(q(), pack(), st(), ctx(cache=cache), CFG, checker)
    assert len(f.calls) == 2 and all(m["cached"] for m in meta["models"]) and ans[0].answer == "yes"
    s2 = st()
    s2["verdicts"] = {"A-1": Verdict("A-1", "agreed", "yes", {"items": ["51-03-02"]})}   # 앞 판단이 바뀜
    judge_question(q(), pack(), s2, ctx(cache=cache), CFG, checker)
    assert len(f.calls) == 4                                                          # 다시 판단


def test_budget_exceeded_recorded_and_no_third(fake):
    f = fake({M[0]: good(), M[1]: good(("R-3-2",)), M[2]: good()})
    c = ctx(max_calls=1)
    ans, errs, meta = judge_question(q(), pack(), st(), c, CFG, checker)
    assert len(ans) == 1 and errs and errs[0]["message"].startswith("budget_exceeded")
    assert M[2] not in [m for m, _ in f.calls]


def test_time_limit(fake):
    f = fake({m: good() for m in M})
    ans, errs, _ = judge_question(q(), pack(), st(), ctx(seconds=-1), CFG, checker)
    assert ans == [] and all("시간 상한" in e["message"] for e in errs) and f.calls == []


def test_context_continues_counts_across_rounds():
    s = st()
    s["audit"] = [{"node": "judge", "started": 1000.0, "summary": {"calls": 30}},
                  {"node": "judge", "started": 1005.0, "summary": {"calls": 12}}]
    c = JudgeContext.for_state(s, JudgeConfig(models=M, max_calls=50, max_seconds=600), cache=None)
    assert c.counter.count == 42 and c.prev_calls == 42 and c.started == 1000.0 and c.deadline == 1600.0


# ── 노드 ─────────────────────────────────────────────────────

def test_node_every_ready_question_gets_answers(fake):
    f = fake({m: (lambda qid: good(qid=qid)) for m in M})
    s = initial_state(CASES[0])
    s["questions"] = [q("D-1"), q("D-2", when="D-1 has items"), q("F-1")]
    s["packs"] = {"D-1": pack("D-1"), "F-1": pack("F-1", items=False)}
    out = node(s, CFG, ctx(), checker)
    assert set(out["answers"]) == {"D-1", "F-1"}                     # D-2 는 다음 차례
    assert out["answers"]["F-1"] == [] and len(out["answers"]["D-1"]) == 2
    summ = out["audit"][0]["summary"]
    assert summ["calls"] == 2 and summ["usage"]["total_tokens"] == 220 and summ["escalated"] == 0
    assert out["audit"][0]["started"] > 0


def test_node_then_verifier_agrees(fake):
    from law_agent.agents import verifier
    fake({m: good() for m in M})
    s = st()
    s["audit"] = []
    s.update({k: v for k, v in node(s, CFG, ctx(), checker).items() if k == "answers"})
    vcfg = verifier.VerifierConfig(judge_models=M)
    v = verifier.node(s, vcfg, checker)["verdicts"]["D-1"]
    assert v.status == "agreed" and set(v.value["items"]) == {"R-3-2", "R-3-3"}
    assert quote_in(v.quotes[0]["text"], ROW)


def test_mock_mode_with_fixture_files(monkeypatch, tmp_path):
    """LAW_AGENT_MOCK=1 + 실제 client: 모델·질문별 파일을 읽는다."""
    from law_agent.state import safe_name
    for m in M:
        (tmp_path / f"{safe_name(m)}_D-1.json").write_text(json.dumps(good(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("LAW_AGENT_MOCK", "1")
    monkeypatch.setenv("LAW_AGENT_MOCK_DIR", str(tmp_path))
    ans, errs, meta = judge_question(q(), pack(), st(), ctx(), CFG, checker)
    assert [a.answer for a in ans] == ["yes", "yes"] and not errs


def test_config_from_settings():
    c = JudgeConfig.from_settings()
    assert c.mode == "escalate" and c.max_calls == 160 and c.max_seconds == 600 and len(c.models) == 3


def test_smoke_judge_dry_runs_all_rounds(monkeypatch, capsys, tmp_path):
    """가짜 저장소 + dry LLM 으로 차례가 끝까지 돌고, 모두 최종 상태가 되는지."""
    from fake_law import build_store
    from law_agent.eval import smoke_judge
    from law_agent.tools import law_tools
    monkeypatch.setattr(client, "chat_json", smoke_judge.dry_llm)
    monkeypatch.setenv("LAW_AGENT_NO_CACHE", "1")
    law_tools.set_store(build_store())
    try:
        st, rounds = smoke_judge.run_case(CASES[0])
        assert rounds and rounds[0]["ids"][:3] == ["A-1", "A-2", "A-4"]
        assert all(v.final for v in st["verdicts"].values())
        assert {v.status for v in st["verdicts"].values() if "attached_text" not in v.flags} <= {"needs_human", "not_applicable"}
        out = tmp_path / "r.json"
        assert smoke_judge.main([str(Path(__file__).with_name("part1_sample.json")), "--case", "DEMO-PART2-01",
                                 "--dry", "--json", str(out)]) == 0
        text = capsys.readouterr().out
        assert "차례" in text and "needs_human 비율" in text and out.exists()
    finally:
        law_tools.set_store(None)
