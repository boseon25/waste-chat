"""업체 통과 근거·주의 (core/partners/evidence.py, 2026.11.20)"""
from law_agent.core.partners import evidence
from law_agent.core.partners.models import Location, PartnerCandidate


def _c(role, **kw):
    base = dict(company_id="k", name="n", role=role, sido="전북", sigungu="전주시", address="", phone=None,
                data_date="2023", source="x.xlsx#1")
    base.update(kw)
    return PartnerCandidate(**base)


def test_transporter_only_off_site_caution():
    c = _c("transporter", waste_group="사업장일반", scope_text="폐합성수지류, 폐종이류; 비배출시설계",
           capacity={"보유차량": "3"})
    evidence.transporter(c, ["51-03-01"], [False], {"monthly_ton": [0.2]})
    items = {e["item"]: e["value"] for e in c.evidence}
    assert items["영업 상태"] == "영업 중" and "폐합성수지류" in items["영업 대상 (자료 원문)"] and "보유차량 3" in items["운반 능력"]
    assert any("비배출시설계 폐기물만 운반" in x for x in c.cautions)
    assert any("영업 구역" in x for x in c.cautions) and any("2023년" in x for x in c.cautions)


def test_transporter_both_categories_no_caution():
    c = _c("transporter", waste_group="사업장일반", scope_text="폐합성수지류; 배출시설계·비배출시설계")
    evidence.transporter(c, ["51-03-01"], [False], {})
    assert not any("배출시설계 폐기물만" in x for x in c.cautions)


def test_recycler_reporter_and_quantity_caution():
    c = _c("preprocessor", permit_type="신고", waste_group="51-03 폐합성수지류", treatment_codes=["2010"],
           r_codes=["R-10"], registration_type="중간재활용업")
    evidence.recycler(c, ["51-03-01"], [False], {"monthly_ton": [0.18, 0.205, 0.2]})
    assert any(e["law"] == "폐기물관리법 제18조 제1항" for e in c.evidence)
    assert any("제46조" in x for x in c.cautions) and any("월 0.195톤" in x for x in c.cautions)


def test_distance_text():
    assert evidence.distance_text(Location(1, 1, "업체 좌표", road_km=5.84)) == "5.8 km (도로)"
    assert "추정 불가" in evidence.distance_text(Location(1, 1, "시군구 중심점", distance_km=0.0))
    assert evidence.distance_text({"distance_km": 10, "basis": "업체 좌표"}) == "약 13 km (직선 × 1.3 추정)"


def test_liquid_waste_vehicle_caution_and_zero_capacity_hidden():
    c = _c("transporter", waste_group="사업장일반", scope_text="폐유기용제; 배출시설계·비배출시설계",
           capacity={"보유차량": "0", "2023_운반량_톤": "120"})
    evidence.transporter(c, ["04-02-00"], [True], {}, None, "액상")
    items = {e["item"]: e["value"] for e in c.evidence}
    assert "보유차량" not in items.get("운반 능력", "") and "2023_운반량_톤 120" in items["운반 능력"]
    assert any("탱크로리" in x for x in c.cautions)


def test_solid_no_vehicle_info_caution():
    c = _c("transporter", waste_group="사업장일반", scope_text="", capacity={"보유차량": "0"})
    evidence.transporter(c, ["51-03-01"], [False], {}, None, "필름 조각")
    assert any("보유 차량 수·종류가 자료에 없습니다" in x for x in c.cautions)
