"""
pipeline.py 테스트: 파트 1 입력 → 사전검증 → 관문 → 업체 매칭이 한 번에 나오는지.

실행 (src/route_agent/src 에서, pipeline.py와 같은 폴더 기준):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<시군구 중심점 폴더> \
  PART1_SAMPLE=preliminary/part1_sample.json \
  $PY -m pytest -v test_pipeline.py
"""
from __future__ import annotations

import json
import re
import os
from pathlib import Path

import pytest

from pipeline import Pipeline

SAMPLE = Path(os.environ.get("PART1_SAMPLE", "preliminary/part1_sample.json"))


@pytest.fixture(scope="module")
def cases():
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 없음: {SAMPLE}")
    return {c["case_id"]: c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]}


@pytest.fixture(scope="module")
def pipe():
    return Pipeline()


def test_every_route_has_partners(pipe, cases):
    for raw in cases.values():
        res = pipe.run_case(raw)
        assert res["routes"]
        for r in res["routes"]:
            assert "partners" in r and r["partners"]["secured"] in (True, False, None)
        json.dumps(res, ensure_ascii=False)                     # 그대로 저장 가능


def test_pe_film_recycling_secured(pipe, cases):
    res = pipe.run_case(cases["DEMO-PART2-01"])
    r = next(r for r in res["routes"] if r["gate"] == 2 and r["tier"] == "material_recycle")
    assert r["partners"]["secured"] is True
    roles = {s["role"]: s for s in r["partners"]["steps"]}
    assert roles["transporter"]["candidates"] and roles["recycler"]["candidates"]
    assert roles["recycler"]["candidates"][0]["sido"] == "전북"


def test_solvent_neutralization_not_secured(pipe, cases):
    res = pipe.run_case(cases["DEMO-PART2-02"])
    r = next(r for r in res["routes"] if r["gate"] == 4 and r["title"].startswith("중간처분"))
    assert r["partners"]["secured"] is False


def test_gate3_not_used(pipe, cases):
    for raw in cases.values():
        assert all(r["gate"] != 3 for r in pipe.run_case(raw)["routes"])


def test_every_route_has_laws(pipe, cases):
    for raw in cases.values():
        for r in pipe.run_case(raw)["routes"]:
            assert r["laws"], r["title"]
            for l in r["laws"]:
                assert l["law"] and l["article"] and l["used_for"], l      # 해석 못 한 근거 없음


def test_recycling_route_laws(pipe, cases):
    res = pipe.run_case(cases["DEMO-PART2-01"])
    r = next(r for r in res["routes"] if r["gate"] == 2 and r["tier"] == "material_recycle")
    b43 = next(l for l in r["laws"] if l["article"] == "별표 4의3")
    # 시행일은 판정표를 만든 원문판에 따라 바뀐다 (개정되면 새 날짜) → 형식과 키 연결만 확인
    assert b43["law"] == "폐기물관리법 시행규칙" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", b43["effective_date"])
    assert b43["key"].startswith("WMA_RULE:B4의3@" + b43["effective_date"].replace("-", ""))
    assert any(u.startswith("재활용 유형 R-3-3") for u in b43["used_for"])


def test_designated_disposal_laws(pipe, cases):
    res = pipe.run_case(cases["DEMO-PART2-02"])
    r = next(r for r in res["routes"] if r["gate"] == 4)
    arts = {(l["law"], l["article"]) for l in r["laws"]}
    assert ("폐기물관리법 시행규칙", "별표 5 제4호") in arts               # 지정폐기물 처분 기준
    assert ("폐기물관리법", "제25조") in arts                             # 처분업 허가 업체에 위탁


def test_no_partners_option(cases):
    res = Pipeline(partners=False).run_case(cases["DEMO-PART2-01"])
    assert all("partners" not in r for r in res["routes"])


def test_candidates_have_transport_carbon(pipe, cases):
    res = pipe.run_case(cases["DEMO-PART2-01"])
    r = next(r for r in res["routes"] if r["gate"] == 2 and r["tier"] == "material_recycle")
    rec = next(s for s in r["partners"]["steps"] if s["role"] == "recycler")
    c = rec["candidates"][0]["carbon"]
    assert c["vehicle"] == "트럭" and c["ton"] == 1.21 and c["transport_kgco2e"] > 0
    assert r["carbon_reference"]["item"] == "혼합폐플라스틱 재활용"
    solvent = pipe.run_case(cases["DEMO-PART2-02"])["routes"][0]
    rec2 = next(s for s in solvent["partners"]["steps"] if s["role"] in ("recycler", "preprocessor"))
    assert rec2["candidates"][0]["carbon"]["vehicle"] == "탱크로리"          # 액상
