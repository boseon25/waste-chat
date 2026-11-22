"""업체 매칭에 쓰는 표 읽기. 한 번 읽어서 역할별 매칭이 같이 쓴다.

경로
- 업체 표:      WASTE_DATA_DIR (= data/tables/active)
- 시군구 중심점: WASTE_REFERENCE_DIR (기본 = data/reference)
                active는 promote 때 통째로 바뀌므로 고정 참조 자료는 따로 둔다.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .geo import Centers, Locator

CENTERS_FILE = "sigungu_centers_2025_2Q.csv"


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def default_dirs() -> tuple[Path, Path]:
    tables = os.environ.get("WASTE_DATA_DIR")
    if not tables:
        raise RuntimeError("WASTE_DATA_DIR 환경변수에 data/tables/active 경로를 지정하세요.")
    tables = Path(tables)
    ref = os.environ.get("WASTE_REFERENCE_DIR")
    reference = Path(ref) if ref else tables.parents[1] / "reference"     # data/tables/active → data/reference
    return tables, reference


@dataclass
class PartnerData:
    recycling: list[dict] = field(default_factory=list)                   # partners_recycling
    recycling_by_group: dict[str, list[dict]] = field(default_factory=dict)   # 분류번호_중분류 → 행
    recycling_by_company: dict[str, list[dict]] = field(default_factory=dict)  # 업체_키 → 행 (업체 전체 허가 범위)
    transport: list[dict] = field(default_factory=list)                   # partners_transport
    disposal: list[dict] = field(default_factory=list)                    # partners_disposal
    demand_by_code: dict[str, list[dict]] = field(default_factory=dict)   # demand_designated 분류번호 → 행
    reporter_scope: list[dict] = field(default_factory=list)
    locator: Locator | None = None
    company_names: dict[str, str] = field(default_factory=dict)           # 업체_키 → 업체명 (통합표)

    @classmethod
    def load(cls, tables_dir: str | Path | None = None, reference_dir: str | Path | None = None) -> "PartnerData":
        if tables_dir is None or reference_dir is None:
            t, r = default_dirs()
            tables_dir, reference_dir = tables_dir or t, reference_dir or r
        tables_dir, reference_dir = Path(tables_dir), Path(reference_dir)

        d = cls()
        d.recycling = _read(tables_dir / "partners_recycling.csv")
        by_group, by_company = defaultdict(list), defaultdict(list)
        for r in d.recycling:
            by_group[r["분류번호_중분류"]].append(r)
            by_company[r["업체_키"]].append(r)
        d.recycling_by_group = dict(by_group)
        d.recycling_by_company = dict(by_company)

        d.transport = _read(tables_dir / "partners_transport.csv")
        d.disposal = _read(tables_dir / "partners_disposal.csv")

        by_code = defaultdict(list)
        for r in _read(tables_dir / "demand_designated.csv"):
            for code in r["분류번호"].split(";"):          # "51-29-01;91-05-00" 처럼 여러 개
                by_code[code.strip()].append(r)
        d.demand_by_code = dict(by_code)

        d.reporter_scope = _read(tables_dir / "reporter_scope.csv")

        coords = {}
        for r in _read(tables_dir / "facility_master.csv"):
            d.company_names[r["업체_키"]] = r["업체명"]
            if r.get("위도") and r.get("경도"):
                try:
                    coords[r["업체_키"]] = (float(r["위도"]), float(r["경도"]))
                except ValueError:
                    pass
        centers_path = reference_dir / CENTERS_FILE
        if not centers_path.exists():
            raise FileNotFoundError(
                f"시군구 중심점 파일이 없습니다: {centers_path}\n"
                "  WASTE_REFERENCE_DIR 환경변수에 sigungu_centers 파일이 있는 폴더를 지정하세요. "
                "업체 매칭 없이 돌리려면 pipeline.py --no-partners")
        d.locator = Locator(Centers.load(centers_path), coords)
        return d
