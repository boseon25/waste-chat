"""2026.11.19 추가 서류: 제6호 배출자 신고서, 제5호 수탁확인(지정), 제14호의4 유해성 정보자료"""
import json

import pytest

from law_agent.guide import guide
from law_agent.review import review_form
from law_agent.review.build_requirements import ROWS
from law_agent.tests.test_review_service import CASE, PROPOSAL

OK6 = {"generator_name": "다온산업", "generator_registration": "123-45-67890", "generator_representative": "김대표",
       "generator_address": "전주시 가상산업로 10", "generator_industry": "플라스틱 제품 제조업",
       "raw_materials_annual": "PE 원료 120톤/년", "products_annual": "포장 필름 100톤/년",
       "manufacturing_process": "원료 투입 → 압출 → 포장(자투리 발생)", "waste_category": "1",
       "waste_legal_name": "폐합성수지류", "waste_classification": "51-03-01", "waste_state_code": "1",
       "monthly_tons": "1.2", "annual_tons": "14.4", "hauler_name": "새만금운수", "haul_amount": "14.4",
       "treatment_type": "위탁", "receiver_name": "미래환경수지", "treatment_method": "2009", "treatment_amount": "14.4"}


def write_box(tmp_path, law_review, status="general"):
    (tmp_path / "PE01_v1_case.json").write_text(json.dumps(CASE), encoding="utf-8")
    (tmp_path / "PE01_v1_proposals.json").write_text(json.dumps([{**PROPOSAL, "waste_status": status}]), encoding="utf-8")
    (tmp_path / "PE01_v1_result.json").write_text(json.dumps({"law_review": law_review}), encoding="utf-8")
    return tmp_path


H1_YES = [{"question_id": "H-1", "answer": "yes", "status": "agreed"}, {"question_id": "A-2", "answer": "no", "status": "agreed"}]


def ids(res):
    return {f["req_id"] for f in res["findings"]}


def test_rows_unique_and_all_new_types():
    assert len({r[0] for r in ROWS}) == len(ROWS)
    assert {r[1] for r in ROWS} >= {"emitter_report", "acceptance_check", "hazard_info"}


def test_form6_complete_passes_with_attachment(tmp_path):
    box = write_box(tmp_path, H1_YES)
    res = review_form({"case_id": "PE01", "doc": "form6", "values": OK6, "attachments": ["form5_processor"]}, box)
    assert not [f for f in res["findings"] if f["severity"] != "info"], res["findings"]
    assert res["verdict"] == "pass" and any("제6호" in n for n in res["notes"])


def test_form6_errors_map_to_fields(tmp_path):
    box = write_box(tmp_path, H1_YES)
    vals = {**OK6, "treatment_type": "외부", "waste_state_code": "고체", "hauler_name": ""}
    vals.pop("manufacturing_process")
    res = review_form({"case_id": "PE01", "doc": "form6", "values": vals}, box)
    f = {x["req_id"]: x for x in res["findings"]}
    assert f["ER-15"]["form_field"] == "treatment_type" and f["ER-11"]["form_field"] == "waste_state_code"
    assert f["ER-14"]["form_field"] == "hauler_name"
    assert f["ER-08"]["kind"] == "form_has_no_field"                      # ⑨ 제조공정: 서식에 아직 없는 칸
    assert "ER-19" not in f                                               # 처리구분이 '위탁'이 아니면 첨부 조건 안 걸림
    assert res["verdict"] == "fix_required"


def test_form6_not_needed_when_h1_no(tmp_path):
    box = write_box(tmp_path, [{"question_id": "H-1", "answer": "no", "status": "agreed"}])
    res = review_form({"case_id": "PE01", "doc": "form6", "values": OK6, "attachments": ["form5_processor"]}, box)
    assert res["verdict"] == "not_applicable"


