"""관문 4: 처분 방법 표가 confirmed면 조건 없는 경로는 confirmed, 아니면 conditional.

실행: $PY -m pytest -v gates/test_gate4_confirmed.py   (WASTE_DATA_DIR 필요)
"""
import csv
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
if (SRC / "pipline").is_dir():
    sys.path.insert(0, str(SRC / "pipline"))
SAMPLE = next((p for p in [SRC / "part1_sample.json", SRC.parent / "data" / "test_data" / "start" / "part1_sample.json"]
               if p.exists()), None)


def _run(tables_dir):
    from pipeline import Pipeline
    raw = next(c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"] if c["case_id"] == "DEMO-PART2-02")
    res = Pipeline(str(tables_dir), partners=False).run_case(raw)
    return {r["title"]: r for r in res["routes"] if r["gate"] == 4}


@pytest.fixture
def tables(tmp_path):
    src = os.environ.get("WASTE_DATA_DIR")
    if not src or SAMPLE is None or not (Path(src) / "disposal_methods_5.csv").exists():
        pytest.skip("WASTE_DATA_DIR 또는 샘플 없음")
    d = tmp_path / "active"
    shutil.copytree(src, d)
    return d


def _set_status(d, value):
    p = d / "disposal_methods_5.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    for r in rows:
        r["status"] = value
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_draft_caps_conditional(tables):
    _set_status(tables, "draft")
    routes = _run(tables)
    assert routes and all(r["regulatory"] in ("conditional", "needs_info") for r in routes.values())
    assert all(r["basis_status"] == "검토 대기" for r in routes.values())


def test_confirmed_lifts_when_no_conditions(tables):
    _set_status(tables, "confirmed")
    routes = _run(tables)
    incin = next(r for t, r in routes.items() if t.startswith("소각"))
    assert incin["basis_status"] == "확정" and incin["regulatory"] == "confirmed"
    middle = next(r for t, r in routes.items() if t.startswith("중간처분"))
    assert middle["regulatory"] == "conditional"          # "잔재물은 소각" 조건이 있어 conditional 유지
