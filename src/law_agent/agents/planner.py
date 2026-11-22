"""① 계획 — 질문 목록 만들기.

- checklist.yaml 질문을 모두 넣는다 (지울 수 없음). when 은 평가하지 않고 그대로 둔다 (schedule·verifier 가 앞 답으로 판단).
- 혼합 사례(components 2개 이상)면 성분별 하위 질문을 부모 바로 뒤에 더한다: SPLIT_PARENTS (A-1, A-2, B-1, B-2, D-1, F-1).
  A-2: 어느 성분 때문에 지정폐기물인지 근거에 남김. B-2: D-1(성분별 R코드)을 좁힐 허용 목록도 성분별로 (2026.11.17 결정).
  하위 질문 id = "D-1.c2" (state.sub_question_id), 부모의 when·최소 조문·필요 사실을 그대로 가진다.
- LLM 으로 질문을 더하는 기능(settings planner_llm)은 아직 없다 — 켜져 있으면 감사 기록에 남기고 넘어간다.

체크리스트 경로: LAW_AGENT_CHECKLIST 환경변수, 없으면 config/checklist.yaml.
"""
from __future__ import annotations

import os
from pathlib import Path

from law_agent.settings import load_settings
from law_agent.state import (FLAG_NOT_RELEVANT, AgentState, Question, Verdict, extract_facts, load_checklist,
                             sub_question_id)

NODE = "planner"
SPLIT_PARENTS = ("A-1", "A-2", "B-1", "B-2", "D-1", "F-1")
DEFAULT_CHECKLIST = Path(__file__).resolve().parents[1] / "config" / "checklist.yaml"


def checklist_path() -> Path:
    return Path(os.environ.get("LAW_AGENT_CHECKLIST") or DEFAULT_CHECKLIST)


def component_label(comp: dict, i: int) -> str:
    name = comp.get("material") or f"성분 {i}"
    share = comp.get("share_pct")
    return f"{name} ({share}%)" if share is not None else str(name)


def sub_questions(parent: Question, components: list[dict]) -> list[Question]:
    out = []
    for i, comp in enumerate(components, start=1):
        out.append(Question(
            id=sub_question_id(parent.id, i), stage=parent.stage,
            text=f"{parent.text} — 혼합물의 성분 {i}: {component_label(comp, i)} 에 대해",
            answer_type=parent.answer_type, min_laws=list(parent.min_laws), needs_facts=list(parent.needs_facts),
            when=parent.when, feeds=list(parent.feeds), parent_id=parent.id, component=i,
            search=parent.search, baseline=parent.baseline, attach=parent.attach, generic=parent.generic,
        ))
    return out


def should_split(case: dict) -> bool:
    """성분별 하위 질문을 만들지. 성분이 2개 이상이고, 모두 '분리 안 됨'이면서 성분 분류번호도 없으면 만들지 않는다
    (나눌 수 없는 혼합물은 사례 분류번호 하나로 판단 — 2026.11.17 데모 2: 하위 질문 12개가 비용의 큰 몫, 판단은 needs_human).
    성분 분류번호가 하나라도 있거나, 분리 가능·모름인 성분이 있으면 만든다."""
    comps = [c for c in case.get("components") or [] if isinstance(c, dict)]
    if len(comps) < 2:
        return False
    if any(c.get("waste_code") for c in comps):
        return True
    return not all(str(c.get("separable") or "").strip() in ("아니오", "no", "false", "False") for c in comps)


def _text(case: dict) -> str:
    parts = [case.get("name"), case.get("process_description"), case.get("material_form")]
    parts += [c.get("material") for c in case.get("components") or [] if isinstance(c, dict)]
    parts += list((case.get("contamination") or {}).get("substances") or [])
    return " ".join(str(p) for p in parts if p)


