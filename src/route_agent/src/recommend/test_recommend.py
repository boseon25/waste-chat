"""
추천 테스트: 배출업체 → 가공업체 카드, 가공업체 → 배출업체 역방향, 양방향 일치.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<…/data/tables/active> WASTE_REFERENCE_DIR=<중심점·탄소 계수 폴더> \
  PART1_SAMPLE=preliminary/part1_sample.json PRELIM_TESTSET=preliminary/testdata/preliminary_testset.json \
  PYTHONPATH=.:pipline $PY -m pytest -v recommend/test_recommend.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline import Pipeline
from recommend.reverse import ReverseIndex

SAMPLE = Path(os.environ.get("PART1_SAMPLE", "preliminary/part1_sample.json"))
TESTSET = Path(os.environ.get("PRELIM_TESTSET", "preliminary/testdata/preliminary_testset.json"))


def _cases(p: Path) -> list[dict]:
    if not p.is_file():
        pytest.skip(f"입력 파일 없음: {p}")
    return json.loads(p.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def pipe():
    return Pipeline()


@pytest.fixture(scope="module")
def raws():
    return _cases(SAMPLE) + (_cases(TESTSET) if TESTSET.is_file() else [])


@pytest.fixture(scope="module")
def forward(pipe, raws):
    return {r["case_id"]: pipe.run_case(r)["recommendation"] for r in raws}


@pytest.fixture(scope="module")
def reverse(pipe, raws):
    idx = ReverseIndex(pipe)
    idx.add_cases(raws)
    return idx


# ---------------------------------------------------------------------------
# 배출업체 → 가공업체 카드
# ---------------------------------------------------------------------------
def test_card_shape(forward):
    rec = forward["DEMO-PART2-01"]
    assert rec["waste"]["분류번호"] == "51-03-01" and rec["waste"]["월물량_톤"] == 1.21
    opt = next(o for o in rec["options"] if o["구분"] == "재활용 위탁 (재생이용)")
    assert opt["법적상태"] == "가능" and opt["업체확보"] == "확보"
    assert opt["근거법령"] and len(opt["추천업체"]) == 5
    first = opt["추천업체"][0]
    for k in ("company_id", "업체명", "지역", "허가", "거리_km", "운송탄소_kgCO2eq_월", "추천이유", "데이터_기준"):
        assert first[k], k
    assert any(s["역할"] == "운반 업체" and s["메모"] == "협력 확정 후 선택" for s in opt["다른_단계"])


def test_cards_sorted_by_distance(forward):
    for rec in forward.values():
        for opt in rec["options"]:
            d = [p["직선거리_km"] for p in opt["추천업체"] if p["직선거리_km"] is not None]
            assert d == sorted(d), opt["처리"]


def test_cards_never_include_transporters(forward):
    for rec in forward.values():
        for opt in rec["options"]:
            assert all(p["역할"] != "운반 업체" for p in opt["추천업체"])


def test_no_partner_routes_listed_separately(forward):
    none = forward["DEMO-PART2-02"]["추천_없음"]
    assert any(n["처리"].startswith("중간처분") and "맞는 업체 없음" in n["이유"][0] for n in none)


def test_options_follow_law_order(forward):
    order = {"순환자원 판매": 0, "재활용 위탁": 1, "소각·매립": 2}
    for rec in forward.values():
        ks = [order[o["구분"].split(" (")[0]] for o in rec["options"]]
        assert ks == sorted(ks)


# ---------------------------------------------------------------------------
# 가공업체 → 배출업체 (역방향)
# ---------------------------------------------------------------------------
def test_reverse_finds_forward_case(reverse):
    rec = reverse.recommend("엘림이엔|전주시 완산구", top_n=0)
    assert "DEMO-PART2-01" in {e["case_id"] for e in rec["추천"]}


def test_forward_and_reverse_agree(forward, reverse):
    """정방향 카드에 나온 모든 업체는 역방향에서도 그 폐기물을 추천해야 한다"""
    for cid, rec in forward.items():
        for opt in rec["options"]:
            for p in opt["추천업체"]:
                back = {e["case_id"] for e in reverse.recommend(p["company_id"], top_n=0)["추천"]}
                assert cid in back, (cid, p["업체명"])


def test_reverse_designated_firm_gets_designated_waste(reverse):
    ids = [c["company_id"] for c in reverse.find("유광드럼")]
    assert ids
    rec = reverse.recommend(ids[0], top_n=0)
    assert any(e["분류번호"].startswith("04-02") for e in rec["추천"])


def test_reverse_sorted_by_distance_then_volume(reverse):
    rec = reverse.recommend("엘림이엔|전주시 완산구", top_n=0)["추천"]
    keys = [(e["거리_km"], -(e["월물량_톤"] or 0)) for e in rec]
    dist = [k[0] for k in keys]
    assert dist == sorted(dist)


def test_transporters_not_indexed(reverse, pipe):
    transport_ids = {r["업체_키"] for r in pipe.partner_data.transport[:2000]}
    recycling_ids = {r["업체_키"] for r in pipe.partner_data.recycling}
    only_transport = transport_ids - recycling_ids - {r["업체_키"] for r in pipe.partner_data.disposal}
    assert not (only_transport & set(reverse.index))
