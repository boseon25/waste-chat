"""agents/context.py 테스트 — 판단 누적 (확정 사실, 숨은 순서, 앞 판단)."""
from __future__ import annotations

import json
from pathlib import Path

from law_agent.agents import context as cx
from law_agent.agents import planner, schedule
from law_agent.state import Question, Verdict, initial_state, load_checklist

CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]
for _c in CASES[1]["components"]:          # 성분별 하위 질문을 시험하려고 '분리 가능 여부 모름'으로 (원본은 '아니오' → 나누지 않음)
    _c["separable"] = None
CHECKLIST = load_checklist(Path(__file__).resolve().parents[1] / "config" / "checklist.yaml")


def V(qid, answer="yes", status="agreed", items=None, **kw):
    return Verdict(qid, status, answer, {"items": items} if items is not None else {}, **kw)


def state(case=0, verdicts=None):
    st = initial_state(CASES[case])
    st["questions"] = planner.plan(CASES[case], CHECKLIST)
    st["verdicts"] = verdicts or {}
    return st


# ── 확정 사실 ─────────────────────────────────────────────────

def test_effective_facts_from_agreed_verdicts():
    st = state(0, {"A-1": V("A-1", "yes", items=["51-03-02"]), "A-2": V("A-2", "no"),
                   "D-1": V("D-1", "yes", items=["R-3-2"]), "F-1": V("F-1", "yes", items=["1101"])})
    eff = cx.effective_facts(st)
    v = eff["values"]
    assert v["waste_code"] == "51-03-02"                        # A-1 이 번호를 고침
    assert v["waste_status"] == "non_designated"
    assert v["r_codes"] == ["R-3-2"] and v["treatment_codes"] == ["1101"]
    assert eff["confirmed"] == {"waste_code": "A-1", "waste_status": "A-2", "r_codes": "D-1", "treatment_codes": "F-1"}
    assert st["facts"]["values"]["waste_code"] == "51-03-01"    # 원래 사실은 그대로


def test_unconfirmed_keeps_part1_value_and_marks():
    st = state(0, {"A-1": V("A-1", "unknown", "needs_human"), "A-2": V("A-2", "yes", "retry")})
    eff = cx.effective_facts(st)
    assert eff["values"]["waste_code"] == "51-03-01" and eff["unconfirmed"] == {"waste_code": "A-1"}
    assert "waste_status" not in eff["confirmed"] and "waste_status" not in eff["unconfirmed"]   # retry 는 아직 안 끝남


def test_a1_yes_without_items_confirms_part1_code():
    eff = cx.effective_facts(state(0, {"A-1": V("A-1", "yes")}))
    assert eff["values"]["waste_code"] == "51-03-01" and eff["confirmed"]["waste_code"] == "A-1"


def test_a2_conditional_is_suspect():
    eff = cx.effective_facts(state(0, {"A-2": V("A-2", "conditional")}))
    assert eff["values"]["waste_status"] == "designated_suspect"


# ── 숨은 순서 ─────────────────────────────────────────────────

def test_fact_dependencies():
    qs = {q.id: q for q in CHECKLIST}
    assert cx.fact_dependencies(qs["A-2"]) == []                        # 분류 단계는 기다리지 않음
    assert cx.fact_dependencies(qs["B-1"]) == ["A-1"]
    assert cx.fact_dependencies(qs["F-1"]) == ["A-1", "A-2"]
    assert cx.fact_dependencies(qs["I-1"]) == ["A-2", "D-1", "F-1"]
    assert cx.all_dependencies(qs["E-1"]) == ["D-1", "A-2"]


def test_full_schedule_waves_single_case():
    """모든 판단을 '확정 yes + 대표 항목' 으로 두고 차례를 끝까지 돌려 본다."""
    st = state(0)
    waves = []
    for _ in range(10):
        ready = schedule.ready_ids(st)
        if not ready:
            break
        waves.append(ready)
        for qid in ready:
            items = {"D-1": ["R-3-2", "R-9-1"], "F-1": ["1101"], "I-1": ["report"]}.get(qid)
            answer = "no" if qid == "B-1" else "yes"            # B-1 yes(금지) 면 D-1 을 안 물으므로 no
            st["verdicts"][qid] = V(qid, answer, items=items)
        cls = schedule.classify(st)
        for qid in cls["skip"]:
            st["verdicts"][qid] = V(qid, "unknown", "not_applicable")
    assert waves[0] == ["A-1", "A-2", "A-4", "J-4"]
    assert "B-1" in waves[1] and "F-1" in waves[1] and "H-1" in waves[1] and "D-1" not in waves[1]
    assert "D-1" in waves[2] and "I-1" not in waves[2]
    assert "I-1" in waves[3] and "E-1" in waves[3]
    assert len(waves) <= 5
    done = {qid for w in waves for qid in w}
    assert done | {qid for qid, v in st["verdicts"].items() if v.status == "not_applicable"} == {q.id for q in CHECKLIST}


