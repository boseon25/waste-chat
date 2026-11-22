"""G-2(전자인계)는 H-2(처리계획 대상) 판단 뒤에 묻는다 (2026.11.20 세척액: 실행마다 조건부/예가 갈림)."""
from law_agent.agents.context import effective_facts, prior_findings, question_facts
from law_agent.agents.schedule import classify
from law_agent.state import Question, Verdict

A2 = Question(id="A-2", stage="A", text="지정폐기물", answer_type="yes_no")
H2 = Question(id="H-2", stage="H", text="처리계획 대상", answer_type="compare", when="A-2 == yes")
G2 = Question(id="G-2", stage="G", text="전자인계", answer_type="yes_no_cond", needs_facts=["gate", "plan_check"])
QS = [A2, H2, G2]


def st(**vs):
    return {"questions": QS, "verdicts": {k.replace("_", "-"): v for k, v in vs.items()}, "facts": {"values": {}}}


def test_designated_waits_for_h2():
    s = st(A_2=Verdict(question_id="A-2", status="agreed", answer="yes"))
    c = classify(s)
    assert "H-2" in c["ready"] and "G-2" in c["pending"]
    s["verdicts"]["H-2"] = Verdict(question_id="H-2", status="agreed", answer="yes")
    assert "G-2" in classify(s)["ready"]
    f = question_facts(G2, s)
    assert f["plan_check"]["값"] == "처리계획 확인 대상" and f["plan_check"]["출처"] == "H-2 판단"
    assert "H-2" in prior_findings(G2, s)


def test_not_designated_h2_skipped_then_g2():
    s = st(A_2=Verdict(question_id="A-2", status="agreed", answer="no"),
           H_2=Verdict(question_id="H-2", status="not_applicable"))
    assert "G-2" in classify(s)["ready"]
    assert effective_facts(s)["values"]["plan_check"] == "해당 없음 (지정폐기물 아님)"


def test_h2_unsure_is_not_a_fact():
    s = st(A_2=Verdict(question_id="A-2", status="agreed", answer="yes"),
           H_2=Verdict(question_id="H-2", status="needs_info"))
    assert "G-2" in classify(s)["ready"]                        # 기다림은 끝 (확정 안 됨으로 판단)
    assert "plan_check" not in question_facts(G2, s)
    assert effective_facts(s)["unconfirmed"]["plan_check"] == "H-2"
