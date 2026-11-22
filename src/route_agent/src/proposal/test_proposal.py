"""
proposal 테스트: RouteProposal v1.1 형식, checks 세 칸, 질문 field, 근거, 필요 서류, InformationRequest, 업체 회신.

실행 (src/route_agent/src 에서):
  WASTE_DATA_DIR=<…/data/tables/active> WASTE_REFERENCE_DIR=<중심점·탄소 계수 폴더> \
  PART1_SAMPLE=preliminary/part1_sample.json PRELIM_TESTSET=preliminary/testdata/preliminary_testset.json \
  PYTHONPATH=.:pipline $PY -m pytest -v proposal/test_proposal.py
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from pipeline import Pipeline
from proposal import apply_feedback, mark_stale
from proposal.questions import FIELDS

SAMPLE = Path(os.environ.get("PART1_SAMPLE", "preliminary/part1_sample.json"))
TESTSET = Path(os.environ.get("PRELIM_TESTSET", "preliminary/testdata/preliminary_testset.json"))

# 1.0(11.12) 필드 — 1.1에서도 빠지면 안 됨 (파트 3 호환)
V10_FIELDS = {"schema_version", "case_id", "case_version", "route_id", "route_version", "stale", "synthetic", "gate",
              "waste_status", "tier", "route_type", "steps", "partner_candidates", "questions", "checks",
              "required_tests", "restrictions", "evidences", "rejected_summary"}
V11_FIELDS = V10_FIELDS | {"title", "secured", "conditions", "document_triggers", "comparison"}
V10_PARTNER = {"company_id", "name", "role", "permit_type", "registration_type", "waste_group", "treatment_codes",
               "r_codes", "accepts_designated", "address", "phone", "data_date", "source", "reasons"}


def _cases(p: Path) -> list[dict]:
    return json.loads(p.read_text(encoding="utf-8"))["cases"] if p.is_file() else []


@pytest.fixture(scope="module")
def pipe():
    return Pipeline()


@pytest.fixture(scope="module")
def results(pipe):
    raws = _cases(SAMPLE) + _cases(TESTSET)
    if not raws:
        pytest.skip("입력 파일 없음")
    return {r["case_id"]: pipe.run_case(r) for r in raws}


def all_props(results):
    return [p for res in results.values() for p in res["route_proposals"]]


# ---------------------------------------------------------------------------
# 형식
# ---------------------------------------------------------------------------
def test_one_proposal_per_route_and_fields(results):
    for res in results.values():
        assert len(res["route_proposals"]) == len(res["routes"])
        for p in res["route_proposals"]:
            assert V11_FIELDS <= set(p), V11_FIELDS - set(p)
            assert p["schema_version"] == "1.1"
            for s in p["steps"]:
                assert {"order", "process", "role", "treatment_code", "r_code", "treatment_codes", "r_codes",
                        "optional"} <= set(s)
            for c in p["partner_candidates"]:
                assert V10_PARTNER <= set(c) and c["rank"] >= 1 and c["status"] == "needs_info"
        json.dumps(res["route_proposals"], ensure_ascii=False)


def test_route_ids_unique_and_stable(pipe, results):
    for res in results.values():
        ids = [p["route_id"] for p in res["route_proposals"]]
        assert len(ids) == len(set(ids))
    raw = _cases(SAMPLE)[0]
    a = [p["route_id"] for p in pipe.run_case(raw)["route_proposals"]]
    b = [p["route_id"] for p in pipe.run_case(raw)["route_proposals"]]
    assert a == b                                              # 다시 돌려도 같은 번호 (회신 연결)


def test_partner_candidates_main_step_first_in_rank_order(results):
    for p in all_props(results):
        mains = [c for c in p["partner_candidates"] if c["step_order"] == p["main_step_order"]]
        assert [c["rank"] for c in mains] == list(range(1, len(mains) + 1))
        if mains:                                                   # 주 업체가 있으면 목록 맨 앞
            assert p["partner_candidates"][:len(mains)] == mains
            assert all(c["role"] != "transporter" for c in mains)
        elif p["main_step_order"] is not None and p["secured"] is not None:
            assert p["secured"] is False                            # 주 업체가 없으면 미확보


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def test_checks_three_separate(results):
    for p in all_props(results):
        assert set(p["checks"]) == {"technical", "regulatory", "acceptance"}
        assert p["checks"]["acceptance"] == "needs_info"                    # 회신 전
        assert p["checks"]["technical"] in ("needs_info", "conditional")    # 사례 DB 없음 → 확정 없음


def test_gate1_technical_conditional(results):
    g1 = [p for p in all_props(results) if p["gate"] == 1 and p["checks"]["regulatory"] == "confirmed"]
    assert g1 and all(p["checks"]["technical"] == "conditional" for p in g1)


# ---------------------------------------------------------------------------
# 질문
# ---------------------------------------------------------------------------
def test_questions_use_contract_fields(results):
    for p in all_props(results):
        ids = [q["question_id"] for q in p["questions"]]
        assert ids == [f"Q-{i + 1}" for i in range(len(ids))]
        for q in p["questions"]:
            assert q["field"] in FIELDS, q
            assert q["to_role"] != "transporter"


def test_report_scope_only_when_report_partner(results):
    for p in all_props(results):
        has_report = any(c["permit_type"] == "신고" and c["step_order"] == p["partner_candidates"][0]["step_order"]
                         for c in p["partner_candidates"]) if p["partner_candidates"] else False
        asked = any(q["field"] == "report_scope" for q in p["questions"])
        if asked:
            assert has_report or any("신고" in q["text"] for q in p["questions"])


def test_info_request_has_only_emitter_questions(results):
    for res in results.values():
        ir = res["information_request"]
        rids = {p["route_id"] for p in res["route_proposals"]}
        for r in ir["requests"]:
            assert r["field"] not in FIELDS                                 # 업체 질문이 섞이지 않음
            assert set(r["affected_routes"]) <= rids
    demo = results["DEMO-PART2-01"]["information_request"]
    assert "emitter_facility_type" in demo["fields"]


# ---------------------------------------------------------------------------
# 근거, 서류, 비교
# ---------------------------------------------------------------------------
def test_evidences_have_law_and_partner(results):
    p = results["DEMO-PART2-01"]["route_proposals"][1]
    law = next(e for e in p["evidences"] if e["evidence_id"].startswith("LAW-WMA_RULE-B4의3"))
    assert law["law"] == "폐기물관리법 시행규칙" and law["ref"]
    assert law["ref"].startswith("WMA_RULE:B4의3@" + law["effective_date"].replace("-", ""))   # 시행일은 원문판에 따라 바뀜
    assert any(e["evidence_id"].startswith("PARTNER-") and e["ref"] for e in p["evidences"])


def test_document_triggers(results):
    general = results["DEMO-PART2-01"]["route_proposals"][1]
    t = {d["trigger"] for d in general["document_triggers"]}
    assert {"위탁계약서", "수탁처리능력 확인", "전자인계 입력 (올바로)"} <= t
    assert all(d["basis"] for d in general["document_triggers"])
    designated = results["DEMO-PART2-02"]["route_proposals"][0]
    assert "지정폐기물 처리계획 확인" in {d["trigger"] for d in designated["document_triggers"]}
    for p in all_props(results):
        if p["gate"] == 1:
            assert [d["trigger"] for d in p["document_triggers"]] == ["폐기물 위탁 서류 해당 없음"]


def test_paper_scrap_exempt_from_e_transfer(results):
    """폐지·고철(비철 포함) 일반폐기물 위탁은 전자인계 제외, 그 밖의 위탁은 전자인계 대상"""
    exempt = {"51-28-02", "51-29-01", "51-29-02"}
    checked = 0
    for res in results.values():
        for r, p in zip(res["routes"], res["route_proposals"]):
            if p["gate"] not in (2, 4) or r["regulatory"] == "rejected":
                continue
            t = {d["trigger"] for d in p["document_triggers"]}
            if p["waste_status"] == "general" and set(r["waste_codes"]) <= exempt:
                assert "전자인계 제외" in t and "전자인계 입력 (올바로)" not in t
                checked += 1
            else:
                assert "전자인계 입력 (올바로)" in t
    assert checked > 0                                             # 시험 세트에 폐지·고철 위탁 경로가 있음


def test_comparison_nulls(results):
    for p in all_props(results):
        c = p["comparison"]
        assert c["carbon_total"] is None and c["carbon_saving"] is None and c["cost_total"] is None
        assert "원료 대체 계수" in c["missing"]
    p = results["DEMO-PART2-01"]["route_proposals"][1]
    assert p["comparison"]["carbon_transport"] == p["partner_candidates"][0]["carbon"]["transport_kgco2e"]


# ---------------------------------------------------------------------------
# 업체 회신, stale
# ---------------------------------------------------------------------------
def test_feedback_accept_and_idempotent(results):
    props = copy.deepcopy(results["DEMO-PART2-01"]["route_proposals"])
    p = props[1]
    cid = p["partner_candidates"][0]["company_id"]
    fb = {"feedback_id": "FB-1", "route_id": p["route_id"], "company_id": cid, "acceptance": "accepted",
          "answers": {"min_quantity": "1톤"}, "quote": {"amount": 150000, "unit": "원/톤"}, "responded_at": "2026-11-15"}
    apply_feedback(props, [fb])
    assert p["checks"]["acceptance"] == "confirmed" and p["route_version"] == 2
    assert p["partner_candidates"][0]["answers"]["min_quantity"] == "1톤"
    assert "quote" not in p["comparison"]["missing"] and p["comparison"]["quotes"][0]["amount"] == 150000
    apply_feedback(props, [fb])                                    # 같은 회신 다시 → 변화 없음
    assert p["route_version"] == 2


def test_feedback_all_rejected(results):
    """주 후보와 예비 후보가 모두 거절하면 acceptance = rejected, 다른 업체 검색"""
    props = copy.deepcopy(results["DEMO-PART2-01"]["route_proposals"])
    p = props[1]
    ids = [c["company_id"] for c in p["partner_candidates"] if c["step_order"] == p["main_step_order"]]
    ids += [c["company_id"] for c in p["reserve_candidates"]]
    apply_feedback(props, [{"feedback_id": f"FB-{i}", "route_id": p["route_id"], "company_id": cid,
                            "acceptance": "rejected"} for i, cid in enumerate(ids)])
    assert p["checks"]["acceptance"] == "rejected" and p["next_action"] == "다른 업체 검색"
    assert len(p["excluded_partners"]) == len(ids)


def test_feedback_unknown_company_recorded(results):
    props = copy.deepcopy(results["DEMO-PART2-01"]["route_proposals"])
    p = props[1]
    apply_feedback(props, [{"feedback_id": "FB-X", "route_id": p["route_id"], "company_id": "없는업체", "acceptance": "accepted"}])
    assert p["unmatched_feedback"] == ["FB-X"] and p["route_version"] == 1


def test_mark_stale(results):
    props = copy.deepcopy(results["DEMO-PART2-01"]["route_proposals"])
    assert not any(p["stale"] for p in mark_stale(props, 1))
    assert all(p["stale"] for p in mark_stale(props, 2))


def test_evidence_text_from_law_store(pipe, results):
    """WASTE_LAW_DIR이 있으면 근거 원문 발췌가 들어감 (별표 4의3은 분류번호 행만)"""
    if pipe.law_store is None:
        pytest.skip("WASTE_LAW_DIR 없음")
    p = results["DEMO-PART2-01"]["route_proposals"][1]
    ev = next(e for e in p["evidences"] if e["evidence_id"].startswith("LAW-WMA_RULE-B4의3"))
    # 판정표 근거 키 시행일 ≠ 저장 조문 시행일이면 "(…재확인 필요)"가 붙는다 → 표를 새 원문으로 다시 만들라는 신호
    assert ev["text_basis"].startswith("분류번호 행 발췌") and ev["text"].startswith("51-03-01 | 폐합성수지류")
