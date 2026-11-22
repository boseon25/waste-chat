"""주소를 좌표로 바꾼다. OpenStreetMap Nominatim을 쓰며, 국내 도로명주소는 보통 건물이 아니라 도로·동 단위까지 찾는다."""
import json
import re
import threading
import time
from urllib import error, parse, request

ENDPOINT = "https://nominatim.openstreetmap.org/search"
LOCK = threading.Lock()
CACHE = {}
_last = [0.0]
ROAD_KINDS = {"building", "house", "amenity", "shop", "office", "industrial", "commercial", "place"}


def _ask(query):
    url = ENDPOINT + "?" + parse.urlencode({"q": query, "format": "jsonv2", "countrycodes": "kr", "limit": 1,
                                             "accept-language": "ko"})
    req = request.Request(url, headers={"User-Agent": "waste-chat-local/1.0 (local prototype)"})
    wait = 1.1 - (time.time() - _last[0])  # 이용 정책: 1초에 한 번 이하
    if wait > 0:
        time.sleep(wait)
    try:
        with request.urlopen(req, timeout=12) as response:
            found = json.loads(response.read(200000))
    finally:
        _last[0] = time.time()
    return found[0] if isinstance(found, list) and found else None


def lookup(address):
    """(결과 dict 또는 None). 번지까지 못 찾으면 번지, 도로명을 차례로 떼고 다시 찾는다."""
    address = re.sub(r"\s+", " ", address).strip()
    if len(address) < 4:
        return None
    if address in CACHE:
        return CACHE[address]
    tries = [address]
    no_number = re.sub(r"\s+\d[\d-]*(?:번지|호)?(?:\s.*)?$", "", address)
    if no_number != address:
        tries.append(no_number)
    parts = no_number.split(" ")
    if len(parts) > 2:
        tries.append(" ".join(parts[:-1]))
    result = None
    with LOCK:
        try:
            for index, query in enumerate(tries):
                hit = _ask(query)
                if hit:
                    exact = index == 0 and (hit.get("addresstype") in ROAD_KINDS or hit.get("category") == "building")
                    result = {"lat": round(float(hit["lat"]), 5), "lng": round(float(hit["lon"]), 5),
                              "label": str(hit.get("display_name", ""))[:120], "exact": exact}
                    break
        except (error.URLError, TimeoutError, OSError, ValueError, KeyError):
            raise ValueError("주소 검색 서비스에 연결하지 못했습니다. 지도를 눌러 위치를 표시하세요.") from None
    CACHE[address] = result
    return result
