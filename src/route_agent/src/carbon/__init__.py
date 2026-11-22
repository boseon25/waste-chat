"""탄소 계산 — 업체 후보마다 운송 탄소, 경로마다 처리 탄소(참고용).

계수는 파일에서 읽는다 (코드에 숫자를 적지 않음):
  WASTE_CARBON_FILE (파일 경로) → 없으면 WASTE_REFERENCE_DIR/탄소_배출계수_v1.csv
  → 없으면 WASTE_DATA_DIR 기준 data/reference/탄소_배출계수_v1.csv
계수 표를 새 판으로 바꾸면 결과도 따라 바뀐다. 필요한 항목이 빠져 있으면 읽을 때 오류.

정한 규칙 (2026-11-14)
- 업체마다 운송 탄소 = 월 물량(톤) × 도로 거리(km) × 운송 계수
    도로 거리 = 직선거리 × 1.3 (추정 우회율). 직선 1km 미만(같은 중심점)이면 최소 5km
    운송 계수 = 계수 표의 트럭, 액상이면 탱크로리 (kgCO2eq/ton·km)
    월 물량   = 파트 1 quantity.monthly_ton 평균. 없으면 1톤 기준
- 운송 거리는 배출지 → 가공·처분·구매 업체 한 구간. 운송업체 차고지 거리는 넣지 않는다.
- 처리 탄소는 참고로만 붙인다. 계수 표의 재활용 값에 원료 대체 효과가 없어서
  (예: 폐플라스틱 재활용 0.019 > 매립 0.013) 경로끼리 숫자로 비교하면 잘못된 결론이 나온다.
  경로 순서는 법 우선순위(재활용 → 에너지 회수 → 처분)를 따른다.
- 처리 계수는 전과정(LCI) 행만 쓴다. 국가 인벤토리 직접배출 행은 범위가 달라 섞지 않는다.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

CARBON_FILE = "탄소_배출계수_v1.csv"
ROAD_FACTOR = 1.3
MIN_ROAD_KM = 5.0
TREATMENT_SCOPE = "LCI 전과정"
TREATMENT_NOTE = "처리 공정 배출만, 원료 대체 효과 미포함 — 경로끼리 비교하지 말 것"

# 설계값: 분류번호 → 계수 표의 재질 이름 (긴 접두어 우선). 지정폐기물(01~4x)은 따로.
MATERIAL_BY_CODE = {
    "51-03": "혼합폐플라스틱", "51-28": "폐지", "51-20": "폐목", "51-30": "폐유리",
    "51-29-01": "폐철금속", "51-29-03": "폐철금속", "51-29-02": "폐비철금속",
}
# 설계값: (처리 방법, 재질) → 계수 표의 항목 이름. 여기 없는 조합은 "{재질} {방법}"
ITEM_NAME = {
    ("소각", "지정폐기물"): "지정폐기물 소각",
    ("매립", "지정폐기물"): "유해폐기물 매립",
    ("소각", "폐철금속"): "폐금속 소각", ("매립", "폐철금속"): "폐금속 매립",
    ("소각", "폐비철금속"): "폐금속 소각", ("매립", "폐비철금속"): "폐금속 매립",
}
VEHICLES = ("트럭", "탱크로리")


def default_path() -> Path:
    if os.environ.get("WASTE_CARBON_FILE"):
        return Path(os.environ["WASTE_CARBON_FILE"])
    if os.environ.get("WASTE_REFERENCE_DIR"):
        return Path(os.environ["WASTE_REFERENCE_DIR"]) / CARBON_FILE
    if os.environ.get("WASTE_DATA_DIR"):
        return Path(os.environ["WASTE_DATA_DIR"]).parents[1] / "reference" / CARBON_FILE
    raise RuntimeError("탄소 계수 파일 위치를 알 수 없습니다. WASTE_REFERENCE_DIR 또는 WASTE_CARBON_FILE을 지정하세요.")


@dataclass
class Factors:
    path: Path
    transport: dict[str, dict] = field(default_factory=dict)     # 수단 → 행
    treatment: dict[tuple, dict] = field(default_factory=dict)   # (방법, 항목) → 행 (전과정만)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Factors":
        path = Path(path) if path else default_path()
        if not path.exists():
            raise FileNotFoundError(
                f"탄소 계수 파일이 없습니다: {path}\n"
                f"  {CARBON_FILE}을 WASTE_REFERENCE_DIR 폴더에 두거나 WASTE_CARBON_FILE로 경로를 지정하세요. "
                "업체 매칭 없이 돌리려면 pipeline.py --no-partners")
        f = cls(path=path)
        with path.open(encoding="utf-8-sig", newline="") as fp:
            for r in csv.DictReader(fp):
                try:
                    r["계수"] = float(r["계수"])
                except (TypeError, ValueError):
                    raise ValueError(f"{path.name}: 계수가 숫자가 아닙니다 → {r}")
                if r["구분"] == "운송" and r["방법"] == "거리기준":
                    f.transport[r["항목"]] = r
                elif r["구분"] == "처리" and r["범위"] == TREATMENT_SCOPE:
                    f.treatment[(r["방법"], r["항목"])] = r
        f._check()
        return f

    def _check(self) -> None:
        for v in VEHICLES:
            row = self.transport.get(v)
            if row is None:
                raise ValueError(f"{self.path.name}: 운송 '{v}' 행이 없습니다.")
            if row["단위"] != "kgCO2eq/ton·km":
                raise ValueError(f"{self.path.name}: 운송 '{v}' 단위가 kgCO2eq/ton·km가 아닙니다 → {row['단위']}")
        for (method, item), row in self.treatment.items():
            if row["단위"] != "kgCO2eq/kg" and not row["단위"].startswith("kgCO2eq/m3"):
                raise ValueError(f"{self.path.name}: 처리 '{item}' 단위가 kgCO2eq/kg가 아닙니다 → {row['단위']}")

    def source(self, row: dict) -> str:
        return f'{row["출처"]} ({self.path.name})'


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------
def monthly_ton(case) -> tuple[float, str]:
    vals = [v for v in ((case.quantity or {}).get("monthly_ton") or []) if isinstance(v, (int, float))]
    if vals:
        return round(mean(vals), 3), f"월 평균 {len(vals)}개월"
    return 1.0, "물량 없음 → 1톤 기준"


def vehicle(case) -> str:
    return "탱크로리" if (case.material_form or "") == "액상" else "트럭"


def transport(distance_km: float | None, ton: float, veh: str, factors: Factors) -> dict | None:
    if distance_km is None:
        return None
    row = factors.transport[veh]
    if distance_km < 1.0:
        road = MIN_ROAD_KM
        how = f"같은 시군구 (위치가 같은 중심점) → 최소 {MIN_ROAD_KM}km 가정"
    else:
        road = round(distance_km * ROAD_FACTOR, 1)
        how = f"직선 {distance_km:.1f}km × {ROAD_FACTOR} (추정 도로 거리)"
    return {
        "transport_kgco2e": round(ton * road * row["계수"], 2),
        "road_km": road,
        "ton": ton,
        "vehicle": veh,
        "factor": row["계수"],
        "basis": f"{how} × {veh} {row['계수']} kgCO2eq/ton·km",
        "source": factors.source(row),
    }


def _material(code: str) -> str | None:
    try:
        if int(code.split("-")[0]) < 50:
            return "지정폐기물"
    except ValueError:
        return None
    for prefix in sorted(MATERIAL_BY_CODE, key=len, reverse=True):
        if code.startswith(prefix):
            return MATERIAL_BY_CODE[prefix]
    return None


def _method(route: dict) -> str | None:
    if route["gate"] == 4:
        t = route["title"]
        return "소각" if t.startswith("소각") else "매립" if t.startswith("매립") else None
    if route.get("tier") == "energy_recovery":
        return "소각"                                   # 에너지 회수: 소각 계수, 회수 크레딧 없음
    return "재활용"


def treatment(route: dict, ton: float, factors: Factors) -> dict:
    """경로의 처리 탄소 (참고). 계수가 없으면 값 None과 이유."""
    method = _method(route)
    mats = {_material(c) for c in route.get("waste_codes") or []}
    if not method:
        return {"treatment_kgco2e": None, "item": None, "note": "처리 방법에 맞는 계수 없음 (중간처분 등)"}
    if None in mats or len(mats) != 1:
        return {"treatment_kgco2e": None, "item": None, "note": "분류번호에 맞는 재질 계수 없음"}
    mat = mats.pop()
    item = ITEM_NAME.get((method, mat), f"{mat} {method}")
    row = factors.treatment.get((method, item))
    if row is None:
        return {"treatment_kgco2e": None, "item": item, "note": f"계수 표에 '{item}' 행 없음"}
    note = TREATMENT_NOTE + (" / 에너지 회수 크레딧 없음" if route.get("tier") == "energy_recovery" else "")
    return {"treatment_kgco2e": round(ton * 1000 * row["계수"], 1), "item": item, "factor_per_kg": row["계수"],
            "ton": ton, "note": note, "source": factors.source(row)}


CARBON_ROLES = {"recycler", "preprocessor", "treatment", "end_user"}   # 운송업체 제외 (출발지 기준 거리 아님)


def attach_carbon(route: dict, case, factors: Factors) -> dict:
    """pipeline 결과 경로 dict에 탄소를 붙인다.
    - partners.steps[].candidates[].carbon : 배출지 → 그 업체 운송 탄소
    - carbon_reference                      : 경로 처리 탄소 (참고용)"""
    ton, ton_basis = monthly_ton(case)
    veh = vehicle(case)
    for step in (route.get("partners") or {}).get("steps", []):
        if step["role"] not in CARBON_ROLES:
            continue
        for c in step["candidates"] + step.get("reserve", []):
            c["carbon"] = transport((c.get("location") or {}).get("distance_km"), ton, veh, factors)
    route["carbon_reference"] = {**treatment(route, ton, factors), "ton_basis": ton_basis}
    return route


__all__ = ["Factors", "attach_carbon", "transport", "treatment", "monthly_ton", "CARBON_FILE", "ROAD_FACTOR"]
