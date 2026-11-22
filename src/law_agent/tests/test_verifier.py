"""agents/verifier.py 테스트 — LLM·서버 데이터 없이 (가짜 코드 검사기)."""
from __future__ import annotations

from pathlib import Path

import pytest

from law_agent.agents import schedule, verifier as vf
from law_agent.agents.verifier import VerifierConfig, decide, drop_reason, node, route_after_verify
from law_agent.state import (
    AgentState, EvidenceItem, JudgeAnswer, Question, Verdict, error_record, load_checklist, make_pack, make_ref,
)

M = ["anthropic/claude-sonnet-5.5", "openai/gpt-6.1-sol", "google/gemini-4-argon"]
CFG = VerifierConfig(judge_models=M, min_judges=2, agree_ratio=0.67, re_retrieve_max=2, safety_veto=True)

ROW = "51-03-01 | 폐합성수지류(고상) | R-3-2, R-3-3, R-9-1 | 사전분석 해당 없음"
ART = "제3조(에너지 회수기준 등) ① 저위발열량이 킬로그램당 3천킬로칼로리 이상일 것 ② 에너지 회수효율이 75퍼센트 이상일 것"
REF_ROW = make_ref("WMA_RULE", "B4의3", "20261112", "aa")
REF_ART = make_ref("WMA_RULE", "3", "20261112", "bb")

KNOWN = {"R-3-2", "R-3-3", "R-9-1", "R-1", "51-03-01", "2003"}


def checker(code: str) -> dict:
    kind = "r_code" if code.startswith("R-") else "treatment_code" if code.isdigit() else \
        "waste_code" if code[:2].isdigit() else None
    return {"code": code, "kind": kind, "exists": (code in KNOWN) if kind else None, "name": None}


def pack(qid="D-1", items=None, truncated=False):
    if items is None:
        items = [EvidenceItem(REF_ROW, "WMA_RULE", "B4의3", "재활용 유형", "20261112", ROW, excerpt=True),
                 EvidenceItem(REF_ART, "WMA_RULE", "3", "에너지 회수기준 등", "20261112", ART)]
    return make_pack(qid, items, truncated)


def q(qid="D-1", atype="list", when=None):
    return Question(id=qid, stage=qid[0], text="질문", answer_type=atype, when=when)


def ans(model, answer="yes", items=("R-3-2", "R-3-3", "R-9-1"), quote=ROW, ref=REF_ROW, qid="D-1", **kw):
    value = kw.pop("value", {"items": list(items)} if items is not None else {})
    quotes = kw.pop("quotes", [{"ref": ref, "text": quote}] if quote else [])
    return JudgeAnswer(question_id=qid, model=model, answer=answer, value=value, quotes=quotes, **kw)


def three(**kw):
    return [ans(m, **kw) for m in M]


# ── 1겹 인용 대조 ─────────────────────────────────────────────

def test_quote_ok_with_whitespace_and_dot_differences():
    a = ans(M[0], quote="51-03-01|폐합성수지류(고상)|R-3-2,  R-3-3, R-9-1")
    assert drop_reason(a, q(), pack(), checker) == ""


@pytest.mark.parametrize("kw,reason", [
    ({"quote": "51-03-01 | 폐합성수지류(고상) | R-3-2, R-3-4"}, "원문에 없음"),
    ({"quote": None}, "인용이 없음"),
    ({"ref": "WMA_RULE:B4의2@20261112#aa"}, "근거 묶음에 없음"),
    ({"quote": "R-3-2, R-3"}, "너무 짧음"),
])
def test_quote_failures(kw, reason):
    assert reason in drop_reason(ans(M[0], **kw), q(), pack(), checker)


def test_ref_without_hash_is_accepted():
    a = ans(M[0], ref="WMA_RULE:B4의3@20261112")
    assert drop_reason(a, q(), pack(), checker) == ""


