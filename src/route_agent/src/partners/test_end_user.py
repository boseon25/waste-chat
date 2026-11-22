"""
순환자원 구매처(end_user) 매칭 테스트 — 관문 1 경로.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<시군구 중심점 폴더> \
  $PY -m pytest -v partners/test_end_user.py
"""
from __future__ import annotations

import pytest

from gates.models import RouteDraft, Status, Step
from partners import by_end_user
from partners.data import PartnerData
from partners.matcher import match_routes


@pytest.fixture(scope="module")
def data():
    return PartnerData.load()


@pytest.fixture(scope="module")
def jeonju(data):
    return data.locator.origin("전북", "전주시")


def gate1_route(code: str, r_codes: list[str]):
    step = Step(2, "순환자원 용도로 사용", "end_user", [], r_codes, [code])
    route = RouteDraft(gate=1, tier=None, route_type="direct_use", title="지정 순환자원 판매",
                       waste_codes=[code], waste_status="general",
                       steps=[Step(1, "수집·운반", "transporter", waste_codes=[code]), step],
                       regulatory=Status.CONFIRMED)
    return route, step


def test_multi_code_rows_are_indexed(data):
    # 표에 "51-29-01;91-05-00"처럼 적힌 행도 각 분류번호로 찾을 수 있어야 함
    assert data.demand_by_code.get("51-29-01") and data.demand_by_code.get("91-05-00")


@pytest.mark.parametrize("code, r_codes", [
    ("51-29-02", ["R-4-1"]),                       # 알루미늄·구리
    ("51-19-00", ["R-4-2", "R-4-5", "R-5-1"]),     # 쌀겨
    ("51-17-25", ["R-4-5", "R-5-1"]),              # 커피찌꺼기
    ("51-28-02", ["R-4-3"]),                       # 폐지
])
def test_only_high_or_medium_and_matching_use(data, jeonju, code, r_codes):
    route, step = gate1_route(code, r_codes)
    found, rejected, questions = by_end_user.match(route, step, data, jeonju)
    assert found
    for c in found:
        assert c.match_confidence in ("높음", "중간")
        assert set(c.r_codes) & set(r_codes)
        assert c.permit_type is None
    assert rejected["확신도 낮음"] > 0
    assert questions and questions[0]["field"] == "purchase_confirm"


def test_use_mismatch_excluded(data, jeonju):
    route, step = gate1_route("51-29-02", ["R-9-9"])          # 없는 용도
    found, rejected, questions = by_end_user.match(route, step, data, jeonju)
    assert not found and rejected["용도 다름"] > 0 and not questions


def test_rank_high_confidence_first_within_same_distance(data, jeonju):
    from partners.rank import rank
    route, step = gate1_route("51-19-00", ["R-4-2", "R-4-5", "R-5-1"])
    found, _, _ = by_end_user.match(route, step, data, jeonju)
    top = rank(found, "전북", "전주시", top_n=0)
    for a, b in zip(top, top[1:]):
        same_place = (a.sido, a.sigungu, a.location.distance_km) == (b.sido, b.sigungu, b.location.distance_km)
        if same_place:
            assert by_end_user.SHOW_CONFIDENCE and ["높음", "중간"].index(a.match_confidence) <= ["높음", "중간"].index(b.match_confidence)


def test_gate1_route_secured(data):
    from preliminary.preliminary_integrator import PreliminaryIntegrator
    import os
    case = PreliminaryIntegrator(os.environ["WASTE_DATA_DIR"]).input_adapter.adapt(
        {"case_id": "T", "classification": {"selected_code": "51-29-02"}, "region": {"sido": "전북", "sigungu": "전주시"}})
    route, _ = gate1_route("51-29-02", ["R-4-1"])
    m = match_routes([route], case, None, data)[0]
    t, e = m.steps
    assert t.optional and not t.candidates                   # 순환자원은 운반 허가 업체를 찾지 않음
    assert e.candidates and m.secured is True
