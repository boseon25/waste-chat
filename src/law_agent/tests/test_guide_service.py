"""서류 작성 가이드 — 칸 하나 → 법 문장 + 추천 값 (guide/service.py)"""
import json

import pytest

from law_agent.guide import guide
from law_agent.tests.test_review_service import CASE, PROPOSAL
from law_agent.web import api

CASE2 = {**CASE, "region": {"address": "전주시 가상산업로 10"}, "contamination": {"level": "깨끗"},
         "combustible": True, "storage_state": "지붕 있는 창고"}
PROP2 = {**PROPOSAL, "steps": [{"order": 1, "role": "transporter", "treatment_codes": []},
                                {"order": 2, "role": "preprocessor", "process": "중간 가공 (R-10)",
                                 "treatment_codes": ["2009"]}],
         "partner_candidates": [
             {"company_id": "미래|군산시", "name": "(주)미래환경수지", "role": "preprocessor", "address": "군산시 1",
              "distance_km": {"value": 40.0, "road_km": 52.3}},
             {"company_id": "새만금|군산시", "name": "새만금운수", "role": "transporter", "address": "군산시 2"}]}


@pytest.fixture
def box(tmp_path):
    (tmp_path / "PE01_v1_case.json").write_text(json.dumps(CASE2), encoding="utf-8")
    (tmp_path / "PE01_v1_proposals.json").write_text(json.dumps([PROP2]), encoding="utf-8")
    return tmp_path


def g(box, doc, field):
    return guide({"case_id": "PE01", "doc": doc, "field": field}, box)


def test_code_field_suggests_case_code_with_law(box):
    r = g(box, "form5_c", "waste_classification")
    assert r["kind"] == "suggest_value" and r["suggest"]["value"] == "51-03-01"
    assert r["law"] and all(x["quote"] for x in r["law"])


def test_partner_fields_from_recommendation(box):
    r = g(box, "form5_c", "receiver_name")
    assert r["suggest"]["value"] == "(주)미래환경수지" and "52.3" in r["suggest"]["why"]
    assert g(box, "contract", "receiver_name")["suggest"]["company_id"] == "미래|군산시"
    assert g(box, "dplan", "designated_transporter")["suggest"]["value"] == "새만금운수"


def test_need_from_fields_never_invent(box):
    for f in ("receiver_permit_number", "receiver_registration", "receiver_acceptance", "hauler_vehicle_count"):
        r = g(box, "form5_c" if f.startswith("receiver") else "form5_b", f)
        assert r["kind"] == "need_from" and r["suggest"] is None and r["need_from"], f
    assert "허가증" in g(box, "form5_c", "receiver_permit_number")["need_from"]


def test_quantity_and_method(box):
    assert g(box, "form5_c", "receiver_entrusted_annual_tons")["suggest"]["value"] == pytest.approx(14.52, 0.01)
    assert g(box, "contract", "treatment_method")["suggest"]["value"] == "2009"
    assert g(box, "contract", "contract_quantity")["suggest"]["value"].startswith("월 1.21")
    assert g(box, "form5_c", "waste_state_code")["suggest"]["value"] == "1"


def test_write_text_gives_case_facts(box):
    r = g(box, "contract", "precautions")
    assert r["kind"] == "write_text" and r["facts"]["보관 상태"] == "지붕 있는 창고"
    assert any("취급" in x["quote"] for x in r["law"])


def test_derived_field_law(box):
    r = g(box, "contract", "contract_period")
    assert r["kind"] == "info" and {x["req_id"] for x in r["law"]} >= {"EC-05", "EC-06", "EC-07"}


def test_enum_allowed_values(box):
    r = g(box, "form5_c", "waste_state_code")
    assert any(x.get("allowed") == ["1", "2", "기타"] for x in r["law"])


def test_no_case_gives_law_only(box):
    r = guide({"case_id": "NOPE", "doc": "contract", "field": "receiver_name"}, box)
    assert r["suggest"] is None and r["law"] and any("찾지 못해" in n for n in r["notes"])


