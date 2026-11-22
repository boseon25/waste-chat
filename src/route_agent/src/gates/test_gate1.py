"""
관문 1 테스트. 사전검증 → 관문 1 순서로 실제 판정표를 써서 돌린다.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active PART1_SAMPLE=<경로>/part1_sample.json \
  $PY -m pytest -v gates/test_gate1.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gates import gate1
from gates.models import Status
from gates.tables import Tables
from preliminary.models import GateAction
from preliminary.preliminary_integrator import PreliminaryIntegrator

DATA_DIR = Path(os.environ["WASTE_DATA_DIR"])
SAMPLE = Path(os.environ.get("PART1_SAMPLE", Path(__file__).resolve().parent.parent / "preliminary" / "part1_sample.json"))


@pytest.fixture(scope="module")
def integrator():
    return PreliminaryIntegrator(DATA_DIR)


@pytest.fixture(scope="module")
def tables():
    return Tables.load(DATA_DIR)


def run(integrator, tables, raw):
    case = integrator.input_adapter.adapt(raw)
    pre = integrator.run(case)
    routes = gate1.run(case, pre, tables) if pre.gate_plan["gate1"].action == GateAction.RUN else []
    return routes, pre


def make_case(code, item=None, mixed=False, foreign=None, storage=None, **extra):
    spec = {"ic_tray": None, "nonferrous_material": None, "grain_processor_registered": None}
    spec.update(item or {})
    case = {
        "schema_version": "1.0", "case_id": "G1", "case_version": 1, "hazardous_suspect": False,
        "classification": {"selected_code": code},
        "components": [{"material": "x", "share_pct": 100}],
        "item_specific": spec,
        "mixed_with_other": mixed,
        "foreign_matter": foreign if foreign is not None else {"items": [], "separable": None},
        "storage_state": storage,
    }
    case.update(extra)
    return case


# ---------------------------------------------------------------------------
# 파트 1 샘플
# ---------------------------------------------------------------------------
def test_samples(integrator, tables):
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 없음: {SAMPLE}")
    cases = {c["case_id"]: c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]}
    routes, pre = run(integrator, tables, cases["DEMO-PART2-01"])
    assert pre.gate_plan["gate1"].action == GateAction.RUN
    assert routes == []                                   # 51-03-01은 IC 트레이만 지정 순환자원, ic_tray=false
    routes, pre = run(integrator, tables, cases["DEMO-PART2-02"])
    assert pre.gate_plan["gate1"].action == GateAction.SKIP and routes == []   # 지정폐기물


# ---------------------------------------------------------------------------
# 경우별
# ---------------------------------------------------------------------------
def test_not_designated_item(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-03-02"))
    assert routes == []


def test_ic_tray(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-03-01", {"ic_tray": True}))
    assert len(routes) == 1 and routes[0].regulatory == Status.CONFIRMED
    assert routes[0].steps[-1].role == "end_user" and routes[0].steps[-1].r_codes == ["R-4-4"]
    assert routes[0].route_type == "direct_use" and routes[0].gate == 1


def test_ic_tray_unknown(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-03-01"))
    assert routes[0].regulatory == Status.NEEDS_INFO
    assert routes[0].questions[0]["field"] == "item_specific.ic_tray"


def test_nonferrous_picks_item(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-29-02", {"nonferrous_material": "구리"}))
    assert "구리" in routes[0].title and "알루미늄" not in routes[0].title
    routes, _ = run(integrator, tables, make_case("51-29-02", {"nonferrous_material": "아연"}))
    assert routes == []


def test_grain_not_registered_is_rejected(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-19-00", {"grain_processor_registered": False}, storage="밀폐"))
    assert routes[0].regulatory == Status.REJECTED


def test_mixed_with_other_rejected(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-16-00", mixed=True))
    assert routes[0].regulatory == Status.REJECTED


def test_foreign_matter(integrator, tables):
    ok, _ = run(integrator, tables, make_case("51-28-02", {"in_scope": True}, foreign={"items": ["테이프"], "separable": True}))
    no, _ = run(integrator, tables, make_case("51-28-02", {"in_scope": True}, foreign={"items": ["코팅"], "separable": False}))
    assert ok[0].regulatory == Status.CONDITIONAL
    assert no[0].regulatory == Status.REJECTED


def test_storage_required(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-17-25"))
    assert routes[0].regulatory == Status.NEEDS_INFO
    assert any(q["field"] == "storage_state" for q in routes[0].questions)


def test_suspected_skips(integrator, tables):
    routes, pre = run(integrator, tables, make_case("51-16-00", hazardous_suspect=True))
    assert pre.gate_plan["gate1"].action == GateAction.SKIP and routes == []


def test_conditions_written(integrator, tables):
    routes, _ = run(integrator, tables, make_case("51-29-01", {"in_scope": True}))
    texts = [c["text"] for c in routes[0].conditions]
    assert any("R-4-1" in t for t in texts)
    assert any("절단" in t and "압축" in t for t in texts)
    assert any("인계서" in t for t in texts)


# ---------------------------------------------------------------------------
# 회귀 50건 (src/tests/regress_cases.csv 의 관문 1 기대값)
#   (case, 분류번호, 상대 용도 r_code, g1_품목해당 Y/N/?, g1_조건 Y/?/N_block/N_warn/빈칸, 기대)
# ---------------------------------------------------------------------------
REGRESS = [
    ("S01", "51-19-00", "R-5-4", "Y", "Y", "가능"),  # 쌀겨 도정공장→자가농가 퇴비
    ("S02", "51-19-00", "R-5-4", "N", "", "불가"),  # 쌀겨 일반식품공장→미신고 농가
    ("S03", "51-19-00", "R-5-2", "N", "", "불가"),  # 쌀겨 일반식품공장→허가 재활용(사료)
    ("S04", "51-19-00", "R-9-1", "Y", "Y", "불가"),  # 쌀겨 도정→연료 상대
    ("S05", "51-16-00", "R-3-3", "Y", "Y", "가능"),  # 폐식용유→바이오연료 원료
    ("S06", "51-16-00", "R-5-2", "Y", "Y", "불가"),  # 폐식용유→사료
    ("S07", "51-16-00", "R-3-3", "Y", "?", "확인 필요"),  # 폐식용유 다른 기름 혼합
    ("S08", "51-03-01", "R-4-4", "N", "", "불가"),  # PP 자투리→허가 없는 용기제조
    ("S09", "51-03-01", "R-3-3", "N", "", "불가"),  # PP 자투리→허가 재활용
    ("S10", "51-03-01", "R-3-3", "N", "", "불가"),  # PP 자투리→처리신고(원료)
    ("S11", "51-03-01", "R-4-4", "Y", "Y", "가능"),  # IC트레이→재생업체
    ("S12", "51-03-01", "R-4-4", "?", "", "확인 필요"),  # 트레이 IC인지 모름
    ("S13", "51-29-01", "R-4-1", "Y", "Y", "가능"),  # 고철 절단 완료→제강
    ("S14", "51-29-01", "R-3-3", "Y", "", "불가"),  # 고철 이물질→처리신고
    ("S15", "51-29-02", "R-4-1", "Y", "Y", "가능"),  # 알루미늄 스크랩
    ("S16", "51-29-02", "R-4-1", "?", "", "확인 필요"),  # 비철 재질 모름
    ("S17", "51-28-02", "R-4-3", "Y", "Y", "가능"),  # 폐지→제지사
    ("S18", "51-17-25", "R-5-3", "Y", "N_warn", "가능(조건 미충족)"),  # 커피찌꺼기 개방보관→버섯
    ("S19", "51-20-23", "R-2-1", "N", "", "불가"),  # 폐목재 파렛트→수리 처리신고
    ("S20", "06-01-03", "R-9-2", "N", "", "불가"),  # 폐작동유(지정)
    ("S21", "51-28-02", "R-4-3", "Y", "Y", "가능"),  # 폐지 베일→제지
    ("S22", "51-28-02", "R-10-1", "Y", "N_block", "불가"),  # 폐지 코팅 분리불가→처리신고 중간가공
    ("S23", "51-29-01", "R-4-1", "Y", "N_block", "불가"),  # 고철 도료 분리불가→처리신고 제품제조
    ("S24", "51-29-02", "R-4-1", "Y", "Y", "가능"),  # 구리 스크랩
    ("S25", "51-29-02", "R-4-1", "N", "", "불가"),  # 아연 스크랩→허가
    ("S26", "51-03-01", "", "N", "", "불가"),  # PP 자투리 상대 없음
    ("S27", "51-30-01", "R-1-1", "Y", "", "불가"),  # 폐유리→원형 재사용
    ("S28", "51-30-02", "R-2-1", "Y", "", "불가"),  # 폐유리병→세척 재사용 처리신고
    ("S29", "51-14-02", "R-7-1", "Y", "", "불가"),  # 폐석재→성토재 처리신고
    ("S30", "51-16-00", "R-9-2", "Y", "", "불가"),  # 폐식용유→정제연료유 허가
    ("S31", "51-16-00", "R-3-3", "Y", "Y", "가능"),  # 폐식용유→원료 처리신고
    ("S32", "51-16-00", "R-4-8", "Y", "", "불가"),  # 폐식용유→비누 처리신고
    ("S33", "51-17-25", "R-5-3", "Y", "Y", "가능"),  # 커피찌꺼기 밀폐→버섯
    ("S34", "51-17-25", "R-9-1", "Y", "", "불가"),  # 커피찌꺼기→연료
    ("S35", "51-19-00", "R-5-1", "Y", "Y", "가능"),  # 쌀겨 도정→비료 허가
    ("S36", "51-19-00", "R-5-1", "N", "", "불가"),  # 쌀겨 식품공장→비료 처리신고 5톤
    ("S37", "51-19-00", "R-5-1", "N", "", "불가"),  # 쌀겨 식품공장→비료 처리신고 15톤
    ("S38", "51-19-00", "R-5-1", "N", "", "불가"),  # 쌀겨 식품공장→비료 처리신고 용량 모름
    ("S39", "51-17-29", "R-5-4", "N", "", "불가"),  # 채소 잔재물→자가 가축 먹이
    ("S40", "51-20-24", "R-9-1", "N", "", "불가"),  # 도장 폐목재→SRF 허가
    ("S41", "51-20-23", "R-2-1", "N", "", "불가"),  # 목재 파렛트→수리 허가
    ("S42", "51-03-01", "R-9-1", "N", "", "불가"),  # PE 필름→고형연료 허가
    ("S43", "51-03-01", "R-4-4", "N", "", "불가"),  # PP 자투리→제품제조 처리신고
    ("S44", "51-03-01", "R-3-3", "N", "", "불가"),  # PP 자투리→자가처리만 신고 업체
    ("S45", "51-03-01", "R-4-4", "N", "", "불가"),  # PP 자투리→제품제조 허가
    ("S46", "06-01-01", "R-9-2", "N", "", "불가"),  # 폐윤활유(지정)→지정 폐유 재활용
    ("S47", "06-01-03", "R-9-2", "N", "", "불가"),  # 폐작동유(지정)→일반 업체
    ("S48", "51-03-01", "R-4-4", "Y", "?", "확인 필요"),  # IC트레이 혼합
    ("S49", "51-29-01", "R-4-1", "Y", "?", "확인 필요"),  # 고철 이물질 분리 모름
    ("S50", "51-41-05", "R-2-2", "Y", "Y", "가능"),  # 전기차 폐배터리 양호→ESS
]

ITEM_FIELD = {"51-03-01": "ic_tray", "51-19-00": "grain_processor_registered"}


def regress_case(code, item, cond):
    spec = {}
    if code == "51-29-02":
        spec["nonferrous_material"] = {"Y": "알루미늄", "N": "아연", "?": None}[item]
    else:
        spec[ITEM_FIELD.get(code, "in_scope")] = {"Y": True, "N": False, "?": None}[item]
    kw = dict(mixed=False, foreign={"items": [], "separable": None}, storage="밀폐 용기, 실내 보관")
    if cond == "?":
        kw["mixed"] = None
    elif cond == "N_block":
        kw["foreign"] = {"items": ["코팅·도료"], "separable": False}
    elif cond == "N_warn":
        kw.update(storage="개방 보관", storage_ok=False)
    return make_case(code, spec, **kw)


@pytest.mark.parametrize("row", REGRESS, ids=[r[0] for r in REGRESS])
def test_regress(integrator, tables, row):
    cid, code, rc, item, cond, expected = row
    routes, pre = run(integrator, tables, regress_case(code, item, cond))
    usable = [r for r in routes if not rc or rc in r.steps[-1].r_codes]

    if expected == "가능":
        assert usable and usable[0].regulatory == Status.CONFIRMED, [(r.title, r.regulatory, r.reasons) for r in routes]
    elif expected == "가능(조건 미충족)":
        assert usable and usable[0].regulatory == Status.CONDITIONAL
    elif expected == "확인 필요":
        assert usable and usable[0].regulatory == Status.NEEDS_INFO
    else:  # 불가: 경로 없음, REJECTED, 또는 상대 용도가 고시 용도가 아님
        assert not usable or usable[0].regulatory == Status.REJECTED, [(r.title, r.regulatory) for r in usable]
