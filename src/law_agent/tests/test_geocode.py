"""주소 → 좌표 (카카오 로컬) — 실제 호출 없이 가짜 client 로"""
from law_agent.core.partners import geocode, road
from law_agent.core.partners.models import Location, PartnerCandidate


class R:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


class Client:
    def __init__(self, table):
        self.table, self.urls = table, []

    def get(self, url, params=None, headers=None):
        self.urls.append((url, params["query"]))
        hit = self.table.get(params["query"])
        return R(200, {"documents": [hit] if hit else []})


def _on(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "x")
    monkeypatch.delenv("WASTE_NO_ROAD", raising=False)
    road.reset_stats()
    geocode.reset_stats()


def test_clean_drops_notes_and_building():
    assert geocode._clean("전주시 가상산업로 10, A동 자재창고 (가상 주소)") == "전주시 가상산업로 10"


def test_origin_and_candidate_refine_and_forget(monkeypatch):
    _on(monkeypatch)
    cl = Client({"전주시 산업로 10": {"y": "35.8", "x": "127.1"}, "완주군 공단로 5": {"y": "35.9", "x": "127.2"}})
    origin = Location(35.82, 127.14, "시군구 중심점")
    assert geocode.refine_origin(origin, "전주시 산업로 10, A동", "전북", cl) and origin.basis == geocode.BASIS
    c = PartnerCandidate(company_id="k", name="n", role="transporter", sido="전북", sigungu="완주군",
                         address="완주군 공단로 5", phone=None, data_date="2023", source="s",
                         location=Location(35.9, 127.16, "시군구 중심점", distance_km=0.0))
    assert geocode.refine([c], origin, client=cl) == 1 and c.location.basis == geocode.BASIS
    assert c.location.distance_km > 5
    geocode.forget([c], origin)
    assert c.location.lat is None and origin.lat is None and c.location.distance_km > 5


def test_not_found_keeps_center(monkeypatch):
    _on(monkeypatch)
    origin = Location(35.82, 127.14, "시군구 중심점")
    assert not geocode.refine_origin(origin, "없는주소 999", "전북", Client({}))
    assert origin.basis == "시군구 중심점" and origin.lat == 35.82


def test_off_without_key(monkeypatch):
    monkeypatch.delenv("KAKAO_REST_API_KEY", raising=False)
    assert geocode.geocode("전주시 산업로 10", client=Client({})) is None


def test_local_403_does_not_stop_road(monkeypatch):
    _on(monkeypatch)

    class Deny:
        def get(self, url, params=None, headers=None):
            return R(403, {})
    assert geocode.geocode("전주시 산업로 10", client=Deny()) is None
    assert geocode.STATS["stopped"] and road.STATS["stopped"] is None and road.enabled()


def test_clean_more_patterns():
    assert geocode._clean("전주시 덕진구 팔복동 123번지 외 2필지") == "전주시 덕진구 팔복동 123"
    assert geocode._clean("완주군 봉동읍 완주산단로 5 3층 301호") == "완주군 봉동읍 완주산단로 5"


class Docs:
    def __init__(self, table):
        self.table = table

    def get(self, url, params=None, headers=None):
        hit = self.table.get((url.rsplit("/", 1)[-1], params["query"]))
        return R(200, {"documents": [hit] if hit else []})


def test_name_fallback_same_area_only(monkeypatch):
    _on(monkeypatch)
    cl = Docs({("keyword.json", "동양수지 전주시 덕진구"): {"y": "35.86", "x": "127.10", "address_name": "전북 전주시 덕진구 팔복동"}})
    assert geocode.geocode("없는주소 1", "전북", cl, name="동양수지", sigungu="전주시 덕진구") == (35.86, 127.10)
    assert geocode.STATS["by_name"] == 1
    cl2 = Docs({("keyword.json", "동양수지 전주시 덕진구"): {"y": "37.5", "x": "127.0", "address_name": "서울 강남구"}})
    assert geocode.geocode("없는주소 1", "전북", cl2, name="동양수지", sigungu="전주시 덕진구") is None
    assert geocode.STATS["missed"][-1]["name"] == "동양수지"


def test_save_missed(tmp_path, monkeypatch):
    _on(monkeypatch)
    geocode.STATS["missed"] = [{"name": "A", "address": "주소1"}, {"name": "A", "address": "주소1"}]
    f = tmp_path / "m.csv"
    assert geocode.save_missed(f, "C1") == 1
    assert geocode.save_missed(f, "C1") == 0              # 같은 줄은 다시 안 씀
    assert "고친 주소" in f.read_text(encoding="utf-8-sig")
