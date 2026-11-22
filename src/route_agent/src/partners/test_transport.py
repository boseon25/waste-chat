"""
업체 매칭 4단계 테스트: 운반 업체(transporter) 매칭, 경로 확보 여부.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<프로젝트>/data/reference \
  $PY -m pytest -v partners/test_transport.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gates import gate1, gate2, gate4
from gates.models import RouteDraft, Status, Step
from gates.tables import Tables
from partners import by_transport
from partners.data import PartnerData
from partners.matcher import match_routes
from preliminary.preliminary_integrator import PreliminaryIntegrator

DATA_DIR = Path(os.environ["WASTE_DATA_DIR"])
SAMPLE = Path(os.environ.get("PART1_SAMPLE", Path(__file__).resolve().parent.parent / "preliminary" / "part1_sample.json"))


@pytest.fixture(scope="module")
def data():
    return PartnerData.load()


@pytest.fixture(scope="module")
def jeonju(data):
    return data.locator.origin("전북", "전주시")


@pytest.fixture(scope="module")
def integrator():
    return PreliminaryIntegrator(DATA_DIR)


def transport_route(codes: list[str], status: str = "general", gate: int = 2):
    step = Step(1, "수집·운반", "transporter", waste_codes=codes)
    route = RouteDraft(gate=gate, tier=None, route_type="single_material_recycle", title="t",
                       waste_codes=codes, waste_status=status, steps=[step], regulatory=Status.CONDITIONAL)
    return route, step


def row(**kw):
    base = {"영업중": "Y", "대상군": "생활·사업장일반", "영업대상_구분": "사업장배출시설계", "지정_분류번호_앞자리": ""}
    base.update(kw)
    return base


@pytest.mark.parametrize("r, codes, reason", [
    (row(영업중="N"), ["51-03-01"], "영업 중단"),
    (row(대상군="건설", 영업대상_구분="건설"), ["51-03-01"], "건설폐기물만"),
    (row(영업대상_구분="생활;음식물류"), ["51-03-01"], "대상 폐기물 다름"),      # 생활·음식물만
    (row(영업대상_구분="생활;사업장비(非)배출시설계"), ["51-03-01"], None),
    (row(), ["04-02-00"], "대상 폐기물 다름"),
    (row(대상군="지정", 지정_분류번호_앞자리="04;06"), ["04-02-00"], None),
    (row(대상군="지정", 지정_분류번호_앞자리="04"), ["04-02-00", "06-01-01"], "혼합물 일부만 대상"),
    (row(대상군="의료", 영업대상_구분=""), ["10-13-00"], None),
])
def test_judge(r, codes, reason):
    assert by_transport.judge(r, codes) == reason


def test_emitter_scope_label():
    assert by_transport._emitter_scope("생활;사업장비(非)배출시설계;사업장배출시설계") == "배출시설계·비배출시설계"
    assert by_transport._emitter_scope("사업장배출시설계") == "배출시설계"
    assert by_transport._emitter_scope("생활;사업장비(非)배출시설계") == "비배출시설계"


def test_general_transport_local_first(data, jeonju):
    route, step = transport_route(["51-03-01"])
    found, rejected, questions = by_transport.match(route, step, data, jeonju)
    assert len(found) > 1000
    assert rejected["건설폐기물만"] > 1000 and rejected["영업 중단"] > 0
    assert any(q["field"] == "emitter_facility_type" for q in questions)
    for c in found[:50]:
        assert "사업장" in (c.scope_text or "") or c.scope_text is None or c.waste_group == "생활·사업장일반"


def test_designated_transport_scope(data, jeonju):
    route, step = transport_route(["04-02-00"], "designated")
    found, _, questions = by_transport.match(route, step, data, jeonju)
    assert found
    for c in found:
        assert c.waste_group == "지정"
        assert "*" in c.accepts_designated or any("04-02-00".startswith(p) for p in c.accepts_designated)
    assert not any(q["field"] == "emitter_facility_type" for q in questions)


def test_medical_transport(data, jeonju):
    route, step = transport_route(["10-13-00"], "designated")
    found, _, _ = by_transport.match(route, step, data, jeonju)
    assert found and all(c.waste_group == "의료" for c in found)


def test_candidate_shows_vehicles(data, jeonju):
    route, step = transport_route(["51-03-01"])
    c = by_transport.match(route, step, data, jeonju)[0][0]
    assert c.capacity and "보유차량" in c.capacity and c.permit_type == "허가"


# ---------------------------------------------------------------------------
# matcher: 관문 1 운반, 경로 확보 여부
# ---------------------------------------------------------------------------
def make_case(integrator, code="51-03-01"):
    return integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": code},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})


def test_gate1_transport_not_matched(data, integrator):
    route, _ = transport_route(["51-03-01"], gate=1)
    m = match_routes([route], make_case(integrator), None, data)[0]
    t = m.steps[0]
    assert t.optional and not t.candidates and t.questions[0]["field"] == "transport_by"


def test_route_secured_with_transport_and_recycler(data, integrator, jeonju):
    route, _ = transport_route(["51-03-01"])
    route.steps.append(Step(2, "재활용", "recycler", ["2003"], ["R-3-3"], ["51-03-01"]))
    m = match_routes([route], make_case(integrator), None, data)[0]
    assert all(s.candidates for s in m.steps) and m.secured is True


def test_route_not_secured_when_treatment_missing(data, integrator):
    route, _ = transport_route(["04-02-00"], "designated", gate=4)
    route.steps.append(Step(2, "중간처분", "treatment", ["2109", "2119", "2116"], [], ["04-02-00"]))
    m = match_routes([route], make_case(integrator, "04-02-00"), None, data)[0]
    assert m.steps[0].candidates and not m.steps[1].candidates
    assert m.secured is False


def test_samples_all_routes_have_status(data, integrator):
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 없음: {SAMPLE}")
    tables = Tables.load(DATA_DIR)
    for raw in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]:
        case = integrator.input_adapter.adapt(raw)
        pre = integrator.run(case)
        routes = gate2.run(case, pre, tables) + gate4.run(case, pre, tables)
        for r, m in zip(routes, match_routes(routes, case, pre, data)):
            assert m.secured in (True, False), r.title
