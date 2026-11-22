"""LLM 보조 (assist/): 초안·질문 — 모델은 가짜로 바꿔 부름. 인용 대조·숫자 검사·need_from 차단"""
import json

import pytest

from law_agent import assist
from law_agent.assist import service
from law_agent.tests.test_guide_service import CASE2, PROP2


@pytest.fixture
def box(tmp_path, monkeypatch):
    (tmp_path / "PE01_v1_case.json").write_text(json.dumps(CASE2), encoding="utf-8")
    (tmp_path / "PE01_v1_proposals.json").write_text(json.dumps([PROP2]), encoding="utf-8")
    monkeypatch.setenv("LAW_AGENT_DATA_DIR", str(tmp_path / "la"))
    return tmp_path


def fake(reply):
    calls = []

    def call(system, user, qid):
        calls.append((qid, user))
        return reply
    return call, calls


def test_draft_keeps_only_real_quotes_and_flags_numbers(box, monkeypatch):
    call, calls = fake({"draft": "가연성이므로 화기를 피하고 지붕 있는 창고에 보관. 3일마다 점검",
                        "uses_quotes": ["다. 위탁하는 폐기물의 성질과 상태 및 취급 시 주의사항", "지어낸 문장"], "missing": ["포장 형태"]})
    monkeypatch.setattr(service, "_call", call)
    r = assist.draft({"case_id": "PE01", "doc": "contract", "field": "precautions"}, box)
    assert r["ok"] and r["uses_quotes"] == ["다. 위탁하는 폐기물의 성질과 상태 및 취급 시 주의사항"]
    assert r["verified"] is False and "3" in r["warnings"][0]                # 사실에 없는 숫자
    assert "지붕 있는 창고" in calls[0][1]                                    # 사실을 넣어 물음
    r2 = assist.draft({"case_id": "PE01", "doc": "contract", "field": "precautions"}, box)   # 저장된 답
    assert len(calls) == 1 and r2["draft"] == r["draft"]


def test_draft_refuses_need_from(box, monkeypatch):
    call, calls = fake({"draft": "x"})
    monkeypatch.setattr(service, "_call", call)
    r = assist.draft({"case_id": "PE01", "doc": "form5_c", "field": "receiver_permit_number"}, box)
    assert r["ok"] is False and "허가증" in r["error"] and not calls


def test_draft_model_failure(box, monkeypatch):
    monkeypatch.setattr(service, "_call", lambda *a: None)
    r = assist.draft({"case_id": "PE01", "doc": "contract", "field": "precautions"}, box)
    assert r["ok"] is False


class Chunk:
    def __init__(self, law, key, title, text):
        self.law, self.key, self.title, self.text = law, key, title, text


SRC = [{"ref": "WMA_RULE:18", "law": "폐기물관리법 시행규칙", "key": "18", "title": "사업장폐기물배출자의 신고",
        "text": "사업개시일 또는 폐기물이 발생한 날부터 1개월 이내에 신고하여야 한다."}]


def test_ask_verified_and_unverified(box, monkeypatch):
    monkeypatch.setattr(service, "_sources", lambda q, d, s=None: SRC)
    call, _ = fake({"answerable": True, "answer": "발생일부터 1개월 안에 신고합니다.",
                    "quotes": [{"ref": "WMA_RULE:18", "quote": "폐기물이 발생한 날부터 1개월 이내에 신고하여야 한다"}]})
    monkeypatch.setattr(service, "_call", call)
    r = assist.ask({"question": "제6호 언제까지 내?", "case_id": "PE01"}, box)
    assert r["verified"] and r["quotes"][0]["law"] == "폐기물관리법 시행규칙"
    call2, _ = fake({"answerable": True, "answer": "7일 안", "quotes": [{"ref": "WMA_RULE:18", "quote": "7일 이내에 신고"}]})
    monkeypatch.setattr(service, "_call", call2)
    r2 = assist.ask({"question": "다른 질문"}, box)
    assert r2["verified"] is False and r2["answer"] is None and r2["sources"]


def test_ask_empty_and_api(box, monkeypatch):
    from law_agent.web import api
    assert assist.ask({"question": ""})["ok"] is False
    monkeypatch.setattr(service, "_sources", lambda q, d, s=None: SRC)
    monkeypatch.setattr(service, "_call", lambda *a: {"answerable": False})
    st, d = api.handle("POST", "/api/law/ask", json.dumps({"question": "x"}).encode())
    assert st == 200 and d["verified"] is False


def test_sources_with_fake_store():
    from law_agent.tests.fake_law import build_store
    st = build_store()
    src = service._sources("폐기물 재활용 기준", None, st)
    assert src and all(s["text"] and s["ref"].count(":") >= 1 for s in src)
    assert sum(len(s["text"]) for s in src) <= service.MAX_CHARS


def test_draft_non_text_field_no_llm(box, monkeypatch):
    call, calls = fake({"draft": "x"})
    monkeypatch.setattr(service, "_call", call)
    r = assist.draft({"case_id": "PE01", "doc": "contract", "field": "receiver_name"}, box)
    assert r["ok"] is False and "(주)미래환경수지" in r["error"] and not calls
    r2 = assist.draft({"case_id": "PE01", "doc": "contract", "field": "pickup_location"}, box)
    assert r2["ok"] is False and not calls
