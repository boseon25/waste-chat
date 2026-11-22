"""판정표 읽기. 한 번 읽어서 관문 1~4가 같이 쓴다."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path


def default_dir() -> Path:
    """WASTE_DATA_DIR(= data/tables/active)를 쓴다. 사전검증과 같은 폴더."""
    env = os.environ.get("WASTE_DATA_DIR")
    if not env:
        raise RuntimeError("WASTE_DATA_DIR 환경변수에 data/tables/active 경로를 지정하세요.")
    return Path(env)


def _read(d: Path, name: str) -> list[dict]:
    with (d / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def split(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(";") if x.strip()]


@dataclass
class Tables:
    designated: list[dict] = field(default_factory=list)      # designated_resources
    types: dict[str, list[dict]] = field(default_factory=dict)  # recycling_types_4_3 (분류번호별)
    rcode: dict[str, dict] = field(default_factory=dict)      # rcode_detail_4_2
    tcode: dict[str, dict] = field(default_factory=dict)      # treatment_code_map
    rcode_to_tcode: dict[str, str] = field(default_factory=dict)  # "R-3" → "2003" (위탁처리)
    not_in_4_3: set[str] = field(default_factory=set)
    gate3: list[dict] = field(default_factory=list)
    disposal: list[dict] = field(default_factory=list)
    waste_names: dict[str, str] = field(default_factory=dict)
    designated_codes: set[str] = field(default_factory=set)   # waste_codes_4 지정폐기물 = Y

    @classmethod
    def load(cls, d: Path | str | None = None) -> "Tables":
        d = Path(d) if d else default_dir()
        t = cls()
        t.designated = _read(d, "designated_resources.csv")
        for r in _read(d, "recycling_types_4_3.csv"):
            t.types.setdefault(r["분류번호"], []).append(r)
        t.rcode = {r["r_code"]: r for r in _read(d, "rcode_detail_4_2.csv")}
        for r in _read(d, "treatment_code_map.csv"):
            t.tcode[r["code"]] = r
            if r["처리구분"] == "위탁처리" and r["r_code"]:
                t.rcode_to_tcode[r["r_code"]] = r["code"]
        t.not_in_4_3 = {r["분류번호"] for r in _read(d, "not_in_4_3.csv")}
        t.gate3 = _read(d, "gate3_rules.csv")
        t.disposal = _read(d, "disposal_methods_5.csv")
        codes = _read(d, "waste_codes_4.csv")
        t.waste_names = {r["분류번호"]: r["명칭"] for r in codes}
        t.designated_codes = {r["분류번호"] for r in codes if r["지정폐기물"].strip().upper() == "Y"}
        return t

    # ---- 도움 함수 ----
    def top_rcode(self, rc: str) -> str:
        """R-3-3 → R-3"""
        parts = rc.split("-")
        return "-".join(parts[:2]) if len(parts) >= 2 else rc

    def rcode_desc(self, rc: str) -> str:
        return (self.rcode.get(rc) or {}).get("설명", "")

    def tier_of(self, rc: str) -> str | None:
        row = self.rcode.get(rc) or self.rcode.get(self.top_rcode(rc))
        return (row or {}).get("tier") or None

    def treatment_code_of(self, rc: str) -> str | None:
        return self.rcode_to_tcode.get(self.top_rcode(rc))

    def tcode_name(self, code: str) -> str:
        return (self.tcode.get(code) or {}).get("세분류", code)
