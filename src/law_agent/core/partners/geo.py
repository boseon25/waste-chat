# route_agent/src/partners/geo.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""위치와 거리.

- 시군구 중심점: data/reference/sigungu_centers_2025_2Q.csv (통계청 SGIS 행정구역 대표점)
  sig_cd 앞 2자리 = 통계청 시도 코드, sig_nm = "전주시 완산구"처럼 시 + 구
  좌표는 longitude/latitude (행정구역 안에 있는 대표점)를 쓴다. centroid_*는 바다에 찍힐 수 있어 안 씀.
- 업체 좌표: facility_master의 위도·경도 (표준데이터와 연결된 업체만)
- 거리: 직선거리(km). 도로 거리는 나중에.
"""
from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from pathlib import Path

from .models import Location

# 통계청(SGIS) 시도 코드 → 업체 표 시도 표기
SGIS_SIDO = {
    "11": "서울", "21": "부산", "22": "대구", "23": "인천", "24": "광주", "25": "대전", "26": "울산",
    "29": "세종", "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "전북", "36": "전남",
    "37": "경북", "38": "경남", "39": "제주",
}

# 긴 시도 이름이 들어와도 찾을 수 있게 (어댑터가 이미 짧게 바꾸지만 안전장치)
SIDO_ALIAS = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원",
    "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
}


def norm_sido(s: str | None) -> str:
    s = (s or "").strip()
    return SIDO_ALIAS.get(s, s)


# 옛 행정구역 이름 → 2025년 이름 (업체 자료에 옛 이름이 남아 있음)
OLD_SIGUNGU = {
    ("경기", "여주군"): ("경기", "여주시"),
    ("충남", "당진군"): ("충남", "당진시"),
    ("충북", "청원군"): ("충북", "청주시 청원구"),
    ("세종", "청원군"): ("세종", "세종시"),
    ("충남", "연기군"): ("세종", "세종시"),
    ("경기", "포천군"): ("경기", "포천시"),
    ("경기", "양주군"): ("경기", "양주시"),
    ("경기", "화성군"): ("경기", "화성시"),
    ("경기", "김포군"): ("경기", "김포시"),
    ("경기", "광주군"): ("경기", "광주시"),
    ("경기", "이천군"): ("경기", "이천시"),
    ("경기", "안성군"): ("경기", "안성시"),
    ("경북", "군위군"): ("대구", "군위군"),
    ("경남", "마산시"): ("경남", "창원시 마산합포구"),
    ("경남", "진해시"): ("경남", "창원시 진해구"),
}


def _split_city_gu(sgg: str) -> str:
    """'천안시서북구' → '천안시 서북구' (띄어쓰기 빠진 표기)"""
    m = re.fullmatch(r"(.+?시)(.+?구)", sgg)
    return f"{m.group(1)} {m.group(2)}" if m and " " not in sgg else sgg


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lng1), (lat2, lng2) = a, b
    p = math.pi / 180
    h = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return round(2 * 6371.0 * math.asin(math.sqrt(h)), 1)


def _avg(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


class Centers:
    """
    (시도, 시군구) → 좌표. 찾는 순서:
      1. (시도, "전주시 덕진구") 그대로
      2. (시도, "전주시")       — 구 행들의 평균
      3. (시도, "덕진구")       — 그 시도 안에서 그 이름이 하나뿐일 때
      4. 시도 중심            — 그 시도 행 전체 평균
    """

    def __init__(self, rows: list[dict]):
        self.exact: dict[tuple[str, str], tuple[float, float]] = {}
        city_pts: dict[tuple[str, str], list] = defaultdict(list)
        gu_pts: dict[tuple[str, str], list] = defaultdict(list)
        sido_pts: dict[str, list] = defaultdict(list)

        for r in rows:
            code = (r.get("sig_cd") or "").strip()
            sido = SGIS_SIDO.get(code[:2])
            if not sido:
                continue
            name = (r.get("sig_nm") or "").strip()
            pt = (float(r["latitude"]), float(r["longitude"]))
            self.exact[(sido, name)] = pt
            sido_pts[sido].append(pt)
            parts = name.split()
            if len(parts) == 2:                      # "전주시 완산구"
                city_pts[(sido, parts[0])].append(pt)
                gu_pts[(sido, parts[1])].append(pt)

        self.city = {k: _avg(v) for k, v in city_pts.items()}
        self.gu = {k: v[0] for k, v in gu_pts.items() if len(v) == 1}     # 시도 안에서 유일한 구 이름만
        self.sido = {k: _avg(v) for k, v in sido_pts.items()}
        if "세종" in self.sido:                                            # 세종은 시군구 칸이 비어 있는 경우가 많음
            self.city.setdefault(("세종", "세종시"), self.sido["세종"])

    @classmethod
    def load(cls, path: str | Path) -> "Centers":
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            return cls(list(csv.DictReader(f)))

    def _find_sgg(self, sido: str, sgg: str) -> tuple[float, float] | None:
        sgg = _split_city_gu(" ".join(sgg.split()))
        sido, sgg = OLD_SIGUNGU.get((sido, sgg), (sido, sgg))
        if (sido, sgg) in self.exact:
            return self.exact[(sido, sgg)]
        first = sgg.split()[0]
        sido2, first = OLD_SIGUNGU.get((sido, first), (sido, first))
        if (sido2, first) in self.exact:                         # "여주시" 등 구 없는 시
            return self.exact[(sido2, first)]
        if (sido2, first) in self.city:                          # "전주시" → 완산구·덕진구 평균
            return self.city[(sido2, first)]
        if (sido, sgg) in self.gu:                               # "덕진구" → 전북 안에서 유일
            return self.gu[(sido, sgg)]
        return None

    def lookup(self, sido: str | None, sigungu: str | None,
               address: str | None = None) -> tuple[tuple[float, float] | None, str]:
        """반환: (좌표, 근거) — 근거 = 시군구 중심점 / 시도 중심점 / 없음
        시군구가 비어 있으면 주소 앞부분의 시·군·구 이름으로 한 번 더 찾는다 ("중구 동호로 …")."""
        sido = norm_sido(sido)
        if sido == "세종":                                       # 세종은 시군구가 하나
            return self.sido.get("세종"), "시군구 중심점"
        if sigungu and sigungu.strip():
            pt = self._find_sgg(sido, sigungu)
            if pt:
                return pt, "시군구 중심점"
        for tok in (address or "").split()[:2]:
            tok = norm_sido(tok)
            if tok in self.sido or not re.search(r"(시|군|구)$", tok):
                continue
            pt = self._find_sgg(sido, tok)
            if pt:
                return pt, "시군구 중심점"
        if sido in self.sido:
            return self.sido[sido], "시도 중심점"
        return None, "없음"


class Locator:
    """업체·배출 사업장 위치와 거리."""

    def __init__(self, centers: Centers, company_coords: dict[str, tuple[float, float]]):
        self.centers = centers
        self.company_coords = company_coords          # 업체_키 → (위도, 경도) (facility_master)

    def origin(self, sido: str | None, sigungu: str | None, address: str | None = None) -> Location:
        """배출 사업장 위치 (파트 1 region). 주소 좌표가 없으니 시군구 중심점."""
        pt, basis = self.centers.lookup(sido, sigungu, address)
        return Location(pt[0] if pt else None, pt[1] if pt else None, basis)

    def company(self, company_id: str, sido: str, sigungu: str, address: str = "",
                origin: Location | None = None) -> Location:
        pt = self.company_coords.get(company_id)
        basis = "업체 좌표"
        if pt is None:
            pt, basis = self.centers.lookup(sido, sigungu, address)
        loc = Location(pt[0] if pt else None, pt[1] if pt else None, basis)
        if origin and origin.lat is not None and loc.lat is not None:
            loc.distance_km = haversine_km((origin.lat, origin.lng), (loc.lat, loc.lng))
        return loc
