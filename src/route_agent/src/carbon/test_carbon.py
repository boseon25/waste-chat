"""탄소 계산 테스트.

- 계산식 테스트는 작은 계수 파일을 임시로 만들어 쓴다 (판정표·실제 계수 파일 없이 돌아감)
- 실제 계수 파일 테스트는 WASTE_REFERENCE_DIR(또는 WASTE_CARBON_FILE)이 있을 때만

실행: $PY -m pytest -v carbon/test_carbon.py
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from carbon import CARBON_FILE, ITEM_NAME, MATERIAL_BY_CODE, Factors, attach_carbon, monthly_ton, transport, treatment

HEADER = "구분,방법,항목,단위,계수,범위,출처,비고\n"
ROWS = [
    "운송,거리기준,트럭,kgCO2eq/ton·km,0.2,LCI 전과정,테스트,",
    "운송,거리기준,탱크로리,kgCO2eq/ton·km,0.05,LCI 전과정,테스트,",
    "처리,재활용,혼합폐플라스틱 재활용,kgCO2eq/kg,0.02,LCI 전과정,테스트,",
    "처리,소각,지정폐기물 소각,kgCO2eq/kg,1.8,LCI 전과정,테스트,",
    "처리,소각,사업장-폐합성수지,kgCO2eq/kg,1.96,직접배출(화석CO2+CH4+N2O),인벤토리,",   # 범위가 달라 안 씀
]


@pytest.fixture
def small(tmp_path) -> Factors:
    p = tmp_path / CARBON_FILE
    p.write_text(HEADER + "\n".join(ROWS) + "\n", encoding="utf-8-sig")
    return Factors.load(p)


def case(monthly=None, form="파쇄"):
    return NS(quantity={"monthly_ton": monthly} if monthly else {}, material_form=form)


def test_monthly_ton():
    assert monthly_ton(case([1.15, 1.27, 1.21])) == (1.21, "월 평균 3개월")
    assert monthly_ton(case()) == (1.0, "물량 없음 → 1톤 기준")


def test_transport_uses_file_values(small):
    t = transport(10.0, 2.0, "트럭", small)
    assert t["road_km"] == 13.0 and t["factor"] == 0.2 and t["transport_kgco2e"] == pytest.approx(5.2)
    assert transport(10.0, 2.0, "탱크로리", small)["factor"] == 0.05
    assert transport(None, 1.0, "트럭", small) is None


def test_same_center_uses_minimum(small):
    t = transport(0.0, 1.0, "트럭", small)
    assert t["road_km"] == 5.0 and t["transport_kgco2e"] == pytest.approx(1.0) and "최소" in t["basis"]


def test_treatment_from_file_and_scope(small):
    r = treatment({"gate": 2, "tier": "material_recycle", "title": "재생이용", "waste_codes": ["51-03-01"]}, 1.0, small)
    assert r["item"] == "혼합폐플라스틱 재활용" and r["treatment_kgco2e"] == pytest.approx(20.0)
    assert "비교하지 말 것" in r["note"] and "테스트" in r["source"]
    inc = treatment({"gate": 4, "title": "소각: 일반소각", "waste_codes": ["04-02-00"]}, 1.0, small)
    assert inc["factor_per_kg"] == 1.8                               # 직접배출 행이 아니라 전과정 행
    miss = treatment({"gate": 4, "title": "매립", "waste_codes": ["51-03-01"]}, 1.0, small)
    assert miss["treatment_kgco2e"] is None and "행 없음" in miss["note"]
    assert treatment({"gate": 4, "title": "중간처분", "waste_codes": ["04-02-00"]}, 1.0, small)["treatment_kgco2e"] is None


def test_missing_vehicle_row_is_error(tmp_path):
    p = tmp_path / CARBON_FILE
    p.write_text(HEADER + ROWS[0] + "\n", encoding="utf-8-sig")              # 탱크로리 없음
    with pytest.raises(ValueError, match="탱크로리"):
        Factors.load(p)


def test_missing_file_is_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="탄소 계수 파일이 없습니다"):
        Factors.load(tmp_path / "없음.csv")


def test_attach_skips_transporter(small):
    route = {"gate": 2, "tier": "material_recycle", "title": "재생이용", "waste_codes": ["51-03-01"],
             "partners": {"steps": [
                 {"role": "transporter", "candidates": [{"name": "운반", "location": {"distance_km": 5.0}}]},
                 {"role": "recycler", "candidates": [{"name": "가공", "location": {"distance_km": 10.0}},
                                                     {"name": "좌표 없음", "location": {"distance_km": None}}]}]}}
    attach_carbon(route, case([2.0], "액상"), small)
    steps = route["partners"]["steps"]
    assert "carbon" not in steps[0]["candidates"][0]
    assert steps[1]["candidates"][0]["carbon"]["vehicle"] == "탱크로리"
    assert steps[1]["candidates"][1]["carbon"] is None


# ---------------------------------------------------------------------------
# 실제 계수 파일 (WASTE_REFERENCE_DIR 또는 WASTE_CARBON_FILE이 있을 때)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real():
    try:
        return Factors.load()
    except (RuntimeError, FileNotFoundError) as e:
        pytest.skip(str(e))


def test_real_file_values(real):
    assert real.transport["트럭"]["계수"] == pytest.approx(0.1924, abs=1e-4)
    assert real.transport["탱크로리"]["계수"] == pytest.approx(0.0444, abs=1e-4)


def test_every_designed_item_exists_in_real_file(real):
    """코드의 재질 대응이 가리키는 계수 행이 실제 파일에 모두 있는지 (계수 표를 바꿨을 때 깨지면 알 수 있게)"""
    mats = set(MATERIAL_BY_CODE.values()) | {"지정폐기물"}
    missing = []
    for mat in mats:
        for method in ("소각", "매립", "재활용"):
            if mat == "지정폐기물" and method == "재활용":
                continue
            item = ITEM_NAME.get((method, mat), f"{mat} {method}")
            if (method, item) not in real.treatment:
                missing.append(item)
    assert not missing, f"계수 표에 없는 항목: {missing}"
