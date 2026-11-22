"""카카오모빌리티 자동차 길찾기로 배출지 → 업체 실제 도로 거리·시간·통행료 (2026.11.18).

  n = enrich(candidates, origin)     # 앞쪽 후보 몇 곳의 location 에 road_km 등을 채운다

- 키: 환경변수 KAKAO_REST_API_KEY (src/law_agent/llm/.env). 값은 출력·기록하지 않는다.
- 키가 없거나, 설정에서 껐거나(road.enabled false), WASTE_NO_ROAD=1 이면 아무것도 안 한다
  → 순위·탄소는 예전처럼 직선 × 1.3 추정.
- 호출이 실패한 업체도 추정값으로 남는다. 인증 오류(401·403)면 이번 실행에서는 더 부르지 않는다.
- 캐시하지 않는다: 카카오 측 답변 "실시간이 아닌 임시 캐싱 등의 방법으로 API 호출 및 이용은 금지"
  (devtalk.kakao.com, 2026). 그래서 실행할 때마다 새로 조회한다.
- 출발점·업체 위치: geocode.py 가 주소를 좌표로 바꿀 수 있으면 그 좌표, 못 바꾸면 업체 좌표·시군구 중심점 (2026.11.20).
"""
from __future__ import annotations

import os
import time
from typing import Iterable

KEY_ENV = "KAKAO_REST_API_KEY"
URL = "https://apis-navi.kakaomobility.com/v1/directions"
SAME_POINT_KM = 0.05                     # 출발·도착이 같은 점이면 조회하지 않음 (카카오가 오류를 냄)
DEFAULTS = {"enabled": True, "shortlist": 10, "timeout_s": 10, "priority": "RECOMMEND"}

STATS = {"calls": 0, "ok": 0, "failed": 0, "seconds": 0.0, "stopped": None}


def settings() -> dict:
    try:
        from law_agent.settings import load_settings
        return {**DEFAULTS, **(load_settings().get("road") or {})}
    except Exception:
        return dict(DEFAULTS)


def enabled() -> bool:
    return bool(os.environ.get(KEY_ENV)) and os.environ.get("WASTE_NO_ROAD") != "1" \
        and bool(settings().get("enabled", True)) and STATS["stopped"] is None


def reset_stats() -> None:
    STATS.update(calls=0, ok=0, failed=0, seconds=0.0, stopped=None)


def fetch(origin: tuple[float, float], dest: tuple[float, float], client=None) -> dict | None:
    """(lat, lng) → (lat, lng). 성공하면 {road_km, duration_min, toll_won}, 실패하면 None."""
    cfg = settings()
    params = {"origin": f"{origin[1]},{origin[0]}", "destination": f"{dest[1]},{dest[0]}",
              "priority": cfg["priority"], "summary": "true", "alternatives": "false"}
    headers = {"Authorization": f"KakaoAK {os.environ.get(KEY_ENV, '')}"}
    t0 = time.time()
    STATS["calls"] += 1
    try:
        if client is None:
            import httpx
            with httpx.Client(timeout=float(cfg["timeout_s"])) as c:
                r = c.get(URL, params=params, headers=headers)
        else:
            r = client.get(URL, params=params, headers=headers)
        if r.status_code in (401, 403):
            STATS["stopped"] = f"인증 오류 {r.status_code} (키 확인)"
            STATS["failed"] += 1
            return None
        if r.status_code != 200:
            STATS["failed"] += 1
            return None
        routes = (r.json() or {}).get("routes") or []
        route = next((x for x in routes if x.get("result_code") == 0), None)
        if not route:
            STATS["failed"] += 1
            return None
        s = route["summary"]
        STATS["ok"] += 1
        return {"road_km": round(s["distance"] / 1000, 1),
                "duration_min": round(s["duration"] / 60),
                "toll_won": int(((s.get("fare") or {}).get("toll")) or 0)}
    except Exception:
        STATS["failed"] += 1
        return None
    finally:
        STATS["seconds"] = round(STATS["seconds"] + time.time() - t0, 2)


def enrich(candidates: Iterable, origin, limit: int | None = None, client=None) -> int:
    """candidates(PartnerCandidate) 앞쪽 limit 곳의 location 에 도로 거리를 채운다. 채운 수."""
    if not enabled() or origin is None or origin.lat is None:
        return 0
    limit = int(settings()["shortlist"]) if limit is None else limit
    n = 0
    for c in list(candidates)[:limit]:
        loc = c.location
        if loc is None or loc.lat is None:
            continue
        if loc.distance_km is not None and loc.distance_km < SAME_POINT_KM:
            continue
        if STATS["stopped"]:
            break
        got = fetch((origin.lat, origin.lng), (loc.lat, loc.lng), client)
        if got:
            loc.road_km, loc.duration_min, loc.toll_won = got["road_km"], got["duration_min"], got["toll_won"]
            loc.road_basis = "카카오 도로"
            n += 1
    return n
