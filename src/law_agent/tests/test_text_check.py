"""글 칸 AI 검토 (review/text_check.py) — 모델 호출은 가짜로"""
import pytest

from law_agent.assist import service as assist
from law_agent.review import review_form, text_check

import json

CONTRACT_OK = {
    "generator_name": "다온산업", "receiver_name": "미래환경수지", "hauler_name": "새만금운수",
    "waste_name": "PE 필름 자투리", "waste_state": "고체(파쇄)", "precautions": "비 맞지 않게 보관, 화기 주의",
    "treatment_method": "2009", "contract_period": "2026-12-01 ~ 2027-11-30", "contract_quantity": "월 1.2톤",
    "processing_price": "120원/kg",
}


@pytest.fixture
def box(tmp_path):
    case = {"case_id": "PE01", "name": "제품 포장용 PE 필름 자투리", "material_form": "파쇄",
            "classification": {"selected_code": "51-03-01"}, "quantity": {"monthly_ton": [1.2]}}
    (tmp_path / "PE01_v1_case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "PE01_v1_proposals.json").write_text("[]", encoding="utf-8")
    return tmp_path



@pytest.fixture
def llm_on(monkeypatch, tmp_path):
    monkeypatch.setenv("LAW_AGENT_REVIEW_LLM", "1")
    monkeypatch.setenv("LAW_AGENT_DATA_DIR", str(tmp_path / "data"))
    calls = []

    def fake(answer):
        def _call(system, user, qid):
            calls.append(user)
            return answer
        return _call
    return calls, fake


def _quote_for(doc="contract", field="precautions"):
    from law_agent.guide.service import _law
    from law_agent.review import field_map
    from law_agent.review.service import requirements
    law = _law(field_map.load(doc), field, requirements())
    assert law, "계약서 취급 주의사항 칸에 기준표 행이 있어야 함"
    return law[0].get("sentence") or law[0]["quote"]


def test_missing_with_real_quote_becomes_warning(box, llm_on, monkeypatch):  # noqa: F811
    calls, fake = llm_on
    q = _quote_for()
    monkeypatch.setattr(text_check, "_call", fake({"missing": [{"what": "폐기물의 성질과 상태", "quote": q[:40]}],
                                                    "conflicts": []}))
    res = review_form({"case_id": "PE01", "doc": "contract", "values": {**CONTRACT_OK, "precautions": "주의할 것"},
                       "labels": {"precautions": "취급 시 주의사항"}}, box)
    f = [x for x in res["findings"] if x["kind"] == "llm_text" and x["field"] == "precautions"]
    assert f and f[0]["severity"] == "warning" and "폐기물의 성질과 상태" in f[0]["message"] and f[0]["item"] == "취급 시 주의사항"
    assert res["verdict"] in ("check_required", "fix_required")
    assert any("주의할 것" in c for c in calls)        # 사용자 글이 프롬프트에 들어감


def test_made_up_quote_is_dropped(box, llm_on, monkeypatch):  # noqa: F811
    calls, fake = llm_on
    monkeypatch.setattr(text_check, "_call", fake({"missing": [{"what": "x", "quote": "원문에 없는 지어낸 문장입니다 정말로"}]}))
    res = review_form({"case_id": "PE01", "doc": "contract", "values": CONTRACT_OK}, box)
    assert not [x for x in res["findings"] if x["kind"] == "llm_text"]


def test_llm_failure_is_note_not_finding(box, llm_on, monkeypatch):  # noqa: F811
    calls, fake = llm_on
    monkeypatch.setattr(text_check, "_call", fake(None))
    res = review_form({"case_id": "PE01", "doc": "contract", "values": CONTRACT_OK}, box)
    assert not [x for x in res["findings"] if x["kind"] == "llm_text"]
    assert any("글 칸 내용 검토(AI)를 하지 못했습니다" in n for n in res["notes"])


def test_off_by_request_or_env(box, llm_on, monkeypatch):  # noqa: F811
    calls, fake = llm_on
    monkeypatch.setattr(text_check, "_call", fake({"missing": []}))
    review_form({"case_id": "PE01", "doc": "contract", "values": CONTRACT_OK, "llm": False}, box)
    assert calls == []
    monkeypatch.setenv("LAW_AGENT_REVIEW_LLM", "0")
    review_form({"case_id": "PE01", "doc": "contract", "values": CONTRACT_OK}, box)
    assert calls == []
