"""서류 검토 서비스 (waste-chat 칸 이름으로 받기) — review/, web/api.py"""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from law_agent.review import field_map, review_form
from law_agent.web import api

CASE = {"case_id": "PE01", "name": "제품 포장용 PE 필름 자투리",
        "classification": {"selected_code": "51-03-01", "reported_code": "51-03-01"},
        "quantity": {"monthly_ton": [1.15, 1.27, 1.21]}, "material_form": "파쇄"}
PROPOSAL = {"route_id": "PE01-G2-01", "case_id": "PE01", "case_version": 1, "gate": 2,
            "waste_status": "general",
            "steps": [{"order": 1, "role": "transporter", "treatment_codes": []},
                      {"order": 2, "role": "preprocessor", "treatment_codes": ["2009"]}],
            "partner_candidates": [{"company_id": "미래환경수지|군산시", "name": "(주)미래환경수지", "role": "preprocessor"},
                                   {"company_id": "새만금운수|군산시", "name": "새만금운수", "role": "transporter"}]}


@pytest.fixture
def box(tmp_path):
    (tmp_path / "PE01_v1_case.json").write_text(json.dumps({**CASE, "part1_v2": {"case_id": "P1-UUID"}}), encoding="utf-8")
    (tmp_path / "PE01_v1_proposals.json").write_text(json.dumps([PROPOSAL]), encoding="utf-8")
    return tmp_path


def by_req(res):
    return {f["req_id"]: f for f in res["findings"]}


CONTRACT_OK = {
    "generator_name": "다온산업", "receiver_name": "미래환경수지", "hauler_name": "새만금운수",
    "waste_name": "PE 필름 자투리", "waste_state": "고체(파쇄)", "precautions": "비 맞지 않게 보관, 화기 주의",
    "treatment_method": "2009", "contract_period": "2026-12-01 ~ 2027-11-30", "contract_quantity": "월 1.2톤",
    "processing_price": "120원/kg",
}


def test_contract_maps_fields_and_flags_missing_form_fields(box):
    res = review_form({"case_id": "PE01", "doc": "contract", "version": 1, "values": CONTRACT_OK,
                       "attachments": ["form5_processor"]}, box)
    f = by_req(res)
    # 서식에 없는 칸(분류번호·체결일) → form_has_no_field
    assert f["EC-03"]["kind"] == "form_has_no_field" and f["EC-03"]["form_field"] == "waste_classification"
    assert f["EC-11"]["kind"] == "form_has_no_field"
    # 나눠 읽은 칸은 문제가 없어야 함 (기간·수량·비용·첨부)
    for rid in ("EC-05", "EC-06", "EC-07", "EC-04", "EC-08", "EC-12", "EC-16", "EC-14", "EC-15"):
        assert rid not in f, rid
    assert res["verdict"] == "fix_required" and res["doc"] == "contract"
    assert all(x.get("quote") for x in res["findings"] if x["severity"] == "error")


def test_contract_wrong_partner_and_period_back_to_form_field(box):
    vals = {**CONTRACT_OK, "receiver_name": "△△자원", "contract_period": "2027-11-30 ~ 2026-12-01",
            "waste_classification": "51-03-01", "contract_signed_date": "2026-11-20"}
    f = by_req(review_form({"case_id": "PE01", "doc": "contract", "values": vals,
                            "attachments": ["form5_processor"]}, box))
    assert f["EC-14"]["severity"] == "warning" and f["EC-14"]["form_field"] == "receiver_name"
    assert f["EC-07"]["form_field"] == "contract_period"            # 날짜 순서 → 원래 칸
    assert "EC-03" not in f and "EC-11" not in f


def test_contract_missing_precautions_points_to_form_field(box):
    vals = {k: v for k, v in CONTRACT_OK.items() if k != "precautions"}
    f = by_req(review_form({"case_id": "PE01", "doc": "contract", "values": vals}, box))
    assert f["EC-10"]["form_field"] == "precautions" and f["EC-10"]["kind"] == "empty"
    assert f["EC-12"]["kind"] == "attachment"                       # 제5호 첨부 없음


