"""정렬과 상위 N개. 모든 역할이 같은 규칙을 쓴다.

정렬 순서 (2026-11-14 변경: 시도 우선 제거 → 탄소·거리 기준)
1. 운송 탄소가 적은 순 = 거리가 가까운 순
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


def sort_key(c: PartnerCandidate, origin_sido: str = "", origin_sigungu: str = ""):
    loc = c.location or Location(None, None, "없음")
    return (
        loc.distance_km if loc.distance_km is not None else float("inf"),
        0 if c.permit_type in (None, "허가") else 1,
        CONFIDENCE_ORDER.get(c.match_confidence or "", 3),
        c.name,
    )


def rank(candidates: list[PartnerCandidate], origin_sido: str = "", origin_sigungu: str = "",
         top_n: int = DEFAULT_TOP_N) -> list[PartnerCandidate]:
    ordered = sorted(candidates, key=sort_key)
    return ordered[:top_n] if top_n else ordered
