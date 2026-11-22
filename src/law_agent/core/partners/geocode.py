"""주소 → 좌표 (카카오 로컬 주소 검색, 2026.11.20) — 시군구 중심점 대신 실제 위치로 거리를 재기 위해.

  geocode.refine_origin(origin, address)          # 배출 사업장: 주소가 있으면 그 좌표로 (사례당 1회)
  geocode.refine(candidates, origin, limit=10)    # 업체 좌표가 없는(시군구 중심점) 앞쪽 후보만 주소 좌표로

- 키·켜고 끄기는 road.py 와 같다 (KAKAO_REST_API_KEY, road.enabled, WASTE_NO_ROAD=1 이면 안 함).
- 저장하지 않는다 (카카오: 임시 캐싱 금지). 받은 좌표는 이번 실행의 거리 계산에만 쓰고,
  거리를 다 잰 뒤 forget() 으로 결과에서 위도·경도를 지운다 (basis = "주소 좌표(카카오, 저장 안 함)").
- 못 찾으면(가상 주소·오타) 원래 좌표(시군구 중심점)를 그대로 둔다. 추측하지 않는다.
"""
from __future__ import annotations

import os
import re
import time

from . import road
from .geo import haversine_km

URL_ADDR = "https://dapi.kakao.com/v2/local/search/address.json"
URL_KEYWORD = "https://dapi.kakao.com/v2/local/search/keyword.json"
BASIS = "주소 좌표(카카오, 저장 안 함)"
STATS = {"calls": 0, "ok": 0, "failed": 0, "seconds": 0.0, "stopped": None, "by_name": 0, "missed": []}


def reset_stats() -> None:
    STATS.update(calls=0, ok=0, failed=0, seconds=0.0, stopped=None, by_name=0, missed=[])


def _clean(address: str) -> str:
    """검색을 방해하는 부분을 뗀다: 괄호 설명, 쉼표 뒤 건물 안 위치, 층·호, '외 N필지', '일원', '…단지 내'"""
    a = re.sub(r"\([^)]*\)", " ", address or "")          # "(가상 주소)", "(팔복동)"
    a = re.split(r",", a)[0]                               # "…로 10, A동 자재창고"
    a = re.sub(r"\s외\s*\d+\s*필지.*$", "", a)              # "123번지 외 2필지"
    a = re.sub(r"\s(일원|일대)\s*$", "", a)
    a = re.sub(r"\S+단지\s*내\s*", "", a)                   # "전주1산업단지 내"
    a = re.sub(r"\s\d+\s*층.*$|\s\d+\s*호.*$|\s[A-Za-z가-힣]?\d*동\s*\d+호.*$", "", a)
    a = re.sub(r"(\d+)\s*번지", r"\1", a)
    return re.sub(r"\s+", " ", a).strip()


def _get(url: str, query: str, client=None) -> dict | None:
    headers = {"Authorization": f"KakaoAK {os.environ.get(road.KEY_ENV, '')}"}
    params = {"query": query, "size": 1}
    t0 = time.time()
    STATS["calls"] += 1
    try:
        if client is None:
            import httpx
            with httpx.Client(timeout=float(road.settings()["timeout_s"])) as c:
                r = c.get(url, params=params, headers=headers)
        else:
            r = client.get(url, params=params, headers=headers)
        if r.status_code in (401, 403):
            # 주소 검색(로컬)만 막힌 것 — 길찾기(모빌리티)는 따로 쓸 수 있으니 road 는 멈추지 않는다 (2026.11.20 버그)
            STATS["stopped"] = (f"인증 오류 {r.status_code} — 카카오 개발자 콘솔에서 이 앱의 '카카오맵(로컬)' 사용 설정 확인"
                                if r.status_code == 403 else "인증 오류 401 (키 확인)")
            return None
        if r.status_code != 200:
            return None
        docs = (r.json() or {}).get("documents") or []
        return docs[0] if docs else None
    except Exception:
        return None
    finally:
        STATS["seconds"] = round(STATS["seconds"] + time.time() - t0, 2)


