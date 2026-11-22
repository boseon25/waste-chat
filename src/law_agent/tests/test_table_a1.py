"""A-1 분류번호 확인을 표(별표 4)로 — 확인된 출처 번호만 (2026.11.20)"""
import pytest

from law_agent.agents import table_answer as ta
from law_agent.tools import fact_tools


@pytest.fixture
def tables(tmp_path, monkeypatch):
    (tmp_path / "waste_codes_4.csv").write_text(
        "분류번호,폐기물_구분,단계,상위,명칭,근거,말단\n"
        "51-03-01,사업장일반폐기물,3,51-03,폐합성수지류(폐염화비닐수지류는 제외한다),WMA_RULE:B4@20261112#ab,Y\n",
        encoding="utf-8")
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path))
    fact_tools._read.cache_clear()
    yield tmp_path
    fact_tools._read.cache_clear()


AUTH = {"classification": {"selected_code": "51-03-01", "source_type": "current_case_authority_reply",
                           "status": "synthetic_authority_reviewed_for_example", "conditions": "A동 PF-01 한정"}}


def test_authority_code_in_table_is_yes(tables):
    v = ta.a1(AUTH, "51-03-01")
    assert v.answer == "yes" and v.value["items"] == ["51-03-01"] and "기관 회신" in v.value["basis"]
    assert v.value["scope"] == "A동 PF-01 한정" and v.quotes[0]["text"].startswith("51-03-01 폐합성수지류")
    assert ta.FLAG_TABLE in v.flags


def test_code_not_in_table_is_no(tables):
    v = ta.a1(AUTH, "99-99-99")
    assert v.answer == "no" and v.value["items"] == []


def test_unconfirmed_source_goes_to_llm(tables):
    assert ta.a1({"classification": {"selected_code": "51-03-01", "source_type": "ai_estimate"}}, "51-03-01") is None


def test_existing_declaration_counts(tables):
    case = {"classification": {}, "existing_declaration": {"exists": True, "waste_code": "51-03-01"}}
    assert ta.a1(case, "51-03-01").answer == "yes"


def test_missing_table_goes_to_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path))
    fact_tools._read.cache_clear()
    from law_agent.state import Question
    q = Question(id="A-1", stage="A", text="", answer_type="yes_no_cond")
    assert ta.answer(q, {"case": AUTH}, {"values": {"waste_code": "51-03-01"}}) is None