def test_condition_quote_checked():
    bad = ans(M[0], conditions=[{"text": "이물질 제거 후", "quote": "이물질을 제거한 후에만 재활용할 수 있다", "ref": REF_ROW}])
    assert "조건" in drop_reason(bad, q(), pack(), checker)
    empty = ans(M[0], conditions=[{"text": " ", "quote": ROW, "ref": REF_ROW}])
    assert "비어" in drop_reason(empty, q(), pack(), checker)


def test_unknown_without_quote_is_valid():
    a = ans(M[0], answer="unknown", items=None, quote=None, missing_facts=["heating_value"])
    assert drop_reason(a, q(), pack(), checker) == ""


# ── 2겹 값 검사 ──────────────────────────────────────────────

def test_value_format_and_codes():
    assert "형식" in drop_reason(ans(M[0], items=()), q(), pack(), checker)             # yes 인데 빈 목록
    a = ans(M[0], items=("R-3-2", "R-7-7"), quote=ROW)
    assert "사실 데이터에 없는 코드" in drop_reason(a, q(), pack(), checker)
    # 사실 데이터에는 있지만 인용(법 원문)에는 없는 R코드 → 버림
    a = ans(M[0], items=("R-3-2", "R-1"))
    assert "인용 안에 없음" in drop_reason(a, q(), pack(), checker)
    # 처리방법 코드는 법 원문에 없어도 됨 (별지 서식 코드라 사실 데이터로만 확인)
    f1 = ans(M[0], items=("2003",), qid="F-1")
    assert drop_reason(f1, q("F-1"), pack("F-1"), checker) == ""
    # 코드가 아닌 항목(허가 종류)은 코드 검사를 건너뜀
    i1 = ans(M[0], items=("폐기물 종합재활용업",), qid="I-1")
    assert drop_reason(i1, q("I-1"), pack("I-1"), checker) == ""


def test_compare_criterion_number_must_be_quoted():
    qq = q("E-1", "compare")
    ok = ans(M[0], qid="E-1", items=None, quote=ART, ref=REF_ART,
             value={"criterion": "저위발열량 3천킬로칼로리 이상", "case_value": 10200, "unit": "kcal/kg", "result": "met"})
    assert drop_reason(ok, qq, pack("E-1"), checker) == ""
    bad = ans(M[0], qid="E-1", items=None, quote=ART, ref=REF_ART,
              value={"criterion": "회수효율 80퍼센트 이상", "case_value": 85, "result": "met"})
    assert "기준 숫자 80" in drop_reason(bad, qq, pack("E-1"), checker)


def test_wrong_question_id_dropped():
    assert "질문 id" in drop_reason(ans(M[0], qid="D-2"), q(), pack(), checker)


# ── 3겹 합의 ─────────────────────────────────────────────────