def _gu(sigungu: str) -> str:
    """'전주시 덕진구' → '덕진구', '완주군' → '완주군' (이름 검색 결과가 같은 지역인지 볼 때)"""
    parts = (sigungu or "").split()
    return parts[-1] if parts else ""


def geocode(address: str, sido: str = "", client=None, name: str = "", sigungu: str = "") -> tuple[float, float] | None:
    """주소 → (위도, 경도).
    ① 주소 검색 → ② '시도 + 주소' 키워드 → ③ (업체) '업체명 + 시군구' 키워드 — 결과 주소가 같은 시군구일 때만.
    못 찾으면 None (중심점 유지, missed 에 남김)"""
    if not road.enabled() or STATS["stopped"]:
        return None
    q = _clean(address)
    tries = []
    if len(q) >= 4:
        tries += [(URL_ADDR, q, None), (URL_KEYWORD, f"{sido} {q}".strip(), None)]
    if name and sigungu:
        tries.append((URL_KEYWORD, f"{name} {sigungu}", _gu(sigungu)))
    for url, query, must in tries:
        d = _get(url, query, client)
        if d and d.get("y") and d.get("x"):
            where = (d.get("address_name") or d.get("road_address_name") or "")
            if must and must not in where:
                continue                                  # 이름은 같지만 다른 지역 → 버림
            STATS["ok"] += 1
            STATS["by_name"] += 1 if must else 0
            return float(d["y"]), float(d["x"])
        if STATS["stopped"]:
            break
    STATS["failed"] += 1
    STATS["missed"].append({"name": name or "(배출 사업장)", "address": address or ""})
    return None


def refine_origin(origin, address: str | None, sido: str = "", client=None) -> bool:
    """배출 사업장 좌표를 주소 좌표로. 바꿨으면 True"""
    if origin is None or not address:
        return False
    pt = geocode(address, sido, client)
    if pt is None:
        return False
    origin.lat, origin.lng, origin.basis = pt[0], pt[1], BASIS
    return True


def refine(candidates, origin, limit: int | None = None, client=None) -> int:
    """앞쪽 limit 곳 중 업체 좌표가 없는(중심점) 후보를 주소 좌표로 바꾸고 직선거리를 다시 잰다. 바꾼 수"""
    if not road.enabled() or STATS["stopped"] or origin is None or origin.lat is None:
        return 0
    limit = int(road.settings()["shortlist"]) if limit is None else limit
    n = 0
    for c in list(candidates)[:limit]:
        loc = c.location
        if loc is None or "중심점" not in (loc.basis or "") or not (c.address or c.name):
            continue
        pt = geocode(c.address, c.sido, client, name=c.name, sigungu=c.sigungu)
        if pt is None:
            continue
        loc.lat, loc.lng, loc.basis = pt[0], pt[1], BASIS
        loc.distance_km = haversine_km((origin.lat, origin.lng), pt)
        n += 1
    return n


def forget(candidates, origin=None) -> None:
    """카카오에서 받은 좌표는 결과에 남기지 않는다 (거리·시간만 남김)"""
    for c in candidates:
        loc = getattr(c, "location", None)
        if loc is not None and loc.basis == BASIS:
            loc.lat = loc.lng = None
    if origin is not None and origin.basis == BASIS:
        origin.lat = origin.lng = None


def save_missed(path, case_id: str = "") -> int:
    """못 찾은 주소를 CSV 에 덧붙인다 (사람이 업체 표 주소를 고치도록). 카카오 값은 넣지 않는다. 덧붙인 줄 수"""
    import csv
    from datetime import date
    from pathlib import Path
    rows = STATS.get("missed") or []
    if not rows:
        return 0
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if p.exists():
        with p.open(encoding="utf-8-sig", newline="") as f:
            seen = {(r.get("이름"), r.get("주소")) for r in csv.DictReader(f)}
    new = []
    for r in rows:
        k = (r["name"], r["address"])
        if k not in seen:
            seen.add(k)
            new.append(r)
    if not new:
        return 0
    head = not p.exists()
    with p.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if head:
            w.writerow(["날짜", "사례", "이름", "주소", "고친 주소(사람이 적음)"])
        for r in new:
            w.writerow([date.today().isoformat(), case_id, r["name"], r["address"], ""])
    return len(new)
