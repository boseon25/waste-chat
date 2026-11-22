"""
업체 매칭 3단계 테스트: 처분 업체(treatment) 매칭 — 소각·매립·중간처분.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<프로젝트>/data/reference \
  $PY -m pytest -v partners/test_treatment.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gates import gate4
from gates.models import RouteDraft, Status, Step
from partners import by_treatment
from partners.data import PartnerData
from partners.matcher import match_routes
from preliminary.preliminary_integrator import PreliminaryIntegrator
from gates.tables import Tables

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


def disposal_step(code: str, tcodes: list[str], status: str | None = None):
    status = status or ("designated" if by_treatment.is_designated_code(code) else "general")
    step = Step(2, "처분", "treatment", tcodes, [], [code])
    route = RouteDraft(gate=4, tier="disposal", route_type="alternative_treatment", title="t",
                       waste_codes=[code], waste_status=status,
                       steps=[Step(1, "수집·운반", "transporter", waste_codes=[code]), step],
                       regulatory=Status.CONDITIONAL)
    return route, step


def row(**kw):
    base = {"영업중": "Y", "대상군": "생활·사업장일반", "영업대상_구분": "", "지정_분류번호_앞자리": "",
            "처리방법코드": "2101"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# judge(): 업체 한 곳 판정
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("r, code, reason", [
    (row(영업중="N"), "51-03-01", "영업 중단"),
    (row(대상군="건설"), "51-03-01", "건설폐기물만"),
    (row(영업대상_구분="생활"), "51-03-01", "대상 폐기물 다름"),                    # 생활폐기물만
    (row(영업대상_구분="생활;사업장배출시설계"), "51-03-01", None),
    (row(처리방법코드="2201"), "51-03-01", "처리 방법 다름"),
    (row(), "04-02-00", "대상 폐기물 다름"),                                       # 일반 업체 + 지정폐기물
    (row(대상군="지정", 지정_분류번호_앞자리="01-01;08"), "04-02-00", "대상 폐기물 다름"),
    (row(대상군="지정", 지정_분류번호_앞자리="04;06"), "04-02-00", None),
    (row(대상군="지정", 지정_분류번호_앞자리="*"), "04-02-00", None),
    (row(대상군="지정", 지정_분류번호_앞자리="*"), "10-13-00", "대상 폐기물 다름"),   # 의료는 의료 업체만
    (row(대상군="의료"), "10-13-00", None),
    (row(처리방법코드="2102;2101"), "51-03-01", None),
])
def test_judge(r, code, reason):
    assert by_treatment.judge(r, [code], {"2101", "2102"}) == reason


def test_judge_mixed_needs_every_code():
    r = row(대상군="지정", 지정_분류번호_앞자리="04")
    assert by_treatment.judge(r, ["04-02-00", "06-01-01"], {"2101"}) == "혼합물 일부만 대상"


# ---------------------------------------------------------------------------
# 실제 업체 표
# ---------------------------------------------------------------------------
def test_general_incineration(data, jeonju):
    route, step = disposal_step("51-03-01", ["2101", "2102"])
    found, rejected, questions = by_treatment.match(route, step, data, jeonju)
    assert len(found) > 30
    assert rejected["건설폐기물만"] > 500
    for c in found:
        assert set(c.treatment_codes) & {"2101", "2102"}
        assert "사업장일반" in c.waste_group
    assert any(q["field"] == "acceptance" for q in questions)


def test_landfill(data, jeonju):
    route, step = disposal_step("51-03-01", ["2201", "2205", "2206"])
    found, _, _ = by_treatment.match(route, step, data, jeonju)
    assert found and all(c.treatment_codes == ["2201"] for c in found)    # 지자체·국가 매립지는 업체 표에 없음


def test_designated_scope_by_prefix(data, jeonju):
    route, step = disposal_step("04-02-00", ["2101", "2102"])
    found, _, _ = by_treatment.match(route, step, data, jeonju)
    assert found
    for c in found:
        assert c.waste_group == "지정"
        assert "*" in c.accepts_designated or any("04-02-00".startswith(p) for p in c.accepts_designated)


def test_designated_neutralization_has_no_partner(data, jeonju):
    """중화(2116) 지정 업체는 폐산·폐알칼리 범위라 폐유기용제(04-02)는 받을 수 없다."""
    route, step = disposal_step("04-02-00", ["2109", "2119", "2116"])
    found, rejected, _ = by_treatment.match(route, step, data, jeonju)
    assert not found and rejected["대상 폐기물 다름"] > 0


def test_medical_only_medical_firms(data, jeonju):
    route, step = disposal_step("10-13-00", ["2101", "2102"])
    found, _, _ = by_treatment.match(route, step, data, jeonju)
    assert found and all(c.waste_group == "의료" for c in found)


def test_suspected_general_code(data, jeonju):
    route, step = disposal_step("51-03-01", ["2101", "2102"], status="designated")
    found, _, questions = by_treatment.match(route, step, data, jeonju)
    assert found and any(q["field"] == "designated_analysis" for q in questions)


def test_candidate_fields(data, jeonju):
    route, step = disposal_step("51-03-01", ["2101", "2102"])
    c = by_treatment.match(route, step, data, jeonju)[0][0]
    assert c.company_id and c.name and c.sido and c.source and c.data_date == "2023"
    assert c.permit_type == "허가" and c.registration_type.startswith("중간처분")
    assert c.location and c.location.basis != "없음"


# ---------------------------------------------------------------------------
# matcher + 관문 4
# ---------------------------------------------------------------------------
def test_undecided_disposal_step_is_pending(data, integrator):
    route, step = disposal_step("51-03-01", [])
    case = integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": "51-03-01"},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})
    m = match_routes([route], case, None, data)[0]
    t = m.steps[1]
    assert t.role == "treatment" and not t.candidates and t.questions[0]["field"] == "disposal_method"
    assert m.secured is None


def test_gate4_routes_get_treatment_partners(data, integrator):
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 없음: {SAMPLE}")
    tables = Tables.load(DATA_DIR)
    raw = next(c for c in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"] if c["case_id"] == "DEMO-PART2-01")
    case = integrator.input_adapter.adapt(raw)
    pre = integrator.run(case)
    routes = gate4.run(case, pre, tables)
    matches = match_routes(routes, case, pre, data)
    incin = next(m for r, m in zip(routes, matches) if r.title.startswith("소각"))
    t = next(s for s in incin.steps if s.role == "treatment")
    assert t.candidates and t.candidates[0].sido == "전북"


def test_pretreatment_step_is_optional(data, integrator):
    route, step = disposal_step("51-03-01", ["2201"])
    pre = Step(2, "전처리: 탈수·건조", "treatment", ["2111"], [], ["51-03-01"])
    step.order = 3
    route.steps = [route.steps[0], pre, step]
    case = integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": "51-03-01"},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})
    m = match_routes([route], case, None, data)[0]
    p = m.steps[1]
    assert p.optional and not p.candidates and any(q["field"] == "pretreatment_by" for q in p.questions)
    assert m.steps[2].candidates
