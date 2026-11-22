"""
업체 매칭 2단계 테스트: 재활용 업체(recycler / preprocessor) 매칭, 정렬, matcher.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<프로젝트>/data/reference \
  $PY -m pytest -v partners/test_recycler.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from gates import gate2
from gates.models import RouteDraft, Status, Step
from gates.tables import Tables
from partners import by_recycler
from partners.data import PartnerData
from partners.matcher import match_routes
from partners.models import Location, PartnerCandidate
from partners.rank import rank
from preliminary.preliminary_integrator import PreliminaryIntegrator

DATA_DIR = Path(os.environ["WASTE_DATA_DIR"])
SAMPLE = Path(os.environ.get("PART1_SAMPLE", Path(__file__).resolve().parent.parent / "preliminary" / "part1_sample.json"))


@pytest.fixture(scope="module")
def data():
    return PartnerData.load()


@pytest.fixture(scope="module")
def tables():
    return Tables.load(DATA_DIR)


@pytest.fixture(scope="module")
def integrator():
    return PreliminaryIntegrator(DATA_DIR)


@pytest.fixture(scope="module")
def jeonju(data):
    return data.locator.origin("전북", "전주시")


def core(name: str) -> str:
    return re.sub(r"\(주\)|\(유\)|주식회사|㈜|\s", "", name)


def one_step(code: str, r_code: str, tables, designated=None, role="recycler"):
    """분류번호 + R코드 하나로 경로 1개·단계 1개를 만든다."""
    top = "-".join(r_code.split("-")[:2])
    designated = (code in tables.designated_codes) if designated is None else designated
    route = RouteDraft(gate=2, tier=None, route_type="single_material_recycle", title="t",
                       waste_codes=code.split("+"), waste_status="designated" if designated else "general",
                       steps=[], regulatory=Status.CONFIRMED)
    step = Step(1, "재활용", role, [tables.rcode_to_tcode[top]], [r_code], [code])
    route.steps = [step]
    return route, step


# ---------------------------------------------------------------------------
# judge(): 업체 한 곳 판정
# ---------------------------------------------------------------------------
def row(**kw):
    base = {"위탁가능": "Y", "폐기물_구분": "사업장일반", "처리방법코드": "2003", "r_code": "R-3"}
    base.update(kw)
    return base


@pytest.mark.parametrize("rows, reason", [
    ([[row(위탁가능="N")]], "자가처리만 신고"),
    ([[row(폐기물_구분="지정")]], "지정/일반 범위 밖"),
    ([[row(처리방법코드="2009")]], "처리 방법 다름"),
    ([[row(r_code="R-4")]], "재활용 유형 다름"),
    ([[row()]], None),
])
def test_judge_single(rows, reason):
    assert by_recycler.judge(rows, False, {"2003"}, {"R-3"})[0] == reason


def test_judge_mixed_needs_every_component():
    ok, only_one = [row()], [row(처리방법코드="2009")]
    assert by_recycler.judge([ok, ok], False, {"2003"}, {"R-3"})[0] is None
    assert by_recycler.judge([ok, only_one], False, {"2003"}, {"R-3"})[0] == "혼합물 일부만 허가"
    assert by_recycler.judge([ok, []], False, {"2003"}, {"R-3"})[0] == "혼합물 일부만 허가"
    assert by_recycler.judge([only_one, only_one], False, {"2003"}, {"R-3"})[0] == "처리 방법 다름"


def test_judge_designated_needs_designated_rows():
    assert by_recycler.judge([[row(폐기물_구분="지정")]], True, {"2003"}, {"R-3"})[0] is None
    assert by_recycler.judge([[row()]], True, {"2003"}, {"R-3"})[0] == "지정/일반 범위 밖"


# ---------------------------------------------------------------------------
# 파트 1 샘플: 사전검증 → 관문 2 → 업체 매칭
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sample_matches(integrator, tables, data):
    if not SAMPLE.is_file():
        pytest.skip(f"파트 1 샘플 없음: {SAMPLE}")
    out = {}
    for raw in json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]:
        case = integrator.input_adapter.adapt(raw)
        pre = integrator.run(case)
        routes = gate2.run(case, pre, tables)
        out[raw["case_id"]] = (routes, match_routes(routes, case, pre, data))
    return out


def recycler_steps(routes, matches):
    for r, m in zip(routes, matches):
        for s in m.steps:
            if s.role in ("recycler", "preprocessor"):
                yield r, s


def test_sample1_pe_film_local_first(sample_matches):
    routes, matches = sample_matches["DEMO-PART2-01"]
    steps = {r.tier: s for r, s in recycler_steps(routes, matches)}
    s = steps["material_recycle"]
    assert s.total_found > 100 and len(s.candidates) == 5
    for c in s.candidates:
        assert c.sido == "전북" and c.sigungu.startswith("전주시")      # 같은 시군구가 먼저
        assert "2003" in c.treatment_codes and "R-3" in c.r_codes
    assert "처리 방법 다름" in s.rejected_summary


def test_sample2_solvent_designated_only(sample_matches, data):
    routes, matches = sample_matches["DEMO-PART2-02"]
    for r, s in recycler_steps(routes, matches):
        assert r.waste_status == "designated"
        assert s.candidates
        for c in s.candidates:
            assert c.accepts_designated                              # 지정폐기물 위탁 허가가 있는 업체만
            rows = data.recycling_by_company[c.company_id]
            assert any(x["폐기물_구분"] == "지정" for x in rows)


def test_candidate_fields_filled(sample_matches):
    routes, matches = sample_matches["DEMO-PART2-01"]
    c = next(recycler_steps(routes, matches))[1].candidates[0]
    d = c.to_dict() if hasattr(c, "to_dict") else c.__dict__
    for k in ("company_id", "name", "sido", "sigungu", "address", "data_date", "source", "permit_type"):
        assert d[k], k
    assert c.location.distance_km is not None and c.location.basis in ("업체 좌표", "시군구 중심점")
    assert c.reasons


def test_route_match_serializes(sample_matches):
    _, matches = sample_matches["DEMO-PART2-01"]
    json.dumps([m.to_dict() for m in matches], ensure_ascii=False)


# ---------------------------------------------------------------------------
# 정렬
# ---------------------------------------------------------------------------
def cand(name, sido, sgg, km, permit="허가"):
    return PartnerCandidate(company_id=name, name=name, role="recycler", sido=sido, sigungu=sgg,
                            address="", phone=None, data_date="", source="", permit_type=permit,
                            location=Location(None, None, "x", km))


def test_rank_order():
    cs = [cand("far_same_sido", "전북", "군산시", 30), cand("other_sido_near", "충남", "논산시", 20),
          cand("near_report", "전북", "전주시 덕진구", 3, "신고"), cand("near_permit", "전북", "전주시 완산구", 3),
          cand("no_loc", "전북", "익산시", None)]
    names = [c.name for c in rank(cs, "전북", "전주시", top_n=0)]
    # 거리순 (시도 경계 무시) → 같은 거리면 허가 먼저 → 위치 모르면 맨 뒤
    assert names == ["near_permit", "near_report", "other_sido_near", "far_same_sido", "no_loc"]
    assert len(rank(cs, "전북", "전주시", top_n=2)) == 2


# ---------------------------------------------------------------------------
# matcher
# ---------------------------------------------------------------------------
def test_rejected_route_not_matched(tables, data, integrator):
    route, _ = one_step("51-03-01", "R-3-3", tables)
    route.regulatory = Status.REJECTED
    case = integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": "51-03-01"},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})
    m = match_routes([route], case, None, data)[0]
    assert m.steps == []


def test_unconnected_role_is_placeholder(tables, data, integrator):
    route, step = one_step("51-03-01", "R-3-3", tables)
    route.steps = [Step(1, "아직 없는 역할", "unknown_role", [], [], ["51-03-01"]), step]
    case = integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": "51-03-01"},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})
    m = match_routes([route], case, None, data)[0]
    t, r = m.steps
    assert t.role == "unknown_role" and not t.candidates and m.secured is None
    assert r.role == "recycler" and r.candidates


def test_mixed_codes_need_both_groups(tables, data, jeonju):
    route, step = one_step("51-03-01+51-28-02", "R-3-3", tables)
    found, rejected, _ = by_recycler.match(route, step, data, jeonju)
    assert found and rejected["혼합물 일부만 허가"] > 0
    for c in found:
        groups = {x["분류번호_중분류"] for x in data.recycling_by_company[c.company_id]
                  if x["위탁가능"] == "Y" and x["처리방법코드"] == "2003"}
        assert {"51-03", "51-28"} <= groups


# ---------------------------------------------------------------------------
# 회귀 사례 (tests/regress_cases.csv 의 실제 상대기업)
#   (사례, 분류번호, R코드, 상대기업, 기대)
#   found   = 매칭 결과에 들어가야 함 (허가 업체)
#   report  = 들어가되 신고 업체 → 신고 범위·하루 처리량 질문이 붙어야 함
#   excluded:이유 = 빠져야 하고, 그 이유
#   absent  = 해당 분류의 업체 목록에 아예 없음
# ---------------------------------------------------------------------------
REGRESS = [
    ("S03", "51-19-00", "R-5-2", "대한상사(주)", "found"),
    ("S09", "51-03-01", "R-3-3", "(유)미래환경수지", "found"),
    ("S25", "51-29-02", "R-4-1", "(주)고려비철공업", "found"),
    ("S30", "51-16-00", "R-9-2", "(주)DS단석", "found"),
    ("S40", "51-20-24", "R-9-1", "주식회사 장적골", "found"),
    ("S41", "51-20-23", "R-2-1", "명일목재산업", "found"),
    ("S42", "51-03-01", "R-9-1", "한국에너지(주)", "found"),
    ("S45", "51-03-01", "R-4-4", "신원기업(주)", "found"),
    ("S46", "06-01-01", "R-9-2", "(주)범창", "found"),
    ("S10", "51-03-01", "R-3-3", "(유)에이앤티", "report"),
    ("S19", "51-20-23", "R-2-1", "(유)신그린", "report"),
    ("S22", "51-28-02", "R-10-1", "(주)태웅씨엔에스", "report"),
    ("S23", "51-29-01", "R-4-1", "(주)승지", "report"),        # 신고 범위 N: 공개 자료로는 못 거름 → 질문
    ("S29", "51-14-02", "R-7-1", "김재근", "report"),
    ("S36", "51-19-00", "R-5-1", "(주)에스엠비", "report"),     # S36~38: 하루 처리량 5 / 15 / ? 톤 → 질문
    ("S43", "51-03-01", "R-4-4", "인다산업", "report"),
    ("S28", "51-30-02", "R-2-1", "형제공병", "excluded:처리 방법 다름"),     # 2001만 신고
    ("S44", "51-03-01", "R-3-3", "(주) 대영폴리머", "excluded:자가처리만 신고"),
    ("S47", "06-01-03", "R-9-2", "대한상사(주)", "absent"),     # 일반 업체에 지정폐기물
]


@pytest.mark.parametrize("case_id, code, r_code, partner, expect", REGRESS, ids=[r[0] for r in REGRESS])
def test_regress_partner(case_id, code, r_code, partner, expect, tables, data, jeonju):
    route, step = one_step(code, r_code, tables)
    found, _, questions = by_recycler.match(route, step, data, jeonju)
    hit = [c for c in found if core(c.name) == core(partner)]

    if expect == "found":
        assert hit, f"{partner} 없음"
        assert hit[0].permit_type == "허가"
    elif expect == "report":
        assert hit and hit[0].permit_type == "신고"
        assert any(q["field"] == "report_scope" for q in questions)
    elif expect == "absent":
        assert not hit
        keys = {k for k, rows in data.recycling_by_company.items()
                if core(rows[0]["업체명"]) == core(partner)}
        for k in keys:
            assert all(r["폐기물_구분"] != "지정" for r in data.recycling_by_company[k])
    else:
        reason = expect.split(":", 1)[1]
        assert not hit
        keys = [k for k, rows in data.recycling_by_company.items() if core(rows[0]["업체명"]) == core(partner)]
        assert keys
        groups = by_recycler._groups_for(code)
        rows = [r for r in data.recycling_by_company[keys[0]] if r["분류번호_중분류"] in groups]
        top = {"-".join(r_code.split("-")[:2])}
        got, _ = by_recycler.judge([rows], route.waste_status == "designated", set(step.treatment_codes), top)
        assert got == reason


def test_secured_states(tables, data, integrator):
    case = integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": "51-03-01"},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})
    route, _ = one_step("51-03-01", "R-3-3", tables)
    assert match_routes([route], case, None, data)[0].secured is True
    bad, _ = one_step("51-03-01", "R-3-3", tables)
    bad.steps[0].treatment_codes = ["9999"]
    assert match_routes([bad], case, None, data)[0].secured is False
    route.regulatory = Status.REJECTED
    assert match_routes([route], case, None, data)[0].secured is None


# ---------------------------------------------------------------------------
# 테스트 세트에서 나온 경우: 지정 의심, 지정+일반 혼합, 선별 단계
# ---------------------------------------------------------------------------
def test_suspected_general_code_matches_general_firms(tables, data, jeonju):
    route, step = one_step("51-03-01", "R-3-3", tables, designated=True)     # 일반 분류번호 + 지정 의심
    found, rejected, questions = by_recycler.match(route, step, data, jeonju)
    assert len(found) > 100
    assert any(q["field"] == "designated_analysis" for q in questions)


def test_designation_per_component(tables, data, jeonju):
    route, step = one_step("51-03-01+01-01-01", "R-3-3", tables, designated=True)
    found, _, questions = by_recycler.match(route, step, data, jeonju)
    assert not any(q["field"] == "designated_analysis" for q in questions)
    for c in found:
        rows = data.recycling_by_company[c.company_id]
        assert any(r["분류번호_중분류"] == "51-03" and r["폐기물_구분"] != "지정" for r in rows)
        assert any(r["분류번호_중분류"].startswith("01") and r["폐기물_구분"] == "지정" for r in rows)


def test_separation_step_is_question(tables, data, integrator):
    route, step = one_step("51-03-01", "R-3-3", tables)
    route.steps = [Step(1, "재질별 선별·분리", "preprocessor", [], [], ["51-03-01"]), step]
    case = integrator.input_adapter.adapt({"case_id": "T", "classification": {"selected_code": "51-03-01"},
                                           "region": {"sido": "전북", "sigungu": "전주시"}})
    m = match_routes([route], case, None, data)[0]
    sep, rec = m.steps
    assert sep.optional and not sep.candidates and sep.questions[0]["field"] == "separation_by"
    assert rec.candidates and m.secured is True
