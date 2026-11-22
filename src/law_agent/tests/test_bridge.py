"""공유 폴더 연동: 파트 1 2.0 파일 → inbox → outbox (가짜 저장소 + dry LLM, 업체 없이)."""
import copy
import json
from pathlib import Path

import pytest

from law_agent.bridge import part1_requests, watch
from law_agent.llm import client

V2 = json.loads((Path(__file__).parent / "fixtures" / "part1_v2_review.json").read_text(encoding="utf-8"))
SCHEMA_REQUIRED = ["request_id", "case_id", "based_on_case_version", "requested_by", "fields", "trigger_rule_id",
                   "reason", "question", "answer_type", "blocking_scope"]


@pytest.fixture
def dry(monkeypatch):
    from fake_law import build_store
    from law_agent.eval import smoke_judge
    from law_agent.tools import law_tools
    monkeypatch.setattr(client, "chat_json", smoke_judge.dry_llm)
    monkeypatch.setenv("LAW_AGENT_NO_CACHE", "1")
    law_tools.set_store(build_store())
    yield
    law_tools.set_store(None)


def filled_v2():
    d = copy.deepcopy(V2)
    f = lambda v: {"value": v, "raw_value": str(v), "status": "confirmed"}   # noqa: E731
    d["case"]["classification"]["reported_code"] = f("51-03-01")
    d["case"]["generation"] = {"amount": f("1.2"), "unit": f("t"), "period": f("month")}
    d["case"]["hazard_declaration"] = f("아니오")
    return d


def test_watch_once_end_to_end(dry, tmp_path):
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir(); outbox.mkdir()
    (inbox / "case_a.json").write_text(json.dumps(filled_v2(), ensure_ascii=False), encoding="utf-8")
    (inbox / "broken.json").write_text("{not json", encoding="utf-8")
    n = watch.scan(inbox, outbox, None, None, {}, once=True)
    assert n == 2
    assert (inbox / "done" / "case_a.json").exists() and (inbox / "failed" / "broken.json").exists()
    assert (outbox / "broken_error.txt").exists()
    req = next(outbox.glob("*_requests.json"))
    body = json.loads(req.read_text(encoding="utf-8"))
    assert body["case_id"] == V2["case"]["case_id"]          # 파트 1 원래 사례 번호로 돌려줌
    for r in body["requests"]:
        assert all(k in r for k in SCHEMA_REQUIRED) and r["requested_by"] == "part2" and r["fields"]
    assert next(outbox.glob("*_proposals.json")) and next(outbox.glob("*_result.json"))


def test_unfilled_demo_asks_for_code(dry, tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir(); outbox.mkdir()
    (inbox / "demo.json").write_text(json.dumps(V2, ensure_ascii=False), encoding="utf-8")
    watch.scan(inbox, outbox, None, None, {}, once=True)
    res = json.loads(next(outbox.glob("*_result.json")).read_text(encoding="utf-8"))
    assert res["input_check"]["status"] != "ok"


def test_request_mapping():
    res = {"case_id": "C", "case_version": 2, "information_request": {"requests": [
        {"field": "B-1.PCB 농도", "text": "PCB 농도", "question_ids": ["B-1"], "affected_routes": ["C-G2-x"]},
        {"field": "A-1.분류번호", "text": "분류번호", "question_ids": ["A-1"], "affected_routes": []}]}}
    a, b = part1_requests.to_part1_requests(res)
    assert a["fields"] == ["analysis_results"] and a["answer_type"] == "number" and a["blocking_scope"] == "route:C-G2-x"
    assert "시험성적서" in a["acceptable_evidence"]
    assert b["fields"] == ["classification.reported_code"] and b["blocking_scope"] == "classification"
    assert part1_requests.to_part1_requests(res)[0]["request_id"] == a["request_id"]     # 같은 질문 = 같은 번호


def test_claim_prevents_double_processing(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{}", encoding="utf-8")
    w = watch._claim(p)
    assert w is not None and w.name == "a.json.working" and not p.exists()
    assert watch._claim(p) is None                      # 두 번째 프로세스는 못 집음
    watch._move(tmp_path / "없음.json", tmp_path / "done", "x.json")   # 사라진 파일 옮기기 → 오류 없이 넘어감
