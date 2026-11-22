"""agents/planner.py 테스트 — 실제 checklist.yaml + 파트 1 샘플 2건."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_agent.agents import planner, schedule
from law_agent.state import AgentState, Question, initial_state, load_checklist

SAMPLE = Path(__file__).with_name("part1_sample.json")


def cases(split: bool = True) -> list[dict]:
    """split=True: 데모 2의 성분을 '분리 가능 여부 모름'으로 바꿔 하위 질문이 생기게 (원본은 '아니오' → 나누지 않음)."""
    cs = json.loads(SAMPLE.read_text(encoding="utf-8"))["cases"]
    if split:
        for c in cs[1]["components"]:
            c["separable"] = None
    return cs


@pytest.fixture
def checklist() -> list[Question]:
    return load_checklist(planner.checklist_path())


def test_single_component_case_is_checklist_only(checklist):
    out = planner.node(initial_state(cases()[0]))
    qs = out["questions"]
    assert [q.id for q in qs] == [q.id for q in checklist]                # 30개 그대로, 순서도 그대로
    assert out["audit"][0]["summary"]["sub_questions"] == 0
    assert "facts" not in out                                             # initial_state 에서 이미 채움


def test_mixture_case_adds_sub_questions_after_parent(checklist):
    case = cases()[1]                                                     # 세척용제 90% + 기계유 10%
    qs = planner.node(initial_state(case))["questions"]
    ids = [q.id for q in qs]
    assert len(qs) == 30 + 6 * 2
    for p in planner.SPLIT_PARENTS:
        i = ids.index(p)
        assert ids[i + 1: i + 3] == [f"{p}.c1", f"{p}.c2"]                # 부모 바로 뒤
    # 체크리스트 질문은 하나도 빠지지 않고 순서도 그대로
    assert [i for i in ids if "." not in i] == [q.id for q in checklist]


def test_sub_question_inherits_parent(checklist):
    qs = {q.id: q for q in planner.node(initial_state(cases()[1]))["questions"]}
    p, c2 = qs["D-1"], qs["D-1.c2"]
    assert c2.parent_id == "D-1" and c2.component == 2 and c2.base_id == "D-1"
    assert c2.when == p.when == "B-1 != yes"
    assert c2.min_laws == p.min_laws and c2.needs_facts == p.needs_facts
    assert c2.answer_type == p.answer_type and c2.stage == p.stage
    assert "혼입된 기계유 (10%)" in c2.text and c2.text.startswith(p.text)
    assert "유기계 세척용제 (90%)" in qs["A-2.c1"].text


def test_sub_questions_follow_parent_schedule():
    st = initial_state(cases()[1])
    st["questions"] = planner.node(st)["questions"]
    first = schedule.ready_ids(st)
    assert {"A-1.c1", "A-1.c2", "A-2.c1", "A-2.c2"} <= set(first)                # 분류 하위 질문 → 1차
    assert "B-1.c1" not in first and "B-1.c1" in schedule.classify(st)["pending"]  # 분류번호(A-1)를 기다림
    assert "D-1.c1" in schedule.classify(st)["pending"]


def test_planner_cannot_drop_questions_even_if_irrelevant():
    case = dict(cases()[0], designated_waste_status="non_designated")
    ids = [q.id for q in planner.node(initial_state(case))["questions"]]
    assert "H-2" in ids and "D-5" in ids                                  # when 으로만 건너뜀, planner 는 빼지 않음


def test_fills_facts_when_missing():
    st = AgentState(case=cases()[0])
    out = planner.node(st)
    assert out["facts"]["values"]["waste_code"] == "51-03-01"


def test_component_label_without_share():
    assert planner.component_label({"material": "PE"}, 1) == "PE"
    assert planner.component_label({}, 3) == "성분 3"


def test_one_component_or_bad_components_no_split(checklist):
    for comps in ([], [{"material": "PE"}], ["문자열", {"material": "PE"}]):
        qs = planner.plan({"components": comps}, checklist)
        assert len(qs) == len(checklist)


def test_checklist_path_env(monkeypatch, tmp_path, checklist):
    p = tmp_path / "c.yaml"
    p.write_text("questions:\n  - {id: A-1, stage: A, question: q, answer_type: list}\n", encoding="utf-8")
    monkeypatch.setenv("LAW_AGENT_CHECKLIST", str(p))
    assert [q.id for q in planner.node(initial_state(cases()[0]))["questions"]] == ["A-1"]


def test_split_parents_exist_in_checklist(checklist):
    ids = {q.id for q in checklist}
    assert set(planner.SPLIT_PARENTS) <= ids



def test_inseparable_mixture_is_not_split(checklist):
    """데모 2 원본: 성분 둘 다 '분리 안 됨', 성분 분류번호 없음 → 사례 분류번호 하나로 판단 (하위 질문 없음)."""
    case = cases(split=False)[1]
    assert planner.should_split(case) is False
    assert len(planner.plan(case, checklist)) == 30
    case["components"][0]["waste_code"] = "04-02-00"
    assert planner.should_split(case) is True


def test_irrelevant_questions_skipped(checklist, monkeypatch):
    """J 문항: 관련 신호가 없으면 묻지 않고 not_applicable (이유를 남김)."""
    monkeypatch.setattr(planner, "_scope_skip", lambda: set())
    demo1, demo2 = cases(split=False)
    v1 = planner.node(initial_state(demo1))["verdicts"]
    assert set(v1) == {"J-1", "J-2", "J-4"} and v1["J-4"].status == "not_applicable"
    assert "not_relevant" in v1["J-4"].flags and "관련 신호 없음" in v1["J-4"].retry_hint[0]
    v2 = planner.node(initial_state(demo2))["verdicts"]
    assert set(v2) == {"J-1", "J-2", "J-3"}                      # 액상·유해 의심 → J-4 는 묻는다
    ok, why = planner.relevant(next(q for q in checklist if q.id == "J-2"), {"classification": {"selected_code": "40-02-07"}})
    assert ok and why == "40-"



def test_scope_skip_questions(checklist, monkeypatch):
    """서비스 범위 밖 경로에만 쓰이는 질문(settings scope.skip_questions)은 묻지 않는다."""
    monkeypatch.setattr(planner, "_scope_skip", lambda: {"E-1", "F-1"})
    v = planner.node(initial_state(cases(split=False)[0]))["verdicts"]
    assert {"E-1", "F-1"} <= set(v) and "범위 밖" in v["F-1"].retry_hint[0]


def test_gate_only_env(monkeypatch):
    monkeypatch.setattr(planner, "_scope_skip", planner._scope_skip)
    from law_agent import settings as S
    monkeypatch.setattr(S, "load_settings", lambda *a, **k: {"scope": {"skip_questions": ["J-1"], "gate_only": False,
                                                                       "gate_only_skip": ["H-1"]}})
    monkeypatch.delenv("LAW_AGENT_GATE_ONLY", raising=False)
    assert planner._scope_skip() == {"J-1"}
    monkeypatch.setenv("LAW_AGENT_GATE_ONLY", "1")
    assert planner._scope_skip() == {"J-1", "H-1"}