def test_unanimous_agreed():
    v = decide(q(), pack(), three(), [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "yes" and set(v.value["items"]) == {"R-3-2", "R-3-3", "R-9-1"}
    assert [x["model"] for x in v.votes] == M and all(x["valid"] for x in v.votes)
    assert v.pack_hash == pack().pack_hash and not v.flags


def test_two_to_one_is_adopted_but_safety_veto():
    answers = three()
    answers[2] = ans(M[2], answer="no", items=())
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "conditional"
    assert "safety_veto" in v.flags
    assert any("모델 간 이견" in c["text"] and M[2] in c["text"] for c in v.conditions)


def test_two_to_one_with_minority_unknown_no_veto():
    """빠진 사실 없이 '모름' 인 소수는 반대 근거가 아니다 (빠진 사실을 댄 경우는 아래 테스트)."""
    answers = three()
    answers[1] = ans(M[1], answer="unknown", items=None, quote=None)
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "yes" and "safety_veto" not in v.flags


def test_list_order_does_not_matter_but_items_do():
    answers = [ans(M[0]), ans(M[1], items=("R-9-1", "R-3-3", "R-3-2")), ans(M[2], items=("R-3-2", "R-3-3"))]
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.answer == "conditional" and "safety_veto" in v.flags          # 2:1 → 채택 + 소수 이견
    assert set(v.value["items"]) == {"R-3-2", "R-3-3", "R-9-1"}


def test_safety_veto_can_be_turned_off():
    answers = three()
    answers[2] = ans(M[2], answer="no", items=())
    cfg = VerifierConfig(**{**CFG.__dict__, "safety_veto": False})
    assert decide(q(), pack(), answers, [], 0, cfg, checker).answer == "yes"


def test_three_way_disagreement_retry_then_needs_human():
    answers = [ans(M[0]), ans(M[1], items=("R-3-2",)), ans(M[2], answer="no", items=())]
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "retry" and v.retries == 1
    assert "R-3-3" in v.retry_hint and "R-9-1" in v.retry_hint
    v = decide(q(), pack(), answers, [], 1, CFG, checker)
    assert v.status == "retry" and v.retries == 2
    v = decide(q(), pack(), answers, [], 2, CFG, checker)
    assert v.status == "needs_human" and v.retries == 2


def test_one_to_one_tie_retries():
    answers = [ans(M[0]), ans(M[1], answer="no", items=())]
    assert decide(q(), pack(), answers, [], 0, CFG, checker).status == "retry"


def test_dropped_answers_reduce_below_min_judges():
    answers = three()
    answers[0] = ans(M[0], quote="원문에 없는 문장을 지어낸 인용입니다")
    answers[1] = ans(M[1], quote=None)
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "needs_human"
    assert [x["valid"] for x in v.votes] == [False, False, True]


def test_judge_failures_and_budget():
    failed = [error_record("judge", "Timeout", "D-1", M[1]), error_record("judge", "Timeout", "D-1", M[2])]
    v = decide(q(), pack(), [ans(M[0])], failed, 0, CFG, checker)
    assert v.status == "needs_human" and "judge_failed" in v.flags
    assert [x["drop_reason"][:5] for x in v.votes[1:]] == ["호출 실패"] * 2
    failed = [error_record("judge", f"{schedule.BUDGET_PREFIX}: 40회", "D-1", m) for m in M]
    v = decide(q(), pack(), [], failed, 0, CFG, checker)
    assert "budget_exceeded" in v.flags and v.status == "needs_human"


def test_two_valid_out_of_three_still_agree():
    answers = three()
    answers[2] = ans(M[2], quote="지어낸 인용 문장입니다 열 글자 넘게")
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "yes"


# ── unknown·근거 ─────────────────────────────────────────────

def test_unknown_with_missing_facts_is_needs_info():
    answers = [ans(m, answer="unknown", items=None, quote=None, qid="E-1", missing_facts=mf)
               for m, mf in zip(M, [["heating_value"], ["heating_value", "analysis"], []])]
    v = decide(q("E-1", "compare"), pack("E-1"), answers, [], 0, CFG, checker)
    assert v.status == "needs_info" and v.missing_facts == ["analysis", "heating_value"]


def test_unknown_without_missing_facts_retries_then_needs_human():
    """사실은 있는데 모름 = 근거 부족 → 넓은 근거로 재검색, 여유가 없으면 사람에게."""
    answers = [ans(m, answer="unknown", items=None, quote=None) for m in M]
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "retry" and v.retries == 1
    assert decide(q(), pack(), answers, [], 1, CFG, checker).status == "needs_human"   # 넓게 한 번 뒤엔 사람에게


def test_empty_pack_retries_then_low_retrieval():
    v = decide(q(), pack(items=[]), three(), [], 0, CFG, checker)
    assert v.status == "retry" and "low_retrieval" in v.flags
    v = decide(q(), pack(items=[]), three(), [], 1, CFG, checker)
    assert v.status == "needs_human" and "low_retrieval" in v.flags


def test_pack_flags():
    items = [EvidenceItem(REF_ROW, "WMA_RULE", "B4의3", "t", "20261112", ROW, complete=False, problems=["호 빠짐"])]
    v = decide(q(), pack(items=items, truncated=True), three(), [], 0, CFG, checker)
    assert v.status == "agreed" and {"evidence_truncated", "incomplete_article"} <= set(v.flags)


def test_conditions_and_quotes_merged_from_majority():
    c1 = {"text": "이물질 제거 후", "quote": ROW, "ref": REF_ROW}
    c2 = {"text": "고상인 경우만", "quote": ROW, "ref": REF_ROW}
    answers = [ans(M[0], answer="conditional", conditions=[c1]), ans(M[1], answer="conditional", conditions=[c1, c2]),
               ans(M[2], answer="conditional", conditions=[c2])]
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.answer == "conditional" and [c["text"] for c in v.conditions] == ["이물질 제거 후", "고상인 경우만"]
    assert len(v.quotes) == 1


# ── 노드·차례 ────────────────────────────────────────────────

def mini_state() -> AgentState:
    qs = [q("B-1", "yes_no_cond"), q("D-1", "list", when="B-1 != yes"), q("D-2", "list", when="D-1 has items"),
          q("E-1", "compare", when="D-1 has R-9"), q("H-2", "compare", when="A-2 == yes"), q("A-2", "yes_no_cond")]
    return AgentState(questions=qs, verdicts={}, packs={}, answers={}, retry_count={}, audit=[], errors=[])


def b1_answers(answer="no"):
    return [ans(m, answer=answer, items=None, qid="B-1", value={}) for m in M]


def test_schedule_waves():
    st = mini_state()
    assert schedule.classify(st)["ready"] == ["B-1", "A-2"]
    assert set(schedule.classify(st)["pending"]) == {"D-1", "D-2", "E-1", "H-2"}


def test_node_round_flow_and_not_applicable():
    st = mini_state()
    # 1차: B-1, A-2
    st["packs"] = {"B-1": pack("B-1"), "A-2": pack("A-2")}
    st["answers"] = {"B-1": b1_answers("no"), "A-2": [ans(m, answer="no", items=None, qid="A-2", value={}) for m in M]}
    out = node(st, CFG, checker)
    assert out["verdicts"]["B-1"].status == "agreed" and out["verdicts"]["A-2"].answer == "no"
    assert out["verdicts"]["H-2"].status == "not_applicable"           # 지정폐기물 아님 → 처리계획 확인 안 물음
    assert out["audit"][0]["node"] == "verifier" and out["audit"][0]["round"] == 1
    st["verdicts"] = {**st["verdicts"], **out["verdicts"]}
    st["audit"] = st["audit"] + out["audit"]
    assert schedule.classify(st)["ready"] == ["D-1"] and route_after_verify(st) == "retriever"

    # 2차: D-1 (R-9 없음) → E-1 은 not_applicable, D-2 는 다음 차례
    st["packs"]["D-1"] = pack("D-1")
    st["answers"]["D-1"] = three(items=("R-3-2", "R-3-3"))
    out = node(st, CFG, checker)
    assert out["verdicts"]["D-1"].status == "agreed"
    assert out["verdicts"]["E-1"].status == "not_applicable"
    assert "B-1" not in out["verdicts"]                               # 이미 끝난 질문은 다시 판단하지 않음
    st["verdicts"] = {**st["verdicts"], **out["verdicts"]}
    st["audit"] = st["audit"] + out["audit"]
    assert schedule.ready_ids(st) == ["D-2"]

    # 3차: D-2
    st["packs"]["D-2"] = pack("D-2")
    st["answers"]["D-2"] = three(qid="D-2", items=("R-3-2",))
    out = node(st, CFG, checker)
    st["verdicts"] = {**st["verdicts"], **out["verdicts"]}
    assert route_after_verify(st) == "composer"
    assert all(v.final for v in st["verdicts"].values()) and len(st["verdicts"]) == 6


def test_node_retry_sets_retry_count_and_routes_back():
    st = mini_state()
    st["packs"] = {"B-1": pack("B-1"), "A-2": pack("A-2")}
    st["answers"] = {"B-1": [ans(M[0], answer="no", items=None, qid="B-1", value={}),
                             ans(M[1], answer="yes", items=None, qid="B-1", value={})],
                     "A-2": [ans(m, answer="no", items=None, qid="A-2", value={}) for m in M]}
    out = node(st, CFG, checker)
    assert out["verdicts"]["B-1"].status == "retry" and out["retry_count"] == {"B-1": 1}
    st["verdicts"] = out["verdicts"]
    assert "B-1" in schedule.ready_ids(st) and route_after_verify(st) == "retriever"
    assert "D-1" in schedule.classify(st)["pending"]                  # retry 중인 B-1 을 기다림


def test_node_unprocessed_ready_question_is_not_judged():
    st = mini_state()
    st["packs"] = {"B-1": pack("B-1")}                                # A-2 는 이번에 묶음이 없음
    st["answers"] = {"B-1": b1_answers()}
    out = node(st, CFG, checker)
    assert "A-2" not in out["verdicts"]


def test_node_judge_all_failed_for_question():
    st = mini_state()
    st["packs"] = {"B-1": pack("B-1"), "A-2": pack("A-2")}
    st["answers"] = {"B-1": b1_answers(), "A-2": []}
    st["errors"] = [error_record("judge", "Timeout", "A-2", m) for m in M]
    out = node(st, CFG, checker)
    assert out["verdicts"]["A-2"].status == "needs_human" and "judge_failed" in out["verdicts"]["A-2"].flags


def test_node_max_rounds_stops_everything(monkeypatch):
    st = mini_state()
    st["audit"] = [{"node": "verifier"}] * (schedule.MAX_ROUNDS - 1)
    st["packs"] = {"B-1": pack("B-1"), "A-2": pack("A-2")}
    st["answers"] = {"B-1": [ans(M[0], answer="no", items=None, qid="B-1", value={}),
                             ans(M[1], answer="yes", items=None, qid="B-1", value={})],
                     "A-2": [ans(m, answer="yes", items=None, qid="A-2", value={}) for m in M]}
    out = node(st, CFG, checker)
    st["verdicts"] = out["verdicts"]
    assert all(v.final for v in out["verdicts"].values())
    assert out["verdicts"]["D-1"].status == "needs_human" and "budget_exceeded" in out["verdicts"]["D-1"].flags
    assert route_after_verify(st) == "composer"


def test_config_from_settings():
    cfg = VerifierConfig.from_settings()
    assert len(cfg.judge_models) == 3 and cfg.min_judges == 2 and cfg.re_retrieve_max == 2 and cfg.safety_veto


def test_real_checklist_first_wave():
    qs = load_checklist(Path(__file__).resolve().parents[1] / "config" / "checklist.yaml")
    st = AgentState(questions=qs, verdicts={})
    first = schedule.ready_ids(st)
    # 판단 누적: 분류(A-1·A-2·A-4)가 먼저, 분류번호·지정 여부가 필요 없는 J-4 도 1차
    assert first == ["A-1", "A-2", "A-4", "J-4"]


def test_conditional_keeps_missing_facts():
    c = {"text": "석면 1% 이상이면", "quote": ROW, "ref": REF_ROW}
    answers = [ans(m, answer="conditional", conditions=[c], missing_facts=["석면 함량"]) for m in M]
    v = decide(q(), pack(), answers, [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "conditional" and v.missing_facts == ["석면 함량"]


# ── 빠진 사실을 댄 '모름' (PRE-02 B-1 세척용제 성분) ─────────

def test_majority_no_with_minority_unknown_missing_becomes_conditional():
    answers = [ans(M[0], answer="no", items=()), ans(M[1], answer="unknown", items=None, quote=None,
                                                      missing_facts=["세척용제 성분"]),
               ans(M[2], answer="no", items=())]
    qq = q("B-1", "yes_no_cond")
    for a in answers:
        a.question_id, a.value = "B-1", {}
    v = decide(qq, pack("B-1"), answers, [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "conditional" and v.missing_facts == ["세척용제 성분"]
    assert "safety_veto" in v.flags and "세척용제 성분" in v.conditions[-1]["text"]


def test_decided_vs_unknown_missing_no_retry_needs_info():
    """1 '아님' 대 1 '모름(빠진 사실)' — 근거를 넓혀도 사실은 안 생기므로 재검색하지 않음."""
    qq = q("B-1", "yes_no_cond")
    answers = [ans(M[0], answer="no", items=None, qid="B-1", value={}),
               ans(M[1], answer="unknown", items=None, quote=None, qid="B-1", missing_facts=["금지물질 해당 여부"])]
    v = decide(qq, pack("B-1"), answers, [], 0, CFG, checker)
    assert v.status == "needs_info" and v.missing_facts == ["금지물질 해당 여부"] and v.retries == 0


def test_decided_disagree_still_retries():
    qq = q("B-1", "yes_no_cond")
    answers = [ans(M[0], answer="no", items=None, qid="B-1", value={}),
               ans(M[1], answer="yes", items=None, qid="B-1", value={})]
    assert decide(qq, pack("B-1"), answers, [], 0, CFG, checker).status == "retry"


# ── 파트 1 의심 신호 (PRE-08 도료 묻은 플라스틱) ──────────────

@pytest.mark.parametrize("case,expect", [
    ({"hazardous_suspect": True}, "conditional"),
    ({"designated_waste_status": "designated"}, "conditional"),
    ({"hazardous_suspect": False, "designated_waste_status": "non_designated"}, "no"),
    ({}, "no"),
])
def test_input_signal_guard(case, expect):
    from law_agent.agents.verifier import input_signal_guard
    v = Verdict("A-2", "agreed", "no", quotes=[{"ref": REF_ROW, "text": ROW}])
    g = input_signal_guard(q("A-2", "yes_no_cond"), v, case, CFG)
    assert g.answer == expect
    if expect == "conditional":
        assert "safety_veto" in g.flags and any("성분 분석" in m for m in g.missing_facts)
        assert "파트 1이" in g.conditions[-1]["text"]


def test_input_signal_guard_only_loosening():
    from law_agent.agents.verifier import input_signal_guard
    yes = Verdict("A-2", "agreed", "yes")
    assert input_signal_guard(q("A-2", "yes_no_cond"), yes, {"hazardous_suspect": True}, CFG) is yes   # 더 엄격은 그대로
    other = Verdict("B-1", "agreed", "no")
    assert input_signal_guard(q("B-1", "yes_no_cond"), other, {"hazardous_suspect": True}, CFG) is other
    sub = Verdict("A-2.c1", "agreed", "no")
    qq = Question(id="A-2.c1", stage="A", text="t", answer_type="yes_no_cond", parent_id="A-2", component=1)
    assert input_signal_guard(qq, sub, {"hazardous_suspect": True}, CFG).answer == "conditional"


def test_law_gap_moves_out_of_missing_facts():
    """'법 내용'을 missing_facts 에 적으면 파트 1 질문이 아니라 근거 부족(재검색 → 사람)으로."""
    from law_agent.agents.judge import split_gaps, to_answer
    facts, gaps = split_gaps(["R코드별 재활용 유형", "사업장폐기물 처리기준 원문", "분류번호 51-03-01의 처분 방법",
                              "PCB 농도", "업종", "할로겐족 유기용제 여부"], [])
    assert facts == ["PCB 농도", "업종", "할로겐족 유기용제 여부"] and len(gaps) == 3
    a = to_answer({"answer": "unknown", "missing_facts": ["별표 5 처분 방법"], "evidence_gaps": ["F-1 행"]},
                  q("F-1"), M[0], "", "v")
    assert a.missing_facts == [] and a.evidence_gaps == ["F-1 행", "별표 5 처분 방법"]
    answers = [JudgeAnswer("F-1", m, "unknown", missing_facts=[], evidence_gaps=["별표 5 행"]) for m in M[:2]]
    v = decide(q("F-1"), pack("F-1"), answers, [], 0, CFG, checker)
    assert v.status == "retry" and "별표 5 행" in v.retry_hint and "evidence_gap" in v.flags
    v2 = decide(q("F-1"), pack("F-1"), answers, [], 1, CFG, checker)
    assert v2.status == "needs_human" and not v2.missing_facts


def test_list_item_vote_like_demo_d1():
    """데모 1 D-1: 세 모델이 같은 R코드를 냈는데 yes/conditional·사전 분석 표시만 다름 → 합의 (conditional, 사전 분석은 참 쪽)."""
    a0 = ans(M[0], value={"items": ["R-3-2", "R-3-3", "R-9-1"], "attrs": {"R-9-1": {"pre_analysis": False}}})
    a1 = ans(M[1], answer="conditional",
             value={"items": ["R-3-2", "R-3-3", "R-9-1"], "attrs": {"R-9-1": {"pre_analysis": True}}})
    a2 = ans(M[2], value={"items": ["R-9-1", "R-3-2", "R-3-3"]})
    v = decide(q(), pack(), [a0, a1, a2], [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "conditional"
    assert v.value["items"] == ["R-3-2", "R-3-3", "R-9-1"] and v.value["attrs"]["R-9-1"]["pre_analysis"] is True


def test_list_item_vote_minority_extra_items_not_adopted():
    """데모 1 F-1: 두 모델 [소각, 매립], 한 모델은 [소각, 파쇄, …, 매립] → 소각·매립만 채택, 나머지는 검토 메모."""
    text = "폐합성고분자화합물은 소각하여야 한다. 다만, 소각이 곤란한 경우에는 파쇄·절단 또는 용융한 후 관리형 매립시설에 매립할 수 있다."
    ref = make_ref("WMA_RULE", "B5:3", "20261112", "cc")
    pk = make_pack("F-1", [EvidenceItem(ref, "WMA_RULE", "B5:3", "별표 5", "20261112", text)])
    mk = lambda m, its, a="conditional": ans(m, answer=a, items=its, quote=text, ref=ref, qid="F-1")   # noqa: E731
    answers = [mk(M[0], ("소각", "매립")), mk(M[1], ("소각", "매립")), mk(M[2], ("소각", "파쇄", "용융", "매립"), "yes")]
    v = decide(q("F-1"), pk, answers, [], 0, CFG, checker)
    assert v.status == "agreed" and v.answer == "conditional" and v.value["items"] == ["소각", "매립"]
    assert any(c.get("kind") == "model_disagreement" and "파쇄" in c["text"] for c in v.conditions)


def test_f1_method_name_must_be_in_evidence():
    text = "폐합성고분자화합물은 소각하여야 한다."
    ref = make_ref("WMA_RULE", "B5:3", "20261112", "cc")
    pk = make_pack("F-1", [EvidenceItem(ref, "WMA_RULE", "B5:3", "별표 5", "20261112", text)])
    a = ans(M[0], items=("소각", "플라즈마처리"), quote=text, ref=ref, qid="F-1")
    assert "근거 원문에 없음: 플라즈마처리" in drop_reason(a, q("F-1"), pk, checker)



def test_quote_marks_and_ref_tolerated():
    """원문 “ ” 와 모델 " 차이, ref 만 다른 조문을 가리킨 인용은 버리지 않는다."""
    from law_agent.state import quote_in
    assert quote_in('(이하 "지방환경관서의 장"이라 한다)', "(이하 “지방환경관서의 장”이라 한다)")



def test_quote_without_definition_paren():
    from law_agent.state import quote_in
    src = '도지사(이하 "시ㆍ도지사"라 한다) 또는 유역환경청장ㆍ지방환경청장(이하 "지방환경관서의 장"이라 한다)으로부터 승인'
    assert quote_in('시ㆍ도지사 또는 유역환경청장ㆍ지방환경청장(이하 "지방환경관서의 장"이라 한다)으로부터', src)  # 실제 G-3 인용
    assert quote_in('도지사 또는 유역환경청장ㆍ지방환경청장으로부터 승인', src)
    assert not quote_in('도지사 또는 지방환경청장으로부터 승인', src)                      # 다른 글자 생략은 안 됨
