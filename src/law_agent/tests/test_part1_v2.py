"""파트 1 패키지 2.0 출력 → 1.0 변환."""
import copy
import json
from pathlib import Path

from law_agent.core import part1_v2 as P

V2 = json.loads((Path(__file__).parent / "fixtures" / "part1_v2_review.json").read_text(encoding="utf-8"))


def fact(v, status="confirmed"):
    return {"value": v, "raw_value": str(v), "status": status}


def test_demo_converts_without_guessing():
    out = P.convert(V2)
    c = out["cases"][0]
    assert out["schema_version"] == "1.0" and c["name"] == "제품 포장용 PE 필름 자투리"
    assert c["classification"]["selected_code"] is None and "classification.selected_code" in c["missing"]
    assert c["quantity"]["monthly_ton"] == [] and c["quantity"]["current_lot"]["value"] == 200.0
    assert c["material_form"] == "파쇄" and c["contamination"]["level"] == "깨끗"
    assert c["hazardous_suspect"] is None and c["region"]["sigungu"] == "전주시"


def test_code_generation_hazard_mapped():
    d = copy.deepcopy(V2)
    c = d["case"]
    c["classification"]["reported_code"] = fact("51-03-01")
    c["generation"] = {"amount": fact("300"), "unit": fact("kg"), "period": fact("week")}
    c["hazard_declaration"] = fact("아니오")
    v1 = P.convert(d)["cases"][0]
    assert v1["classification"]["selected_code"] == "51-03-01"
    assert abs(v1["quantity"]["monthly_ton"][0] - 0.3 * 52 / 12) < 1e-3
    assert v1["hazardous_suspect"] is False


def test_region_with_sido_and_v1_passthrough():
    assert P._region("전북특별자치도 전주시 덕진구 어딘가 1")["sigungu"] == "전주시 덕진구"
    v1 = {"cases": [{"schema_version": "1.0", "case_id": "X"}]}
    assert not P.is_v2(v1) and P.convert(v1) is v1
    assert P.is_v2({"snapshot": V2["case"]})


def test_phase3_export_uses_supported_code_and_monthly_records():
    """파트 1 실제 흐름: classification supported 로 기록한 번호와 입력서 월별 발생량을 읽는다."""
    d = json.loads((Path(__file__).parent / "fixtures" / "part1_phase3_export.json").read_text(encoding="utf-8"))
    c = P.convert(d)["cases"][0]
    assert c["classification"]["selected_code"] == "51-03-01" and c["classification"]["status"] == "part1_supported"
    assert c["quantity"]["monthly_ton"] == [0.18, 0.205, 0.2] and c["quantity"]["monthly_periods"][0] == "2026-08"


def test_minor_component_becomes_foreign_matter():
    d = json.loads((Path(__file__).parent / "fixtures" / "part1_phase3_export.json").read_text(encoding="utf-8"))
    c = P.convert(d)["cases"][0]
    assert [x["material"] for x in c["components"]] == ["PE 필름"]
    assert c["foreign_matter"]["items"] == ["종이 라벨 조각 2%"] and c["mixed_with_other"] is False
