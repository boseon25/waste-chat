"""기준 질문(원문 첨부) → 기준마다 한 줄 + 누구 기준인지 + '맡길 업체가 지켜야 할 기준' 묶음 (2026.11.20)."""
from law_agent.agents.attach import attach_verdict
from law_agent.output import law_summary as L
from law_agent.output import plain
from law_agent.output.cards_html import body
from law_agent.state import Candidate, EvidenceItem, EvidencePack, Question

COMMON = "1. 공통기준 가. 재활용 과정에서 분말상으로 비산될 우려가 있는 경우에는 용기에 넣거나 포장해야 한다. " + "나. 기록을 보존해야 한다. " * 30
R10 = "R-10-1 유형의 재활용 기준: 별표 4의3에 따른 폐기물의 종류별 재활용 가능 유형에 적합한 폐기물을 사용하여야 한다."


def _d3():
    q = Question(id="D-3", stage="D", text="재활용 기준", answer_type="text", attach=True)
    items = [EvidenceItem(ref="WMA_RULE:B5의3:1@20261112#a", law="WMA_RULE", key="B5의3:1", title="재활용 기준", effective="20261112", text=COMMON),
             EvidenceItem(ref="WMA_RULE:B5의3:2~2@20261112#b", law="WMA_RULE", key="B5의3:2~2", title="재활용 기준", effective="20261112", text=R10)]
    v = attach_verdict(q, EvidencePack(question_id="D-3", items=items, pack_hash="h"),
                       [Candidate(law="WMA_RULE", key="B5의3", title="", score=1.0, source="min_laws")])
    d = v.to_dict() if hasattr(v, "to_dict") else dict(v.__dict__)
    d.update({"question_id": "D-3", "quotes": [q if isinstance(q, dict) else q.__dict__ for q in v.quotes]})
    return d


def test_attach_keeps_full_standard_text():
    d = _d3()
    st = d["value"]["standards"]
    assert len(st) == 2 and st[0]["text"] == COMMON and len(COMMON) > 400      # 400자에서 자르지 않음


def test_lines_fallback_then_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("LAW_AGENT_DATA_DIR", str(tmp_path))
    L.reset()
    s = plain.summarize(_d3(), {"values": {"waste_code": "51-03-01"}})
    assert s["label"] == "기준 찾음" and s["who"] == "맡길 재활용업체" and s["when"] == "업체 고를 때"
    assert s["standards"][1]["text"].startswith("R-10-1 유형의 재활용 기준") and not s["standards"][1]["plain"]   # 요약 없음 → 첫 문장
    L.save({L.key_of(R10): {"ok": True, "summary": "허용된 재활용 유형에 맞는 폐기물만 써야 합니다.", "reviewed": False}})
    L.reset()
    s = plain.summarize(_d3(), {"values": {"waste_code": "51-03-01"}})
    assert s["standards"][1]["text"] == "허용된 재활용 유형에 맞는 폐기물만 써야 합니다." and s["standards"][1]["plain"]
    L.reset()


def test_cards_section_and_html():
    c = plain.cards([_d3()], {"values": {"waste_code": "51-03-01"}}, ["R-10"])
    ps = c["partner_standards"]
    assert [x["question_id"] for x in ps] == ["D-3"] and len(ps[0]["lines"]) == 2
    h = body(c)
    assert "맡길 업체가 지켜야 할 기준" in h and "맡길 재활용업체 · 업체 고를 때" in h and "원문 보기" in h
    assert "맡길 업체가 지켜야 할 기준" in plain.show_cards(c)


def test_emitter_standard_not_in_partner_section():
    d = _d3()
    d["question_id"] = "G-1"
    c = plain.cards([d], {"values": {"waste_code": "51-03-01"}}, ["R-10"])
    assert c["partner_standards"] == []
    assert "해야 할 일" in next(x for x in c["details"] if x["question_id"] == "G-1")["summary"]
