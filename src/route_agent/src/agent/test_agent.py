"""에이전트 테스트 (AGENT_MOCK=1, 네트워크 없음). 판정표·업체 표가 필요 (WASTE_DATA_DIR).

실행: AGENT_MOCK=1 $PY -m pytest -v agent/test_agent.py
"""
import json
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
if (SRC / "pipline").is_dir():                      # 서버 구조: src/pipline/pipeline.py
    sys.path.insert(0, str(SRC / "pipline"))
# 파트 1 샘플: 환경변수 → src 옆 → data/test_data/start
SAMPLE = next((p for p in [Path(os.environ.get("PART1_SAMPLE", "/nonexistent")), SRC / "part1_sample.json",
                           SRC.parent / "data" / "test_data" / "start" / "part1_sample.json"] if p.exists()), None)


@pytest.fixture(scope="module")
def pipe():
    if not os.environ.get("WASTE_DATA_DIR") or SAMPLE is None:
        pytest.skip("WASTE_DATA_DIR 또는 part1_sample.json 없음")
    from pipeline import Pipeline
    return Pipeline(agent=True)


@pytest.fixture(scope="module")
def designated(pipe):
    os.environ["AGENT_MOCK"] = "1"
    raw = next(c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"] if c["case_id"] == "DEMO-PART2-02")
    return pipe.run_case(raw)


def test_only_unsecured_routes_get_alternatives(designated):
    for r in designated["routes"]:
        has = "alternatives" in r
        assert has == ((r.get("partners") or {}).get("secured") is False)


def test_alternatives_verified(designated):
    r = next(r for r in designated["routes"] if "alternatives" in r)
    alt = r["alternatives"]
    assert alt["status"] == "candidate" and alt["tools_used"][-1] == "finish"
    change = next(a for a in alt["items"] if a["kind"] == "change_method")
    assert change["partners"] and all(p["company_id"] for p in change["partners"])
    p = next(p for p in designated["route_proposals"] if p["alternatives"])
    assert p["secured"] is False                                           # 확보 여부도 그대로


def test_verify_drops_made_up(pipe, designated):
    from agent.tools import Ctx, verify
    routes_d = designated["routes"]
    i = next(i for i, r in enumerate(routes_d) if "alternatives" in r)
    ctx = Ctx(index=i, routes=[], route_dicts=routes_d, case=None, pre=None, partner_data=None,
              data_dir=pipe.data_dir)
    kept, dropped = verify(ctx, [{"kind": "change_method", "partners": ["지어낸업체"], "reason": "x"},
                                 {"kind": "other_route", "route_title": "없는 경로", "reason": "x"},
                                 {"kind": "change_method", "treatment_codes": ["2201"], "reason": "x"},
                                 {"kind": "need_info", "reason": "배출업체에 성상 확인"}])
    assert [a["kind"] for a in kept] == ["need_info"] and len(dropped) == 3


def test_failure_does_not_break(pipe, monkeypatch):
    monkeypatch.delenv("AGENT_MOCK", raising=False)
    monkeypatch.delenv("TIMELY_API_KEY", raising=False)
    raw = next(c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"] if c["case_id"] == "DEMO-PART2-02")
    res = pipe.run_case(raw)
    alt = next(r["alternatives"] for r in res["routes"] if "alternatives" in r)
    assert alt["status"] == "failed" and alt["items"] == []
    assert res["route_proposals"]                                          # 나머지 결과는 그대로