def test_form5_processor_route_checks(box):
    vals = {"generator_name": "다온산업", "generator_registration": "123-45-67890", "generator_representative": "김대표",
            "generator_address": "전주시 가상산업로 10", "waste_classification": "51-03-02", "waste_state_code": "1",
            "receiver_entrusted_annual_tons": "40", "receiver_name": "미래환경수지",
            "receiver_registration": "222-33-44444", "receiver_address": "군산시", "receiver_permit_number": "제2020-1호",
            "receiver_permit_authority": "군산시장", "receiver_acceptance": "가능", "receiver_method": "2009",
            "generator_birthdate": "1970-01-01", "generator_phone": "063-000-0000", "receiver_representative": "이대표",
            "receiver_birthdate": "1965-02-02", "receiver_phone": "063-111-1111", "receiver_industry": "수도, 하수 및 폐기물 처리",
            "receiver_technical_capacity": "기술사 1", "receiver_permit_date": "2020-01-01"}
    res = review_form({"case_id": "PE01", "doc": "form5_c", "values": vals,
                       "attachments": ["permit_processor", "bond"]}, box)
    f = by_req(res)
    assert f["CC-16"]["problem"] == "mismatch" and f["CC-16"]["form_field"] == "waste_classification"
    assert f["CC-19"]["form_field"] == "receiver_entrusted_annual_tons"   # 40 vs 14.5
    assert "CC-14" not in f and "CC-15" not in f and "CC-17" not in f and "CC-18" not in f
    assert res["verdict"] == "check_required"


def test_form5_hauler_skips_guarantee(box):
    res = review_form({"case_id": "PE01", "doc": "form5_b", "values": {"hauler_name": "새만금운수"}}, box)
    assert "CC-15" not in by_req(res)


def test_case_found_by_part1_id_and_missing_case_note(box):
    res = review_form({"case_id": "P1-UUID", "doc": "contract", "values": CONTRACT_OK}, box)
    assert res["case_id"] == "PE01" and res["route_id"] == "PE01-G2-01"
    res2 = review_form({"case_id": "NOPE", "doc": "contract", "values": CONTRACT_OK}, box)
    assert any("찾지 못해" in n for n in res2["notes"]) and "EC-14" not in by_req(res2)


def test_unknown_doc_rejected(box):
    res = review_form({"case_id": "PE01", "doc": "form99", "values": {}}, box)
    assert res["verdict"] == "rejected_request"


@pytest.mark.parametrize("text,expect", [
    ("월 1.2톤", 14.4), ("연 15 t", 15.0), ("1,200 kg/월", 14.4), ("1.2톤", None), ("", None)])
def test_ton_year(text, expect):
    assert field_map._ton_year(text) == [expect]


def test_period_korean():
    assert field_map._period("2026년 12월 1일부터 2027년 11월 30일까지") == ["2026-12-01", "2027-11-30"]


def test_api_handle(box, monkeypatch):
    monkeypatch.setenv("WASTE_OUTBOX", str(box))
    st, data = api.handle("GET", "/api/law/health")
    assert st == 200 and "contract" in data["review_docs"]
    st, data = api.handle("POST", "/api/law/review", json.dumps(
        {"case_id": "PE01", "doc": "contract", "values": CONTRACT_OK}).encode())
    assert st == 200 and data["verdict"] in ("fix_required", "check_required")
    assert api.handle("POST", "/api/law/review", b"not json")[0] == 400
    assert api.handle("POST", "/api/law/review", b'{"doc":"x","values":{}}')[0] == 400
    assert api.handle("GET", "/api/law/nope")[0] == 404


def test_http_server_routes_api(box, monkeypatch):
    monkeypatch.setenv("WASTE_OUTBOX", str(box))
    from law_agent.web.admin import Handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/law/health", timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
        req = urllib.request.Request(base + "/api/law/review", method="POST",
                                     data=json.dumps({"case_id": "PE01", "doc": "contract", "values": CONTRACT_OK}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
            assert r.status == 200 and body["doc"] == "contract"
    finally:
        srv.shutdown()
