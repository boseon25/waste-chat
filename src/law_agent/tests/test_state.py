"""state.py 테스트 — 네트워크·서버 데이터 없이 돈다."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from law_agent.state import (
    ALLOWED_ANSWERS, AgentState, Candidate, EvidenceItem, EvidencePack, JudgeAnswer, Question, Verdict,
    WhenSyntaxError, cache_key, check_question, consensus_key, core_value, error_record, eval_when,
    extract_facts, facts_for, load_checklist as _load_checklist, MinLaw, facts_hash, initial_state, make_pack, make_ref, merge_dict, norm_text,
    parse_ref, parse_when, quote_in, ref_base, safe_name, state_from_json, state_to_json,
    sub_question_id, validate_value, when_dependencies,
)

PKG = Path(__file__).resolve().parents[1]
CHECKLIST = PKG / "config" / "checklist.yaml"
SAMPLE = Path(__file__).with_name("part1_sample.json")


def load_checklist() -> list[Question]:
    return _load_checklist(CHECKLIST)


def sample_cases() -> list[dict]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]


def item(law="WMA_RULE", key="B4의3", text="51-03-01 | 폐합성수지류 | R-3-2, R-3-3", eff="20261112", h="abc123", **kw):
    return EvidenceItem(ref=make_ref(law, key, eff, h), law=law, key=key, title="t", effective=eff, text=text, **kw)


def verdict(qid, answer="yes", status="agreed", items=None):
    return Verdict(question_id=qid, status=status, answer=answer, value={"items": items} if items is not None else {})


# ── 체크리스트 ────────────────────────────────────────────────

def test_checklist_loads_all_questions():
    qs = load_checklist()
    assert len(qs) == 30
    assert len({q.id for q in qs}) == 30
    for q in qs:
        assert q.min_laws, f"{q.id} 최소 조문 없음"


def test_checklist_min_laws_numbers_survive_yaml():
    """YAML 이 no 키를 False 로 읽어도 조문 번호가 남아야 한다."""
    qs = {q.id: q for q in load_checklist()}
    assert qs["A-1"].min_laws[0].no == "B4"
    assert [m.no for m in qs["B-1"].min_laws] == ["13의2", "14의3", "B1"]
    assert qs["G-2"].min_laws[2].no == ""                  # 고시는 번호 없음
    assert qs["A-3"].min_laws[1].no == "Ⅵ"
    assert MinLaw.from_dict({"law": "WMA", "article": "18", "title": "x"}).no == "18"
    assert MinLaw.from_dict({"law": "WMA", "no": 25, "title": "x"}).no == "25"


def test_load_checklist_rejects_duplicates_and_dangling_when(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("questions:\n"
                 "  - {id: A-1, stage: A, question: q, answer_type: list}\n"
                 "  - {id: A-1, stage: A, question: q, answer_type: list}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="중복"):
        _load_checklist(p)
    p.write_text("questions:\n"
                 "  - {id: A-1, stage: A, question: q, answer_type: list, when: 'Z-9 has items'}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Z-9"):
        _load_checklist(p)


def test_checklist_when_dependencies_exist_and_come_earlier():
    qs = load_checklist()
    order = {q.id: i for i, q in enumerate(qs)}
    for q in qs:
        for dep in when_dependencies(q.when):
            assert dep in order, f"{q.id} 가 없는 질문 {dep} 를 기다림"
            assert order[dep] < order[q.id], f"{q.id} → {dep}"


def test_checklist_needs_facts_are_known_names():
    from law_agent.state import COMPUTED_FACTS, DERIVED_FACTS, FACT_PATHS
    for q in load_checklist():
        unknown = [f for f in q.needs_facts if f not in FACT_PATHS and f not in DERIVED_FACTS and f not in COMPUTED_FACTS]
        assert not unknown, f"{q.id}: FACT_PATHS 에 없는 사실 {unknown}"


def test_checklist_entry_errors():
    with pytest.raises(ValueError):
        Question.from_checklist({"id": "A-1", "stage": "A", "question": "?", "answer_type": "boolean"})
    with pytest.raises(ValueError):
        Question.from_checklist({"id": "A-9", "stage": "A", "question": "?", "answer_type": "list", "when": "A-1 maybe"})


def test_sub_question():
    qid = sub_question_id("D-1", 2)
    assert qid == "D-1.c2"
    q = Question(id=qid, stage="D", text="성분 2", answer_type="list", parent_id="D-1", component=2)
    assert q.base_id == "D-1" and not check_question(q)
    bad = Question(id="D-1.c2", stage="D", text="x", answer_type="list", parent_id="F-1")
    assert check_question(bad)


# ── value 형식 ───────────────────────────────────────────────

@pytest.mark.parametrize("atype,answer,value", [
    ("yes_no_cond", "yes", {}),
    ("yes_no_cond", "conditional", {"items": ["51-03-01"]}),
    ("yes_no", "unknown", {}),
    ("list", "yes", {"items": ["R-3-2", "R-3-3"], "attrs": {"R-3-2": {"pre_analysis": False}}}),
    ("list", "no", {"items": []}),
    ("list", "unknown", {}),
    ("compare", "yes", {"criterion": "월 평균 1톤 이상", "case_value": 1.21, "unit": "ton/월", "result": "met"}),
    ("compare", "unknown", {"result": "unknown"}),
    ("text", "yes", {"text": "수탁처리능력 확인서를 받는다"}),
    ("text", "unknown", {}),
])
def test_validate_value_ok(atype, answer, value):
    assert validate_value(atype, value, answer) == []


@pytest.mark.parametrize("atype,answer,value", [
    ("yes_no", "conditional", {}),                         # yes_no 에 conditional 없음
    ("yes_no_cond", "yes", {"r_codes": ["R-3-2"]}),         # 없는 칸
    ("list", "yes", {"items": []}),                         # yes 인데 비어 있음
    ("list", "no", {"items": ["R-1"]}),                     # no 인데 있음
    ("list", "yes", {"items": ["R-1", "R-1"]}),             # 중복
    ("list", "yes", {"items": ["R-1"], "attrs": {"R-2": {}}}),   # attrs 키가 items 에 없음
    ("list", "yes", {"items": ["R-1"], "attrs": {"R-1": {"x": [1]}}}),  # 단순 값 아님
    ("compare", "yes", {"criterion": "1톤", "result": "unknown"}),
    ("compare", "no", {"result": "not_met"}),                # criterion 없음
    ("compare", "yes", {"criterion": "1톤", "result": "maybe"}),
    ("text", "yes", {"text": "  "}),
    ("bogus", "yes", {}),
])
def test_validate_value_bad(atype, answer, value):
    assert validate_value(atype, value, answer)


def test_validate_value_not_dict():
    assert validate_value("list", ["R-1"], "yes")


def test_allowed_answers_cover_types():
    from law_agent.state import ANSWER_TYPES
    assert set(ALLOWED_ANSWERS) == set(ANSWER_TYPES)


def test_consensus_key_list_is_set_compare():
    a = consensus_key("list", "yes", {"items": ["R-3-2", "R-3-3"]})
    b = consensus_key("list", "yes", {"items": ["R-3-3", "R-3-2"]})
    assert a == b
    c = consensus_key("list", "yes", {"items": ["R-3-2", "R-3-3"], "attrs": {"R-3-2": {"pre_analysis": True}}})
    assert a != c                                          # 사전 분석 여부가 다르면 다른 답
    d = consensus_key("list", "yes", {"items": ["R-3-2", "R-3-3"], "attrs": {"R-3-2": {"note": "설명"}}})
    assert a == d                                          # 문자열 설명은 비교에서 뺌
    assert core_value("text", {"text": "가"}) is None
    assert consensus_key("text", "yes", {"text": "가"}) == consensus_key("text", "yes", {"text": "나"})
    assert core_value("compare", {"result": "met"}) == "met"
    assert core_value("yes_no_cond", {}) is None


# ── 근거 묶음·해시 ───────────────────────────────────────────

def test_pack_hash_is_order_independent_and_dedups():
    a, b = item(key="B4의3"), item(law="WMA", key="13의2", text="법 원문")
    p1 = make_pack("D-1", [a, b])
    p2 = make_pack("D-1", [b, a, a])
    assert p1.pack_hash == p2.pack_hash and len(p2.items) == 2
    assert [i.law for i in p1.items] == ["WMA", "WMA_RULE"]
    assert make_pack("D-1", [a, b], truncated=True).pack_hash != p1.pack_hash
    changed = item(key="B4의3", text="51-03-01 | 바뀐 원문")
    assert make_pack("D-1", [changed, b]).pack_hash != p1.pack_hash      # 원문이 바뀌면 캐시 무효
    via_search = item(key="B4의3", source="min_laws")
    assert make_pack("D-1", [via_search, b]).pack_hash == p1.pack_hash   # 찾은 방법은 해시에 안 들어감


def test_pack_get_with_and_without_hash():
    p = make_pack("D-1", [item()])
    full = p.items[0].ref
    assert p.get(full) is p.items[0]
    assert p.get(ref_base(full)) is p.items[0]
    assert p.get("WMA_RULE:B4의2@20261112") is None
    assert p.get("아무거나") is None


def test_facts_hash_by_names():
    f1 = {"values": {"waste_code": "51-03-01", "quantity": 1}}
    f2 = {"values": {"waste_code": "51-03-01", "quantity": 2}}
    assert facts_hash(f1, ["waste_code"]) == facts_hash(f2, ["waste_code"])
    assert facts_hash(f1) != facts_hash(f2)


def test_cache_key_stable_and_distinct():
    k = cache_key("D-1", "p", "f", "m", "judge_v1")
    assert k == cache_key("D-1", "p", "f", "m", "judge_v1")
    assert k != cache_key("D-1", "p", "f", "m", "judge_v2")
    assert len(k) == 64


def test_safe_name():
    assert safe_name("anthropic/claude-sonnet-5.5") == "anthropic__claude-sonnet-5.5"
    assert "/" not in safe_name("a/b:c")


# ── ref·인용 ─────────────────────────────────────────────────

def test_ref_roundtrip():
    r = make_ref("WMA_RULE", "16의13", "20261112", "06131c0d8446eddb")
    assert r == "WMA_RULE:16의13@20261112#06131c0d8446eddb"
    p = parse_ref(r)
    assert p == {"law": "WMA_RULE", "key": "16의13", "effective": "20261112", "hash": "06131c0d8446eddb"}
    assert parse_ref("CEA_DESIG:B1@20260101")["hash"] is None
    assert ref_base(r) == "WMA_RULE:16의13@20261112"
    assert parse_ref("WMA_RULE 16의13") is None


def test_quote_normalization():
    text = "제16조의13(위탁ㆍ수탁의 기준 및 절차)\n① 사업장폐기물배출자는  수탁자의 처리능력을 확인하여야 한다."
    assert quote_in("위탁·수탁의 기준 및 절차", text)            # 가운뎃점 종류가 달라도
    assert quote_in("사업장폐기물배출자는 수탁자의\n처리능력을", text)  # 공백·줄바꿈이 달라도
    assert not quote_in("수탁자의 처리능력을 확인하지 않아도 된다", text)
    assert not quote_in("   ", text)
    assert norm_text("가 · 나ㆍ다") == "가나다"


# ── when ─────────────────────────────────────────────────────

def test_parse_when_forms():
    assert parse_when(None) == [] and parse_when("  ") == []
    assert parse_when("A-2 != yes") == [{"qid": "A-2", "kind": "ne", "arg": "yes"}]
    assert parse_when("C-1 in [yes, conditional]")[0]["arg"] == ["yes", "conditional"]
    assert parse_when("D-1 has items")[0]["kind"] == "has_items"
    assert parse_when("D-1 has R-9")[0] == {"qid": "D-1", "kind": "has", "arg": "R-9"}
    assert parse_when("I-1 includes report")[0]["arg"] == "report"
    t = parse_when("A-2 == yes and D-1 has items")
    assert [x["qid"] for x in t] == ["A-2", "D-1"]
    for bad in ["A-2 = yes", "A-2 == maybe", "A-2 in []", "a-2 == yes", "A-2 != yes or B-1 == no"]:
        with pytest.raises(WhenSyntaxError):
            parse_when(bad)


def test_eval_when_basic():
    vs = {"A-2": verdict("A-2", "no"), "D-1": verdict("D-1", "yes", items=["R-3-2", "R-9-1"])}
    assert eval_when(None, vs) is True
    assert eval_when("A-2 != yes", vs) is True
    assert eval_when("A-2 == yes", vs) is False
    assert eval_when("D-1 has items", vs) is True
    assert eval_when("D-1 has R-9", vs) is True
    assert eval_when("D-1 has R-1", vs) is False
    assert eval_when("A-2 == yes and D-1 has items", vs) is False


def test_eval_when_pending_and_unknown_is_safe():
    assert eval_when("A-2 != yes", {}) is None                                   # 아직 안 끝남
    assert eval_when("A-2 != yes", {"A-2": verdict("A-2", status="retry")}) is None
    # 모르면 묻는다: needs_human·needs_info·unknown 이면 True
    for v in [verdict("A-2", "unknown", "needs_human"), verdict("A-2", "unknown", "needs_info"),
              verdict("A-2", "unknown", "agreed")]:
        assert eval_when("A-2 == yes", {"A-2": v}) is True
        assert eval_when("A-2 != yes", {"A-2": v}) is True
    # 하나라도 거짓이 확정되면 나머지가 대기여도 False
    assert eval_when("A-2 == yes and D-1 has items", {"A-2": verdict("A-2", "no")}) is False
    # 앞 질문이 not_applicable 이면 no / 빈 목록
    na = {"D-1": verdict("D-1", "unknown", "not_applicable")}
    assert eval_when("D-1 has items", na) is False
    assert eval_when("B-1 != yes", {"B-1": verdict("B-1", "unknown", "not_applicable")}) is True


# ── 사례 사실 ────────────────────────────────────────────────

def test_extract_facts_sample_case_1():
    c = sample_cases()[0]
    f = extract_facts(c)
    v = f["values"]
    assert v["waste_code"] == "51-03-01"
    assert v["waste_name"] == "제품 포장용 PE 필름 자투리"
    assert v["waste_status"] == "non_designated"
    assert v["heating_value"] == 10200
    assert v["quantity.monthly_ton"] == [1.15, 1.27, 1.21]
    assert "separable" in f["not_applicable"]                 # case.not_applicable 에 components[].separable
    assert "origin" in f["missing"] and "business_type" in f["missing"]
    assert "waste_code" not in f["missing"]


def test_extract_facts_mixture_case_2():
    c = sample_cases()[1]
    f = extract_facts(c)
    assert len(f["values"]["components"]) == 2
    assert f["values"]["separable"] == ["아니오", "아니오"]


def test_extract_facts_missing_paths_override():
    c = dict(sample_cases()[0])
    c["missing"] = ["storage_state"]
    f = extract_facts(c)
    assert "storage_state" in f["missing"] and f["values"]["storage_state"] is None


def test_facts_for_skips_derived():
    f = extract_facts(sample_cases()[0])
    got = facts_for(f, ["waste_code", "gate", "r_codes"])
    assert got == {"waste_code": "51-03-01"}


# ── 상태·직렬화 ──────────────────────────────────────────────

def test_merge_dict_reducer():
    assert merge_dict({"A-1": 1, "B-1": 2}, {"B-1": 3}) == {"A-1": 1, "B-1": 3}
    assert merge_dict(None, {"A-1": 1}) == {"A-1": 1}


def test_agent_state_reducers_declared():
    from typing import get_type_hints
    hints = get_type_hints(AgentState, include_extras=True)
    for k in ("candidates", "packs", "answers", "verdicts", "retry_count"):
        assert hints[k].__metadata__[0] is merge_dict, k
    import operator
    for k in ("audit", "errors"):
        assert hints[k].__metadata__[0] is operator.add, k


def test_state_json_roundtrip():
    c = sample_cases()[0]
    st = initial_state(c)
    st["questions"] = load_checklist()[:3]
    st["candidates"] = {"A-1": [Candidate("WMA_RULE", "B4", "폐기물의 분류번호", 1.0, "min_laws")]}
    pack = make_pack("A-1", [item(key="B4", text="51-03-01 폐합성수지")])
    st["packs"] = {"A-1": pack}
    ja = JudgeAnswer("A-1", "m1", "yes", {"items": ["51-03-01"]},
                     quotes=[{"ref": pack.items[0].ref, "text": "51-03-01 폐합성수지"}])
    st["answers"] = {"A-1": [ja]}
    st["verdicts"] = {"A-1": Verdict("A-1", "agreed", "yes", {"items": ["51-03-01"]},
                                     votes=[{"model": "m1", "answer": "yes", "valid": True, "drop_reason": ""}],
                                     pack_hash=pack.pack_hash)}
    st["retry_count"] = {"A-1": 0}
    st["errors"] = [error_record("judge", "타임아웃", "A-1", "m2")]

    back = state_from_json(state_to_json(st))
    assert back["questions"] == st["questions"]
    assert back["candidates"] == st["candidates"]
    assert back["packs"] == st["packs"]
    assert back["answers"] == st["answers"]
    assert back["verdicts"] == st["verdicts"]
    assert back["facts"] == st["facts"] and back["case"] == c
    assert back["errors"] == st["errors"]
    # 다시 직렬화해도 같다
    assert state_to_json(back) == state_to_json(st)


def test_state_json_routes_use_to_dict():
    class FakeRoute:
        def to_dict(self):
            return {"gate": 2, "regulatory": "confirmed"}
    st = AgentState(routes=[FakeRoute()])
    assert json.loads(state_to_json(st))["routes"] == [{"gate": 2, "regulatory": "confirmed"}]
