"""업체 회신 반영 재계산 테스트 (거절 → 예비 후보, 물량 초과, 시험 요구, 직접 운반, 허가증 확인, 재매칭)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from pipeline import Pipeline
from proposal import apply_feedback

SAMPLE = Path(os.environ.get("PART1_SAMPLE", "preliminary/part1_sample.json"))


@pytest.fixture(scope="module")
def pipe():
    return Pipeline()


@pytest.fixture(scope="module")
def raw():
    if not SAMPLE.is_file():
        pytest.skip("파트 1 샘플 없음")
    return json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"][0]          # PE 필름, 월 1.21톤


@pytest.fixture(scope="module")
def base(pipe, raw):
    return pipe.run_case(raw)


def route(props, tier="material_recycle"):
    return next(p for p in props if p["gate"] == 2 and p["tier"] == tier)


def mains(p):
    return [c for c in p["partner_candidates"] if c["step_order"] == p["main_step_order"]]


def fb(p, cid, acc, n=1, **kw):
    return {"feedback_id": f"FB-{cid}-{n}", "route_id": p["route_id"], "company_id": cid, "acceptance": acc, **kw}


def test_reserve_candidates_exist(base):
    p = route(base["route_proposals"])
    assert len(mains(p)) == 5 and len(p["reserve_candidates"]) == 5
    assert [c["rank"] for c in p["reserve_candidates"]] == [6, 7, 8, 9, 10]
    assert all(c.get("carbon") for c in p["reserve_candidates"])
    assert p["waste_ton_month"] == 1.21


def test_reject_promotes_reserve(base):
    props = copy.deepcopy(base["route_proposals"])
    p = route(props)
    first, nxt = mains(p)[0], p["reserve_candidates"][0]
    apply_feedback(props, [fb(p, first["company_id"], "rejected", rejection_reason="51-03-01 허가 없음")])
    m = mains(p)
    assert first["company_id"] not in [c["company_id"] for c in m]
    assert m[-1]["company_id"] == nxt["company_id"] and [c["rank"] for c in m] == [1, 2, 3, 4, 5]
    assert p["excluded_partners"][0]["reason"] == "51-03-01 허가 없음"
    assert p["promoted"][0]["replaces"] == first["company_id"]
    assert p["checks"]["acceptance"] == "needs_info" and p["route_version"] == 2 and p["next_action"] is None


def test_capacity_over(base):
    props = copy.deepcopy(base["route_proposals"])
    p = route(props)
    cid = mains(p)[0]["company_id"]
    apply_feedback(props, [fb(p, cid, "accepted", capacity_ton_month=0.5)])
    c = mains(p)[0]
    assert "물량 초과" in c["flags"] and c["status"] == "conditional"
    assert p["checks"]["acceptance"] == "conditional"


def test_required_test_goes_to_emitter(pipe, raw, base):
    props = copy.deepcopy(base["route_proposals"])
    p = route(props)
    cid = mains(p)[0]["company_id"]
    apply_feedback(props, [fb(p, cid, "conditional", required_tests=["용출시험"])])
    assert any(t["test"] == "용출시험" for t in p["required_tests"])
    assert p["emitter_requests"][0]["field"] == "partner_required_test"
    res = pipe.run_case(raw, [fb(route(base["route_proposals"]), cid, "conditional", required_tests=["용출시험"])])
    ir = res["information_request"]
    assert "partner_required_test" in ir["fields"]


def test_direct_transport_makes_transport_optional(base):
    props = copy.deepcopy(base["route_proposals"])
    p = route(props)
    apply_feedback(props, [fb(p, mains(p)[0]["company_id"], "accepted", transport_by="업체 직접 수거")])
    assert all(s["optional"] for s in p["steps"] if s["role"] == "transporter")
    assert "직접 운반" in p["transport_note"]


def test_license_checked_adds_evidence(base):
    props = copy.deepcopy(base["route_proposals"])
    p = route(props)
    c = mains(p)[0]
    apply_feedback(props, [fb(p, c["company_id"], "accepted", license_checked=True, responded_by="영업팀")])
    assert c["verified"] is True
    assert any(e["evidence_id"].startswith("FEEDBACK-") and e["responded_by"] == "영업팀" for e in p["evidences"])
    assert p["checks"]["acceptance"] == "confirmed"


def test_same_company_second_reply_overrides(base):
    props = copy.deepcopy(base["route_proposals"])
    p = route(props)
    cid = mains(p)[0]["company_id"]
    apply_feedback(props, [fb(p, cid, "conditional", 1, quote={"amount": 150000, "unit": "원/톤"})])
    apply_feedback(props, [fb(p, cid, "accepted", 2, quote={"amount": 120000, "unit": "원/톤"})])
    c = mains(p)[0]
    assert c["status"] == "confirmed" and len(c["feedback_history"]) == 2
    assert [q["amount"] for q in p["comparison"]["quotes"]] == [120000]
    assert p["route_version"] == 3


def test_rerun_with_rejection_excludes_from_matching(pipe, raw, base):
    p0 = route(base["route_proposals"])
    cid = mains(p0)[0]["company_id"]
    res = pipe.run_case(raw, [fb(p0, cid, "rejected", rejection_reason="여력 없음")])
    p = route(res["route_proposals"])
    assert cid not in [c["company_id"] for c in p["partner_candidates"] + p["reserve_candidates"]]
    assert len(mains(p)) == 5
    assert p["excluded_partners"][0]["company_id"] == cid
    assert p["rejected_summary"].get("업체 거절 (회신)") == 1