def test_api_guide(box, monkeypatch):
    monkeypatch.setenv("WASTE_OUTBOX", str(box))
    st, data = api.handle("POST", "/api/law/guide", json.dumps(
        {"case_id": "PE01", "doc": "form5_c", "field": "receiver_name"}).encode())
    assert st == 200 and data["suggest"]["value"] == "(주)미래환경수지"
    assert api.handle("POST", "/api/law/guide", b'{"doc":"form5_c"}')[0] == 400
    assert api.handle("POST", "/api/law/guide", b'{"doc":"x","field":"y"}')[0] == 400


def test_guide_all_and_cases(box, monkeypatch):
    from law_agent.guide import guide_all
    from law_agent.review import context
    r = guide_all({"case_id": "PE01", "doc": "contract"}, box)
    assert r["case_id"] == "PE01" and "contract_period" in r["fields"] and "receiver_name" in r["fields"]
    assert r["fields"]["receiver_name"]["suggest"]["value"] == "(주)미래환경수지"
    assert context.recent(box)[0]["case_id"] == "PE01"
    monkeypatch.setenv("WASTE_OUTBOX", str(box))
    assert api.handle("GET", "/api/law/cases")[1]["cases"][0]["case_id"] == "PE01"
    st, d = api.handle("POST", "/api/law/guide_all", b'{"doc":"form6"}')
    assert st == 200 and d["fields"]["hauler_name"]["law"]


def test_guide_has_summary_duty_and_sentence(box, monkeypatch):
    from law_agent.guide import service as gs
    monkeypatch.setattr(gs, "sentence", lambda b, q, store=None: "2. " + q + " … 제출합니다.")
    gs._SENT.clear()
    r = g(box, "form5_c", "receiver_registration")
    assert "처리능력" in r["duty"] and r["summary_reviewed"] is False
    x = r["law"][0]
    assert x["summary"].startswith("받는 업체는") and x["sentence"].startswith("2. 수탁자(처리자)는")


def test_changed_sentences(tmp_path):
    import csv, hashlib
    from law_agent.review.build_requirements import changed_sentences
    f = tmp_path / "t.csv"
    with f.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["req_id", "sentence_hash"])
        w.writeheader()
        w.writerow({"req_id": "CC-01", "sentence_hash": hashlib.sha1("옛 문장".encode()).hexdigest()[:12]})
        w.writerow({"req_id": "CC-02", "sentence_hash": hashlib.sha1("같은 문장".encode()).hexdigest()[:12]})
    assert changed_sentences(f, {"CC-01": "새 문장", "CC-02": "같은 문장", "CC-03": "x"}) == ["CC-01"]


class _C:
    def __init__(self, text):
        self.text = text


class _Store:
    def __init__(self, text):
        self.c = _C(text)

    def get(self, law, key):
        return self.c

    def children(self, law, key):
        return []


def test_sentence_list_marker_is_not_end():
    from law_agent.guide import service as gs
    """목록 기호 '다.' 에서 자르지 않는다 (2026.11.19 EC-01: '… 위탁 비용 다.' 로 잘림)"""
    gs._SENT.clear()
    text = ("제16조의13(위탁기준)\n 2. 처리능력을 확인할 것\n 3. 수탁자와 서면으로 다음 각 목의 사항이 포함된 위탁계약을 체결할 것\n"
            "  가. 위탁하는 폐기물의 종류 및 수량\n  나. 계약 기간 및 위탁 비용\n  다. 수탁자의 허가 사항\n"
            " 4. 계약서를 3년간 보관할 것")
    s = gs.sentence("WMA_RULE:16의13/호3@x", "수탁자와 서면으로 다음 각 목의 사항이 포함된", _Store(text))
    assert s.startswith("3. 수탁자와") and s.endswith("다. 수탁자의 허가 사항") and "4." not in s


def test_sentence_form_item_ends_at_real_end():
    from law_agent.guide import service as gs
    gs._SENT.clear()
    text = "작성방법\n 1. 위탁자는 ①란부터 적습니다.\n 2. 수탁자(처리자)는 ⑦란부터 적은 후 위탁자에게 제출합니다.\n 3. 다음"
    s = gs.sentence("WMA_RULE:BF5@x", "수탁자(처리자)는 ⑦란부터", _Store(text))
    assert s == "2. 수탁자(처리자)는 ⑦란부터 적은 후 위탁자에게 제출합니다."
