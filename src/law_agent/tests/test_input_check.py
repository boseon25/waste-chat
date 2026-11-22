"""input_check.py 테스트 — 시험세트 24건의 입력 문제 건 (PRE-17~22)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_agent import input_check as ic

TESTSET = Path(__file__).resolve().parents[1] / "eval" / "data" / "preliminary_testset.json"
CASES = {c["case_id"]: c for c in json.loads(TESTSET.read_text(encoding="utf-8"))["cases"]}


@pytest.fixture(autouse=True)
def no_tables(monkeypatch):
    # 분류표 존재 검사: 99-99-99 만 없다고 본다 (서버 표 없이)
    monkeypatch.setattr(ic, "_exists", lambda c: c != "99-99-99")


@pytest.mark.parametrize("cid,status", [
    ("PRE-01", "ok"), ("PRE-02", "ok"), ("PRE-14", "ok"), ("PRE-15", "ok"),
    ("PRE-17", "needs_info"),      # 51-03 중분류만
    ("PRE-18", "needs_info"),      # 5103 하이픈 없음
    ("PRE-19", "needs_info"),      # 분류번호 없음
    ("PRE-20", "needs_review"),    # 51-03-XX 형식 오류
    ("PRE-21", "needs_info"),      # 99-99-99 표에 없음
    ("PRE-22", "ok"),              # 신고·선택 다름 → 경고만
])
def test_testset_input_status(cid, status):
    assert ic.check_input(CASES[cid])["status"] == status


def test_warnings():
    r = ic.check_input(CASES["PRE-22"])
    assert any("51-20-24" in w and "51-20-23" in w for w in r["warnings"]) and not r["issues"]
    bad = dict(CASES["PRE-14"], components=[{"material": "a", "share_pct": 70}, {"material": "b", "share_pct": 20}])
    assert any("90" in w for w in ic.check_input(bad)["warnings"])


def test_component_codes_checked():
    case = dict(CASES["PRE-14"])
    case["components"] = [dict(case["components"][0]), dict(case["components"][1], waste_code="51-03")]
    r = ic.check_input(case)
    assert r["status"] == "needs_info" and r["issues"][0]["field"] == "components[1].waste_code"


def test_issue_text_mentions_code():
    r = ic.check_input(CASES["PRE-20"])
    assert "51-03-XX" in r["issues"][0]["text"]


# ── 시험세트 평가 스크립트 ───────────────────────────────────

def test_testset_eval_compare_rules():
    from law_agent.eval import testset_eval as te
    assert te.compare("지정", "지정", te.RANK_A2) == "일치"
    assert te.compare("일반", "지정", te.RANK_A2) == "더 엄격"
    assert te.compare("지정", "일반", te.RANK_A2) == "덜 엄격"
    assert te.compare("의심", te.HOLD, te.RANK_A2) == "일치"
    assert te.compare("지정", te.HOLD, te.RANK_A2) == te.HOLD
    assert te.compare("검토", "금지", te.RANK_B1) == "더 엄격"
    assert te.compare("지정", "의심", te.RANK_A2) == "조건부"           # 석면 함량 조건 → 위험 아님
    assert te.compare("금지", "검토", te.RANK_B1) == "조건부"
    assert te.compare("금지", "아님", te.RANK_B1) == "덜 엄격"
    assert set(te.EXPECTED) == set(CASES)


def test_testset_eval_dry_runs(monkeypatch, capsys):
    import sys as _s
    from fake_law import build_store
    from law_agent.eval import smoke_judge, testset_eval as te
    from law_agent.llm import client
    from law_agent.tools import law_tools
    monkeypatch.setattr(client, "chat_json", smoke_judge.dry_llm)
    monkeypatch.setenv("LAW_AGENT_NO_CACHE", "1")
    law_tools.set_store(build_store())
    try:
        assert te.main(["--cases", "PRE-01,PRE-15,PRE-20", "--dry"]) == 0
        out = capsys.readouterr().out
        assert "PRE-20" in out and "입력 검사에서 멈춤" in out and "A-2: 일치" in out
        st, _ = smoke_judge.run_case(CASES["PRE-15"], None, ["A-1", "A-2", "B-1"])
        assert {q.base_id for q in st["questions"]} == {"A-1", "A-2", "B-1"}
        assert "A-2.c2" in {q.id for q in st["questions"]}                # 혼합 → 하위 질문도
    finally:
        law_tools.set_store(None)
