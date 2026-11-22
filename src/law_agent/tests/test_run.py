"""run.py · output/proposal.py 테스트 — 판단 루프를 가짜 상태로 바꿔 끼워 경로 → 출력까지. 업체 매칭은 가짜 매칭 함수."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_agent import run as runner
from law_agent.core.partners.models import Location, PartnerCandidate, RouteMatch, StepMatch
from law_agent.output import proposal as op
from law_agent.state import Question, Verdict, initial_state, load_checklist
from test_composer import FACTS, REF, V, base_verdicts

CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]
TESTSET = {c["case_id"]: c for c in json.loads(
    (Path(__file__).resolve().parents[1] / "eval" / "data" / "preliminary_testset.json").read_text(encoding="utf-8"))["cases"]}
CHECKLIST = load_checklist(Path(__file__).resolve().parents[1] / "config" / "checklist.yaml")

PROPOSAL_KEYS = {  # route_agent proposal/route_proposal.to_route_proposals 의 칸 (파트 3 계약 v1.1)
    "schema_version", "case_id", "case_version", "route_id", "route_version", "stale", "synthetic", "gate",
    "waste_status", "tier", "route_type", "title", "steps", "checks", "check_reasons", "secured", "main_step_order",
    "partner_candidates", "reserve_candidates", "excluded_partners", "waste_ton_month", "questions", "conditions",
    "required_tests", "requirements", "alternatives", "restrictions", "evidences", "related_laws",
    "document_triggers", "comparison", "rejected_summary", "applied_feedback_ids"}


def full_verdicts(**over):
    g1 = V("G-1", value={"text": "적법한 수탁자인지 확인하고 서면으로 계약한다"})
    h1 = V("H-1", value={"criterion": "월 평균 1톤 이상", "case_value": 1.21, "unit": "ton/월", "result": "met"})
    vs = base_verdicts(**{"G-1": g1, "G-2": V("G-2"), "H-1": h1,
                          "D-3": V("D-3", value={"text": "재활용 기준을 지킨다"})})
    vs.update(over)
    return vs


@pytest.fixture
def fake_law(monkeypatch):
    """판단 루프 대신 정해 둔 판단을 돌려준다. 사실 데이터도 가짜."""
    def install(vs):
        def fake_run_law(raw, mode=None, only=None, progress=None, **kw):
            st = initial_state(raw)
            st["questions"] = list(CHECKLIST)
            st["verdicts"] = vs
            st["packs"] = {}
            rounds = [{"round": 1, "ready": 4, "calls": 8, "cached": 0, "escalated": 0,
                       "usage": {"total_tokens": 1000}, "statuses": {"agreed": 4}, "seconds": 0.1, "ids": ["A-1", "D-1"]}]
            if progress:
                progress(rounds[0], st)
            return st, rounds
        monkeypatch.setattr(runner, "run_law", fake_run_law)
        monkeypatch.setattr("law_agent.agents.composer.Facts", lambda: FACTS)
        monkeypatch.setattr(runner, "scope", lambda: {})             # 기존 시험은 전체 경로 (범위 시험은 따로)
    return install


def test_run_case_no_partners(fake_law):
    fake_law(full_verdicts())
    res = runner.run_case(CASES[0])
    assert res["engine"] == "law_agent_v0" and res["case_id"] == "DEMO-PART2-01"
    assert res["preliminary"]["classification"]["normalized_waste_code"] == "51-03-01"
    assert res["preliminary"]["waste_status"] == "general"
    gates = [r["gate"] for r in res["routes"]]
    assert gates == sorted(gates) and 2 in gates and 4 in gates
    assert res["run"]["calls"] == 8 and res["run"]["usage"]["total_tokens"] == 1000
    for p in res["route_proposals"]:
        assert PROPOSAL_KEYS <= set(p), PROPOSAL_KEYS - set(p)
        assert p["engine"] == "law_agent_v0" and p["law_questions"]
    assert res["recommendation"]["waste"]["분류번호"] == "51-03-01"
    assert any(x["question_id"] == "D-1" and x["status"] == "agreed" for x in res["law_review"])
    json.dumps(res, ensure_ascii=False, default=str)                     # 저장 가능


def test_proposal_documents_and_evidences(fake_law):
    fake_law(full_verdicts())
    res = runner.run_case(CASES[0])
    mat = next(p for p in res["route_proposals"] if p["tier"] == "material_recycle")
    trig = {t["trigger"]: t for t in mat["document_triggers"]}
    assert trig["위탁계약서"]["text"].startswith("적법한 수탁자") and trig["위탁계약서"]["condition_id"] == "LA-G-1"
    assert "전자인계 입력 (올바로)" in trig and "사업장폐기물배출자 신고" in trig
    assert trig["사업장폐기물배출자 신고"]["basis"] == REF
    ev = mat["evidences"][0]
    assert ev["ref"] == REF and ev["law"] == "폐기물관리법 시행규칙" and ev["article"] == "별표 4의3"
    assert "D-1" in ev["used_for"] and mat["related_laws"][0]["ref"] == REF
    assert any("재활용 기준" in c["text"] for c in mat["conditions"])
    energy = next(p for p in res["route_proposals"] if p["tier"] == "energy_recovery")
    assert energy["requirements"] and energy["requirements"][0]["result"] == "met"


def test_designated_plan_check_trigger(fake_law):
    h2 = V("H-2", value={"criterion": "월 평균 100kg 이상", "case_value": 1000, "result": "met"})
    h3 = V("H-3", value={"text": "처리계획서를 시·도에 제출"})
    fake_law(full_verdicts(**{"A-2": V("A-2", "yes"), "H-2": h2, "H-3": h3, "D-1": V("D-1", items=["R-6-1"])}))
    res = runner.run_case(CASES[1])
    p = res["route_proposals"][0]
    t = next(t for t in p["document_triggers"] if t["trigger"] == "지정폐기물 처리계획 확인")
    assert t["text"] == "처리계획서를 시·도에 제출" and t["plan_check"]["decision_label"] == "확인 대상"


def test_information_request_includes_unrouted_needs_info(fake_law):
    fake_law(full_verdicts(**{"H-1": V("H-1", "unknown", "needs_info", missing_facts=["업종"]),
                              "D-1": V("D-1", "unknown", "needs_info", missing_facts=["성분"])}))
    res = runner.run_case(CASES[0])
    fields = res["information_request"]["fields"]
    assert "H-1.업종" in fields and "D-1.성분" in fields
    assert "보류" in res["skipped"]["gate2"]["reason"]


def test_bad_input_stops_before_judge(monkeypatch):
    monkeypatch.setattr(runner, "run_law", lambda *a, **k: pytest.fail("판단을 부르면 안 됨"))
    monkeypatch.setattr("law_agent.input_check._exists", lambda c: True)
    res = runner.run_case(TESTSET["PRE-20"])
    assert res["input_check"]["status"] == "needs_review" and res["routes"] == []
    assert res["information_request"]["requests"][0]["field"] == "classification.selected_code"


def test_match_partners_wiring(fake_law, monkeypatch):
    """업체 매칭 결과(RouteMatch)가 경로·RouteProposal·추천 카드에 붙는지 (매칭 함수는 가짜)."""
    def fake_match(routes, case, pre, data, top_n, exclude=None):
        out = []
        for i, r in enumerate(routes):
            rm = RouteMatch(route_index=i)
            for s in r.steps:
                cands = [PartnerCandidate(company_id=f"C{k}|전주시", name=f"업체{k}", role=s.role, sido="전북",
                                          sigungu="전주시", address="가상", phone=None, data_date="2025", source="f.csv#1",
                                          permit_type="허가", location=Location(35.8, 127.1, "업체 좌표", 3.0))
                         for k in range(7)] if s.role != "transporter" else []
                rm.steps.append(StepMatch(step_order=s.order, role=s.role, candidates=cands, total_found=len(cands),
                                          optional=s.role == "transporter"))
            out.append(rm)
        return out
    monkeypatch.setattr("law_agent.core.partners.matcher.match_routes", fake_match)
    fake_law(full_verdicts())
    res = runner.run_case(CASES[0], partner_data=object(), factors=None, top_n=5)
    mat = next(r for r in res["routes"] if r.get("tier") == "material_recycle")
    step = next(s for s in mat["partners"]["steps"] if s["role"] == "recycler")
    assert len(step["candidates"]) == 5 and len(step["reserve"]) == 2 and mat["partners"]["secured"] is True
    p = next(p for p in res["route_proposals"] if p["tier"] == "material_recycle")
    assert len(p["partner_candidates"]) == 5 and len(p["reserve_candidates"]) == 2 and p["questions"]
    card = next(o for o in res["recommendation"]["options"] if o["처리"].startswith("재생이용"))
    assert len(card["추천업체"]) == 5 and card["근거법령"]


def test_cli_dry_no_partners(monkeypatch, tmp_path, capsys):
    from fake_law import build_store
    from law_agent.tools import law_tools
    monkeypatch.setenv("LAW_AGENT_NO_CACHE", "1")
    monkeypatch.setenv("LAW_AGENT_DATA_DIR", str(tmp_path / "data"))
    law_tools.set_store(build_store())
    try:
        assert runner.main([str(Path(__file__).with_name("part1_sample.json")), "--out", str(tmp_path),
                            "--no-partners", "--dry", "--case", "DEMO-PART2-01"]) == 0
    finally:
        law_tools.set_store(None)
    res = json.loads((tmp_path / "DEMO-PART2-01.json").read_text(encoding="utf-8"))
    assert res["engine"] == "law_agent_v0" and res["law_review"]
    out = capsys.readouterr().out
    assert "DEMO-PART2-01" in out and "판단:" in out
    audit = list((tmp_path / "data" / "audit" / "DEMO-PART2-01").glob("*.jsonl"))
    assert len(audit) == 1 and res["run"]["audit_file"] == str(audit[0])
    nodes = [json.loads(ln)["node"] for ln in audit[0].read_text(encoding="utf-8").splitlines()]
    assert {"planner", "retriever", "judge", "verifier", "verdict", "composer", "output"} <= set(nodes)


def test_article_label():
    assert op.article_label("13의2") == "제13조의2" and op.article_label("B5:1~2") == "별표 5"
    assert op.article_label("§Ⅵ") == "Ⅵ" and op.article_label("전체") == ""
    assert op.law_of("WMA_DEC:B1@20261112#x")["law"] == "폐기물관리법 시행령"


def test_compare_routes(tmp_path, capsys):
    from law_agent.eval import compare_routes as cr
    ra = {"route_proposals": [{"route_id": "X-G2-material_recycle-single", "title": "재생이용", "secured": True,
                               "checks": {"regulatory": "conditional"}, "partner_candidates": [{"name": "갑"}]}],
          "skipped": {"gate1": {"action": "SKIP", "reason": "지정"}}}
    la = {"route_proposals": [{"route_id": "X-G2-material_recycle-single", "title": "재생이용", "secured": True,
                               "checks": {"regulatory": "confirmed"}, "partner_candidates": [{"name": "갑"}]},
                              {"route_id": "X-G4-incineration-alternative_treatment", "title": "소각", "secured": None,
                               "checks": {"regulatory": "conditional"}}]}
    rows = cr.compare(ra, la)
    assert rows[0]["danger"] and rows[1]["route_agent"] is None
    (tmp_path / "ra").mkdir(); (tmp_path / "la").mkdir()
    (tmp_path / "ra" / "X.json").write_text(json.dumps(ra), encoding="utf-8")
    (tmp_path / "la" / "X.json").write_text(json.dumps(la), encoding="utf-8")
    assert cr.main([str(tmp_path / "ra"), str(tmp_path / "la")]) == 0
    out = capsys.readouterr().out
    assert "◀ 위험" in out and "위험 1" in out


def test_inspect_laws(capsys):
    from fake_law import build_store
    from law_agent.eval import inspect_laws as il
    from law_agent.tools import law_tools
    law_tools.set_store(build_store())
    try:
        assert il.main([]) == 0 and il.main(["--outline", "WMA_RULE", "B4의3"]) == 0
    finally:
        law_tools.set_store(None)
    out = capsys.readouterr().out
    assert "문제" in out and "조각" in out


def test_verbose_and_show_result(fake_law, tmp_path, capsys):
    from law_agent.eval import show_result
    fake_law(full_verdicts())
    res = runner.run_case(CASES[0], verbose=True)
    out = capsys.readouterr().out
    assert "[1] 입력 검사: ok" in out and "[2] 법률 매칭" in out and "[3] 경로" in out
    res["route_proposals"][0]["evidences"].append({"evidence_id": "DATA-1", "source": "업체 표 f.csv#1"})  # law 없는 데이터 근거
    p = tmp_path / "r.json"
    p.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    assert show_result.main([str(p)]) == 0 and show_result.main([str(p), "--q", "D-1"]) == 0
    assert show_result.main([str(p), "--routes"]) == 0
    out = capsys.readouterr().out
    assert "[판단]" in out and "[경로]" in out and "단계:" in out and "질문: " in out


def test_information_request_merges_same_fact():
    ir = {"requests": [{"field": "A-1.할로겐족 유기용제 함유 여부", "text": "a", "to": "배출업체", "affected_routes": ["r1"]},
                       {"field": "F-1.할로겐족 유기용제 함유 여부", "text": "b", "to": "배출업체", "affected_routes": ["r2"]},
                       {"field": "F-2.할로겐족  유기용제 함유 여부", "text": "c", "to": "배출업체", "affected_routes": []},
                       {"field": "separable", "text": "나눌 수 있나요?", "to": "배출업체", "affected_routes": []}],
          "fields": []}
    out = op.merge_same_facts(ir)
    assert len(out["requests"]) == 2 and out["requests"][0]["question_ids"] == ["A-1", "F-1", "F-2"]
    assert out["requests"][0]["affected_routes"] == ["r1", "r2"] and "A-1, F-1, F-2" in out["requests"][0]["text"]
    assert [r["request_id"] for r in out["requests"]] == ["IR-1", "IR-2"]



def test_route_scope_keeps_only_listed_tiers(fake_law, monkeypatch):
    """서비스 범위(settings scope.route_tiers): 목록에 없는 경로는 빼고 skipped 에 이유."""
    fake_law(full_verdicts())
    monkeypatch.setattr(runner, "scope", lambda: {"route_tiers": ["material_recycle"]})
    res = runner.run_case(CASES[0])
    assert {r["tier"] for r in res["routes"]} == {"material_recycle"}
    assert "범위 밖" in res["skipped"]["scope"]["reason"] and res["route_proposals"]


def test_partner_registration_scope():
    from law_agent.core.partners.data import PartnerData
    rows = [{"업체_키": "A", "분류번호_중분류": "51-03", "등록형태": "중간 재활용업"},
            {"업체_키": "B", "분류번호_중분류": "51-03", "등록형태": "종합 재활용업"},
            {"업체_키": "C", "분류번호_중분류": "51-03", "등록형태": "폐기물처리 신고자"}]
    d = PartnerData(recycling=rows).only_registration(["중간 재활용업"])
    assert [r["업체_키"] for r in d.recycling] == ["A"] and list(d.recycling_by_company) == ["A"]
    assert len(PartnerData(recycling=rows).only_registration([]).recycling) == 3