def test_form6_h1_unknown_check(tmp_path):
    box = write_box(tmp_path, [{"question_id": "H-1", "answer": "unknown", "status": "needs_human"}])
    res = review_form({"case_id": "PE01", "doc": "form6", "values": OK6, "attachments": ["form5_processor"]}, box)
    assert res["verdict"] == "check_required" and any("H-1" in n for n in res["notes"])


def test_acceptance_only_for_designated(tmp_path):
    vals = {"generator": "다온산업", "contractor": "미래환경수지"}
    gen = review_form({"case_id": "PE01", "doc": "designated_acceptance", "values": vals}, write_box(tmp_path, H1_YES))
    assert gen["verdict"] == "not_applicable"


def test_acceptance_designated_flags_missing_section4(tmp_path):
    box = write_box(tmp_path, [{"question_id": "A-2", "answer": "yes", "status": "agreed"}], "designated")
    vals = {"generator": "다온산업", "registration": "1", "representative": "김", "address": "전주",
            "contractor": "미래환경수지", "contractor_reg": "2", "contractor_rep": "이", "contractor_industry": "하수·폐기물 처리업"}
    res = review_form({"case_id": "PE01", "doc": "designated_acceptance", "values": vals}, box)
    f = {x["req_id"]: x for x in res["findings"]}
    assert {"AC-10", "AC-11", "AC-12", "AC-13"} <= set(f)
    assert all(f[k]["kind"] == "form_has_no_field" for k in ("AC-10", "AC-11", "AC-12", "AC-13"))
    assert "AC-16" not in f                                              # 추천 업체 → 경로 일치


def test_hazard_review_and_guide(tmp_path):
    box = write_box(tmp_path, [{"question_id": "A-2", "answer": "yes", "status": "agreed"}], "designated")
    res = review_form({"case_id": "PE01", "doc": "hazard", "values": {"record": "H-001", "company": "다온산업"}}, box)
    f = {x["req_id"]: x for x in res["findings"]}
    assert f["HZ-11"]["kind"] == "form_has_no_field" and f["HZ-02"]["form_field"] == "date"
    g = guide({"case_id": "PE01", "doc": "hazard", "field": "hazard_properties"}, box)
    assert g["kind"] == "need_from" and "추정" in g["need_from"] and g["suggest"] is None
    assert guide({"case_id": "PE01", "doc": "hazard", "field": "hazard_waste_code"}, box)["suggest"]["value"] == "51-03-01"


def test_hazard_general_waste_check_note(tmp_path):
    box = write_box(tmp_path, H1_YES)
    res = review_form({"case_id": "PE01", "doc": "hazard", "values": {}}, box)
    assert any("대상이 아닐 수" in n for n in res["notes"])


@pytest.mark.parametrize("doc,field,kind", [
    ("form6", "waste_category", "need_from"), ("form6", "treatment_type", "suggest_value"),
    ("form6", "hauler_name", "suggest_value"), ("designated_acceptance", "contractor", "suggest_value"),
    ("designated_acceptance", "contractor_reg", "need_from"), ("designated_acceptance", "acceptance_method", "suggest_value"),
    ("hazard", "storage_method", "write_text"), ("hazard", "record", "info")])
def test_guide_kinds_new_forms(tmp_path, doc, field, kind):
    box = write_box(tmp_path, H1_YES)
    g = guide({"case_id": "PE01", "doc": doc, "field": field}, box)
    assert g["kind"] == kind
    assert g["law"], f"{doc}.{field} 에 법 기준이 연결되지 않음"


def test_requirements_merge_active_and_bundled(tmp_path, monkeypatch):
    import csv
    from law_agent.review import rules, service
    rows = [r for r in rules.load_requirements(None) if r["doc_type"] == "entrust_contract"]
    for r in rows:
        r["status"] = "confirmed"
    with (tmp_path / rules.TABLE).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path))
    got = service.requirements()
    assert all(r["status"] == "confirmed" for r in got if r["doc_type"] == "entrust_contract")
    assert any(r["doc_type"] == "emitter_report" for r in got)          # 확정본에 없는 서류는 초안으로