def test_sub_questions_wait_like_parent():
    st = state(1)
    assert "B-1.c2" in schedule.classify(st)["pending"]
    st["verdicts"]["A-1"] = V("A-1", "yes")
    st["verdicts"]["A-2"] = V("A-2", "yes")
    assert "B-1.c2" in schedule.ready_ids(st)


# ── judge 에 넘길 사실·앞 판단 ────────────────────────────────

def test_question_facts_with_sources():
    st = state(0, {"A-1": V("A-1", "yes"), "A-2": V("A-2", "unknown", "needs_human")})
    q = next(x for x in st["questions"] if x.id == "F-1")
    f = cx.question_facts(q, st)
    assert f["waste_code"] == {"값": "51-03-01", "출처": "A-1 판단"}
    assert f["waste_status"] == {"값": "non_designated", "출처": "파트 1 (A-2 판단 확정 안 됨)"}
    assert f["material_form"]["출처"] == "파트 1"
    i1 = next(x for x in st["questions"] if x.id == "I-1")
    assert "r_codes" not in cx.question_facts(i1, st) and "gate" not in cx.question_facts(i1, st)
    st["verdicts"]["D-1"] = V("D-1", "yes", items=["R-3-2"])
    assert cx.question_facts(i1, st)["r_codes"] == {"값": ["R-3-2"], "출처": "D-1 판단"}


def test_question_facts_missing_and_component():
    st = state(1)
    q = next(x for x in st["questions"] if x.id == "J-2")
    assert cx.question_facts(q, st)["origin"]["출처"].startswith("제공되지 않음")   # 파트 1 v1.0 에 칸이 없음
    sub = next(x for x in st["questions"] if x.id == "D-1.c2")
    assert cx.question_facts(sub, st)["이 질문의 성분"]["값"]["material"] == "혼입된 기계유"


def test_prior_findings_only_dependencies_and_component_first():
    vs = {"A-1": V("A-1", "yes"), "A-2": V("A-2", "yes"), "B-1": V("B-1", "no"),
          "B-1.c2": V("B-1.c2", "conditional", conditions=[{"text": "PCB 2ppm 미만일 때", "quote": "q", "ref": "r"}]),
          "C-1": V("C-1", "no")}
    st = state(1, vs)
    sub = next(x for x in st["questions"] if x.id == "D-1.c2")
    pf = cx.prior_findings(sub, st)
    assert list(pf) == ["B-1.c2", "A-1"]                        # when(B-1, 성분 2 먼저) + 사실(A-1). C-1 은 안 넣음
    assert pf["B-1.c2"]["상태"] == "확정" and pf["B-1.c2"]["조건"] == ["PCB 2ppm 미만일 때"]
    e1 = next(x for x in st["questions"] if x.id == "H-2")
    assert list(cx.prior_findings(e1, st)) == ["A-2", "A-1"]


def test_prior_findings_marks_unconfirmed():
    st = state(0, {"A-1": V("A-1", "unknown", "needs_info", missing_facts=["components"])})
    q = next(x for x in st["questions"] if x.id == "B-1")
    pf = cx.prior_findings(q, st)
    assert pf["A-1"]["상태"].startswith("확인 안 됨") and pf["A-1"]["빠진 사실"] == ["components"]


def test_fact_source_not_provided_vs_explicit_missing():
    st = state(0)
    b1 = next(x for x in st["questions"] if x.id == "B-1")
    f = cx.question_facts(b1, st)
    assert f["analysis.pcb"]["출처"].startswith("제공되지 않음")        # 입력 항목에 없음 → 다른 사실로 판단 가능
    case = dict(CASES[0], missing=["analysis.pcb"])
    st2 = initial_state(case)
    st2["questions"] = planner.plan(case, CHECKLIST)
    assert cx.question_facts(b1, st2)["analysis.pcb"]["출처"].startswith("없음 (파트 1이 확인하지 못함)")
