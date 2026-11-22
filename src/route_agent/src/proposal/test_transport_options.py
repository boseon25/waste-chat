"""가공업체마다 운송 업체 추천 (2026-11-17): 빈 차 거리 = 운송→배출지 + 가공업체→운송"""
from proposal.route_proposal import transport_options


def _c(cid, lat, lng, dist):
    return {"company_id": cid, "name": cid, "location": {"lat": lat, "lng": lng, "basis": "업체 좌표"},
            "distance_km": {"value": dist} if dist is not None else None}


def test_options_depend_on_destination():
    near_origin = _c("T-near-origin", 35.80, 127.10, 2.0)
    near_dest = _c("T-near-dest", 35.95, 126.70, 30.0)
    steps = [{"role": "transporter", "optional": False, "candidates": [near_origin, near_dest], "reserve": []}]
    a = _c("A", 35.81, 127.11, 1.5)                 # 배출지 근처 가공업체
    b = _c("B", 35.96, 126.71, 35.0)                # 멀리 있는 가공업체 (운송 업체 하나가 그 근처)
    transport_options([a, b], steps)
    assert a["transport_options"][0]["company_id"] == "T-near-origin"
    assert b["transport_options"][0]["company_id"] == "T-near-dest"     # 돌아가는 길이 짧음
    o = b["transport_options"][0]
    assert o["empty_km"] == round(o["to_origin_km"] + o["from_destination_km"], 1)


def test_no_coordinates_falls_back_to_origin_distance():
    t = {"company_id": "T", "name": "T", "location": None, "distance_km": {"value": 5.0}}
    a = _c("A", 35.8, 127.1, 1.0)
    transport_options([a], [{"role": "transporter", "optional": False, "candidates": [t]}])
    o = a["transport_options"][0]
    assert o["from_destination_km"] is None and o["empty_km"] == 5.0 and "좌표 없음" in o["basis"]


def test_optional_transport_step_gives_no_options():
    a = _c("A", 35.8, 127.1, 1.0)
    transport_options([a], [{"role": "transporter", "optional": True, "candidates": [_c("T", 35.8, 127.1, 1.0)]}])
    assert a["transport_options"] == []
