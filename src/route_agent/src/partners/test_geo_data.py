"""
업체 매칭 1단계 테스트: 시군구 중심점, 위치 찾기, 거리, 표 읽기.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<프로젝트>/data/reference \
  $PY -m pytest -v partners/test_geo_data.py
"""
from __future__ import annotations

import pytest

from partners.data import PartnerData, default_dirs, CENTERS_FILE
from partners.geo import Centers, haversine_km


@pytest.fixture(scope="module")
def data():
    return PartnerData.load()


@pytest.fixture(scope="module")
def centers():
    _, ref = default_dirs()
    return Centers.load(ref / CENTERS_FILE)


# ---------------------------------------------------------------------------
# 시군구 중심점
# ---------------------------------------------------------------------------
def test_center_file_loaded(centers):
    assert len(centers.exact) == 252          # 2025년 2분기 시군구 (구가 있는 시는 구 단위)
    assert len(centers.sido) == 17


def test_city_is_average_of_gu(centers):
    wansan = centers.exact[("전북", "전주시 완산구")]
    deokjin = centers.exact[("전북", "전주시 덕진구")]
    pt, basis = centers.lookup("전북", "전주시")
    assert basis == "시군구 중심점"
    assert pt == pytest.approx(((wansan[0] + deokjin[0]) / 2, (wansan[1] + deokjin[1]) / 2))


def test_gu_only_name_inside_sido(centers):
    assert centers.lookup("전북", "덕진구")[0] == centers.exact[("전북", "전주시 덕진구")]


def test_same_name_different_sido(centers):
    seoul, busan = centers.lookup("서울", "중구")[0], centers.lookup("부산", "중구")[0]
    assert seoul != busan and seoul[0] > 37 and busan[0] < 36
    assert centers.lookup("강원", "고성군")[0] != centers.lookup("경남", "고성군")[0]


def test_long_sido_name(centers):
    assert centers.lookup("전북특별자치도", "군산시") == centers.lookup("전북", "군산시")


def test_sejong(centers):
    for sgg in ("", "세종시", "부강면"):
        pt, basis = centers.lookup("세종", sgg)
        assert pt and basis == "시군구 중심점"


def test_spacing_and_old_names(centers):
    assert centers.lookup("충남", "천안시서북구")[0] == centers.exact[("충남", "천안시 서북구")]
    assert centers.lookup("경기", "여주군")[0] == centers.exact[("경기", "여주시")]
    assert centers.lookup("충북", "청원군")[0] == centers.exact[("충북", "청주시 청원구")]
    assert centers.lookup("충남", "연기군")[0] == centers.sido["세종"]


def test_address_fallback(centers):
    pt, basis = centers.lookup("서울", "", "중구 동호로34길 29, 201호")
    assert basis == "시군구 중심점" and pt == centers.exact[("서울", "중구")]


def test_unknown_sigungu_falls_back_to_sido(centers):
    pt, basis = centers.lookup("경기", "", "공단1대로 244")
    assert basis == "시도 중심점" and pt == centers.sido["경기"]
    assert centers.lookup("", "")[1] == "없음"


def test_haversine():
    assert haversine_km((37.5665, 126.9780), (35.1796, 129.0756)) == pytest.approx(325, abs=5)   # 서울-부산


# ---------------------------------------------------------------------------
# 표 읽기, 업체 위치
# ---------------------------------------------------------------------------
def test_tables_loaded(data):
    assert len(data.recycling) > 10000 and len(data.transport) > 10000 and len(data.disposal) > 800
    assert "51-03" in data.recycling_by_group
    assert data.demand_by_code and data.reporter_scope
    assert len(data.locator.company_coords) > 5000             # 표준데이터와 연결된 업체


def test_origin_jeonju(data):
    o = data.locator.origin("전북", "전주시")
    assert o.basis == "시군구 중심점" and 35.7 < o.lat < 35.9


def test_company_with_coordinates(data):
    o = data.locator.origin("전북", "전주시")
    loc = data.locator.company("미래환경수지|군산시", "전북", "군산시", origin=o)
    assert loc.basis == "업체 좌표"
    assert 25 < loc.distance_km < 45                           # 전주 → 군산 나포면


def test_company_without_coordinates_uses_center(data):
    o = data.locator.origin("전북", "전주시")
    loc = data.locator.company("없는업체|군산시", "전북", "군산시", origin=o)
    assert loc.basis == "시군구 중심점" and loc.distance_km > 20
