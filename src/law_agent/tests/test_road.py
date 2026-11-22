"""카카오 도로 거리 (core/partners/road.py). 실제 호출·키 없이 가짜 응답으로."""
import pytest

from law_agent.core.carbon import transport
from law_agent.core.partners import road
from law_agent.core.partners.models import Location, PartnerCandidate
from law_agent.core.partners.rank import rank
from law_agent.core.proposal.route_proposal import road_km, road_text


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


class FakeClient:
    def __init__(self, table):
        self.table, self.calls = table, []

    def get(self, url, params, headers):
        self.calls.append(params["destination"])
        assert headers["Authorization"].startswith("KakaoAK ")
        return self.table.get(params["destination"], Resp(500))


def ok(m, s, toll=0):
    return Resp(200, {"routes": [{"result_code": 0, "summary": {"distance": m, "duration": s, "fare": {"toll": toll}}}]})


def cand(name, lat, lng, dist):
    c = PartnerCandidate(company_id=name, name=name, role="preprocessor", sido="", sigungu="", address="",
                         phone=None, data_date="2025", source="", permit_type="허가")
    c.location = Location(lat, lng, "업체 좌표", dist)
    return c


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(road.KEY_ENV, "test-key")
    monkeypatch.delenv("WASTE_NO_ROAD", raising=False)
    road.reset_stats()


ORIGIN = Location(35.8, 127.1, "시군구 중심점")


def test_enrich_fills_and_reranks():
    a, b = cand("A", 35.9, 127.2, 10.0), cand("B", 36.0, 127.3, 12.0)   # 직선은 A가 가까움
    fc = FakeClient({"127.2,35.9": ok(40000, 3000), "127.3,36.0": ok(15000, 1200, 1800)})   # 도로는 B가 가까움
    assert road.enrich(rank([a, b], top_n=0), ORIGIN, client=fc) == 2
    assert (b.location.road_km, b.location.duration_min, b.location.toll_won) == (15.0, 20, 1800)
    assert b.location.road_basis == "카카오 도로"
    assert [c.name for c in rank([a, b], top_n=0)] == ["B", "A"]


def test_shortlist_limit_and_mixed_comparison():
    cs = [cand(f"C{i}", 36 + i / 10, 127 + i / 10, 5.0 + i) for i in range(4)]
    fc = FakeClient({f"{127 + i / 10},{36 + i / 10}": ok(int((5.0 + i) * 1000 * 1.6), 600) for i in range(4)})
    assert road.enrich(cs, ORIGIN, limit=2, client=fc) == 2
    assert len(fc.calls) == 2 and cs[2].location.road_km is None
    # C2 (직선 7km → 추정 9.1km) 가 C1 (카카오 9.6km) 보다 앞: 같은 '도로 km' 잣대로 비교
    assert [c.name for c in rank(cs, top_n=0)][:3] == ["C0", "C2", "C1"]


def test_no_key_does_nothing(monkeypatch):
    monkeypatch.delenv(road.KEY_ENV)
    a = cand("A", 35.9, 127.2, 10.0)
    assert road.enrich([a], ORIGIN, client=FakeClient({})) == 0 and a.location.road_km is None


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("WASTE_NO_ROAD", "1")
    assert road.enrich([cand("A", 35.9, 127.2, 10.0)], ORIGIN, client=FakeClient({})) == 0


def test_auth_error_stops_further_calls():
    cs = [cand("A", 35.9, 127.2, 10.0), cand("B", 36.0, 127.3, 12.0)]
    fc = FakeClient({"127.2,35.9": Resp(401)})
    assert road.enrich(cs, ORIGIN, client=fc) == 0
    assert len(fc.calls) == 1 and "401" in road.STATS["stopped"]


def test_failure_keeps_estimate_and_same_point_skipped():
    same = cand("S", 35.8, 127.1, 0.0)
    bad = cand("X", 35.9, 127.2, 10.0)
    fc = FakeClient({})
    road.enrich([same, bad], ORIGIN, client=fc)
    assert fc.calls == ["127.2,35.9"] and bad.location.road_km is None and road.STATS["failed"] == 1


def test_carbon_and_text_use_road():
    from types import SimpleNamespace
    f = SimpleNamespace(transport={"트럭": {"계수": 0.2}}, source=lambda r: "s")
    t = transport(10.0, 2.0, "트럭", f, road_km=20.0)
    assert t["road_km"] == 20.0 and "카카오 도로" in t["basis"] and t["transport_kgco2e"] == 8.0
    t2 = transport(10.0, 2.0, "트럭", f)
    assert t2["road_km"] == 13.0 and "추정" in t2["basis"]
    c = {"distance_km": {"value": 10.0, "road_est": 20.0, "road_km": 20.0, "duration_min": 25, "toll_won": 0}}
    assert road_km(c) == 20.0 and road_text(c) == "도로 20.0km · 25분 · 통행료 0원 (카카오)"
    assert road_text({"distance_km": {"value": 10.0, "road_est": 13.0}}) == "약 13.0km (직선 × 1.3 추정)"
