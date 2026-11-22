# route_agent/src/partners/rank.py 에서 복사 (2026.11.17). 바꾼 점: import 경로, 자료 연도 정렬 추가 (2026.11.18)
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""정렬과 상위 N개. 모든 역할이 같은 규칙을 쓴다.

정렬 순서 (2026-11-14 변경: 시도 우선 제거 → 탄소·거리 기준)
1. 운송 탄소가 적은 순 = 거리가 가까운 순
   (2026.11.18) 앞쪽 후보는 카카오 도로 거리, 나머지는 직선 × 1.3 추정으로 같은 잣대 비교 (road.py)
   한 폐기물 안에서는 물량·차량이 같아서 운송 탄소 순서와 거리 순서가 같다.
   위치를 모르는 업체는 맨 뒤.
2. 허가 업체를 신고 업체보다 앞에 (신고 업체는 신고 범위 확인이 필요)
3. 구매처(end_user)는 확신도 높음 → 중간
4. 이름순 (같은 입력이면 같은 결과)

사업장폐기물은 시도 안에서 처리해야 한다는 규정이 없어서 시도 경계는 순위에 쓰지 않는다.
origin_sido, origin_sigungu 인자는 호출하는 쪽 호환을 위해 남겨 둔다.
"""
from __future__ import annotations

from .models import Location, PartnerCandidate

DEFAULT_TOP_N = 5
CONFIDENCE_ORDER = {"높음": 0, "중간": 1, "낮음": 2}


ROAD_FACTOR = 1.3                               # core/carbon.py 와 같은 값 (직선 → 도로 추정)


def road_or_est(loc: Location | None) -> float:
    """정렬용 도로 거리: 카카오 도로 거리가 있으면 그 값, 없으면 직선 × 1.3. 위치 모르면 inf.
    카카오로 조회한 업체와 못 한 업체를 같은 잣대(도로 km)로 비교하려고 추정값으로 맞춘다."""
    if loc is None:
        return float("inf")
    if getattr(loc, "road_km", None) is not None:
        return loc.road_km
    return loc.distance_km * ROAD_FACTOR if loc.distance_km is not None else float("inf")


def sort_key(c: PartnerCandidate, origin_sido: str = "", origin_sigungu: str = ""):
    loc = c.location or Location(None, None, "없음")
    year = int(c.data_date[:4]) if (c.data_date or "")[:4].isdigit() else 0
    return (
        road_or_est(loc),
        0 if c.permit_type in (None, "허가") else 1,
        -year,                                       # 자료가 최신인 업체 먼저 (폐업·변경 위험, 2026.11.18 law_agent 추가)
        CONFIDENCE_ORDER.get(c.match_confidence or "", 3),
        c.name,
    )


def rank(candidates: list[PartnerCandidate], origin_sido: str = "", origin_sigungu: str = "",
         top_n: int = DEFAULT_TOP_N) -> list[PartnerCandidate]:
    ordered = sorted(candidates, key=sort_key)
    return ordered[:top_n] if top_n else ordered