def relevant(q: Question, case: dict) -> tuple[bool, str]:
    """relevant_if 신호 중 하나라도 있으면 묻는다. 없으면 (False, 이유). relevant_if 가 비어 있으면 항상 묻는다.
    신호: keywords(이름·공정·성상·성분·오염물질에 든 말), code_prefix(분류번호 앞자리),
          hazardous(유해 의심), designated(지정·지정 의심), contaminated(오염 물질 있음), forms(성상)."""
    r = q.relevant_if or {}
    if not r:
        return True, ""
    text = _text(case)
    code = str((case.get("classification") or {}).get("selected_code") or "")
    hits = [k for k in r.get("keywords") or [] if k.lower() in text.lower()]
    hits += [p for p in r.get("code_prefix") or [] if code.startswith(p)]
    if r.get("hazardous") and case.get("hazardous_suspect") is True:
        hits.append("유해 의심")
    if r.get("designated") and str(case.get("designated_waste_status") or "") in ("designated", "suspect", "designated_suspect"):
        hits.append("지정폐기물")
    if r.get("contaminated") and (case.get("contamination") or {}).get("substances"):
        hits.append("오염 물질")
    if case.get("material_form") in (r.get("forms") or []):
        hits.append(f"성상 {case.get('material_form')}")
    if hits:
        return True, ", ".join(hits)
    return False, "사례에 관련 신호 없음 (" + ", ".join(
        f"{k}: {v if not isinstance(v, list) else '/'.join(map(str, v[:6]))}" for k, v in r.items()) + ")"


def plan(case: dict, checklist: list[Question]) -> list[Question]:
    """체크리스트 + 성분별 하위 질문. 체크리스트 순서를 지키고 하위 질문은 부모 바로 뒤."""
    comps = [c for c in case.get("components") or [] if isinstance(c, dict)]
    split = should_split(case)
    out: list[Question] = []
    for q in checklist:
        out.append(q)
        if split and q.id in SPLIT_PARENTS:
            out.extend(sub_questions(q, comps))
    return out


def _scope_skip() -> set[str]:
    try:
        from law_agent.settings import load_settings
        sc = load_settings().get("scope") or {}
        skip = set(sc.get("skip_questions") or [])
        import os
        if sc.get("gate_only") or os.environ.get("LAW_AGENT_GATE_ONLY") == "1":                        # 관문 판단만: 서류·신고·허가 안내 질문도 묻지 않음 (토큰 최소)
            skip |= set(sc.get("gate_only_skip") or [])
        return skip
    except Exception:                                  # noqa: BLE001
        return set()


def skipped_verdicts(case: dict, questions: list[Question]) -> dict[str, Verdict]:
    """관련 신호가 없는 질문은 묻지 않고 not_applicable 로 끝낸다 (이유는 retry_hint 칸에 남김 → law_review·감사).
    서비스 범위(settings scope.skip_questions) 밖 경로에만 쓰이는 질문도 묻지 않는다."""
    out = {}
    scope_skip = _scope_skip()
    for q in questions:
        if q.base_id in scope_skip:
            out[q.id] = Verdict(question_id=q.id, status="not_applicable", answer="unknown",
                                flags=[FLAG_NOT_RELEVANT], retry_hint=["서비스 범위 밖 (중간재활용업만) — 묻지 않음"])
            continue
        ok, why = relevant(q, case)
        if not ok:
            out[q.id] = Verdict(question_id=q.id, status="not_applicable", answer="unknown",
                                flags=[FLAG_NOT_RELEVANT], retry_hint=[why])
    return out


def node(state: AgentState, checklist: list[Question] | None = None) -> dict:
    """planner 노드. 바뀐 칸: questions, facts(없을 때만), audit."""
    case = state.get("case") or {}
    base = checklist if checklist is not None else load_checklist(checklist_path())
    questions = plan(case, base)
    out: dict = {"questions": questions}
    skipped = skipped_verdicts(case, questions)
    if skipped:
        out["verdicts"] = skipped
    if not state.get("facts"):
        out["facts"] = extract_facts(case)
    notes = []
    if load_settings().get("planner_llm"):
        notes.append("planner_llm 이 켜져 있지만 아직 구현되지 않아 체크리스트만 사용")
    out["audit"] = [{"node": NODE, "summary": {
        "checklist": len(base), "sub_questions": len(questions) - len(base),
        "components": len(case.get("components") or []), "split": should_split(case),
        "not_relevant": sorted(skipped), "notes": notes},
        "output": {"question_ids": [q.id for q in questions],
                   "not_relevant": {k: v.retry_hint for k, v in skipped.items()}}}]
    return out
