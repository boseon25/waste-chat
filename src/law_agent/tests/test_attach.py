from law_agent.agents.attach import attach_verdict
from law_agent.state import Candidate, EvidenceItem, EvidencePack, Question


def test_attach_uses_min_law_items():
    q = Question(id="D-3", stage="D", text="재활용 기준", answer_type="text", attach=True)
    a = EvidenceItem(ref="WMA_RULE:B5의3:2@1#a", law="WMA_RULE", key="B5의3:2", title="재활용 기준", effective="1",
                     text="R-10-1 유형의 재활용 기준: 별표 4의3에 적합한 폐기물을 사용")
    b = EvidenceItem(ref="RRA_DEC:18@1#b", law="RRA_DEC", key="18", title="다른 법", effective="1", text="무관")
    pack = EvidencePack(question_id="D-3", items=[a, b], pack_hash="h")
    cands = [Candidate(law="WMA_RULE", key="B5의3", title="", score=1.0, source="min_laws")]
    v = attach_verdict(q, pack, cands)
    assert v.status == "agreed" and "R-10-1" in v.value["text"] and [x["ref"] for x in v.quotes] == [a.ref]
    assert attach_verdict(q, EvidencePack(question_id="D-3", items=[]), cands) is None


def test_generic_drops_case_name():
    from law_agent.agents.retriever import build_queries
    q = Question(id="I-3", stage="I", text="운반 허가", answer_type="text", generic=True)
    assert all("PE 필름" not in x for x in build_queries(q, {"name": "PE 필름"}, {}))
    q.generic = False
    assert any("PE 필름" in x for x in build_queries(q, {"name": "PE 필름"}, {}))
