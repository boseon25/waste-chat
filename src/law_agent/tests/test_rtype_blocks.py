"""별표 5의3 유형별 재활용 기준 — R코드 유형 부분만 (2026.11.20 세척액 R-10 이 빠진 문제)."""
from types import SimpleNamespace as NS

from law_agent.agents import evidence
from law_agent.agents.attach import attach_verdict
from law_agent.state import Candidate, EvidenceItem, EvidencePack, Question
from law_agent.tools.law_tools import rtype_blocks

PART2 = """2. 유형별 재활용의 세부기준
  마. 열분해 유형
    2) R-9 유형의 재활용 기준
      가) R-9-2 유형의 재활용 기준
        폐유는 정제하여야 한다.
      (1) 가)에 따른 R-7-1 유형의 재활용 기준을 준수하여 재활용하여야 한다.
      나) R-9-3 유형의 재활용 기준
        다른 내용.
    바. 제품제조 등을 위한 중간가공폐기물을 만드는 유형
      R-10-1 유형의 재활용 기준: 별표 4의3에 따른 폐기물의 종류별 재활용 가능
  유형에 적합한 폐기물을 사용하여야 한다.
비고
  1. 비고 내용"""


def test_parent_code_takes_child_type():
    b = rtype_blocks(PART2, ["R-10"])
    assert [x["code"] for x in b] == ["R-10-1"]
    assert "적합한 폐기물을 사용" in b[0]["text"] and "비고" not in b[0]["text"]


def test_sentence_mention_is_not_heading():
    b = rtype_blocks(PART2, ["R-9-2"])
    codes = [x["code"] for x in b]
    assert codes == ["R-9", "R-9-2"]                      # 상위 머리글 + 해당 유형
    assert "R-7-1 유형의 재활용 기준을 준수" in b[1]["text"]  # 문장 속 언급은 끊지 않음
    assert "R-9-3" not in b[1]["text"]


def test_no_rcode_no_block():
    assert rtype_blocks(PART2, ["04-02-00"]) == []
    assert rtype_blocks(PART2, ["R-4-5"]) == []


def _chunk(key, text):
    return NS(law="WMA_RULE", key=key, title="폐기물의 재활용 기준", effective="20261112", hash="h" + key, text=text)


class Store:
    def __init__(self, parts):
        self.parts = parts

    def children(self, law, key):
        return self.parts


def test_items_common_plus_type():
    store = Store([_chunk("B5의3:1", "1. 공통기준\n 가. 날림 방지"), _chunk("B5의3:2~2", PART2)])
    c = Candidate(law="WMA_RULE", key="B5의3", title="", score=1.0, source="min_laws")
    items = evidence._rtype_items(c, ["04-02-00", "R-10"], store)
    assert [i.key for i in items] == ["B5의3:1", "B5의3:2~2"]
    assert "R-10-1" in items[1].text and "R-9-2" not in items[1].text


def test_attach_no_standin_when_min_law_missing():
    q = Question(id="D-3", stage="D", text="재활용 기준", answer_type="text", attach=True)
    other = EvidenceItem(ref="WMA:13의2@1#b", law="WMA", key="13의2", title="재활용 원칙", effective="1", text="원칙")
    pack = EvidencePack(question_id="D-3", items=[other], pack_hash="h")
    cands = [Candidate(law="WMA_RULE", key="B5의3", title="", score=1.0, source="min_laws")]
    v = attach_verdict(q, pack, cands)
    assert v.status == "needs_human" and not v.quotes and "B5의3" in v.value["text"]
