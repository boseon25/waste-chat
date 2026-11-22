"""판단 누적 — 앞에서 확정한 판단을 뒤 질문의 사실·근거·프롬프트로 넘긴다. 판단하지 않는다 (옮기기만).

1. 확정 사실 (effective_facts)
   파트 1이 보낸 값 대신, 판단으로 확정된 값을 쓴다. 합의(agreed)된 판단만 — 확정 안 된 판단은 사실로 쓰지 않고
   파트 1 값을 그대로 두되 '확인 안 됨'으로 표시한다.

     사실 이름           확정하는 질문   값
     waste_code         A-1           A-1 이 yes/conditional 이고 items 가 있으면 그 첫 번호 (번호가 고쳐졌을 수 있음)
     waste_status       A-2           yes → designated, no → non_designated, conditional → designated_suspect
     r_codes            D-1           D-1 items (R코드)
     treatment_codes    F-1           F-1 items (처리방법 코드)
     plan_check         H-2           지정폐기물 처리계획 확인 대상 여부 (해당 없음 = 지정폐기물 아님)
                                      G-2(전자인계)가 기다림 — 제20조가 '제18조의2제1항 각 호의 지정폐기물'을 대상으로 정함.
                                      2026.11.20 세척액: H-2 를 모른 채 판단해 실행마다 조건부/예가 갈렸음

2. 숨은 순서 (fact_dependencies)
   위 사실이 필요한 질문은 그 사실을 확정하는 질문이 끝날 때까지 기다린다 (schedule 이 when 과 같이 본다).
   예외: 확정하는 질문 자신, 그리고 A 단계(분류)끼리는 서로 기다리지 않는다 — A-1·A-2 는 파트 1 값으로 1차에 판단.
   하위 질문(.cN)은 부모가 기다리는 것을 그대로 기다린다.

3. 앞 판단 (prior_findings)
   judge 프롬프트의 [앞 판단] 칸. 그 질문이 when 으로 기다린 질문 + 그 질문이 쓰는 사실을 확정한 질문만.
   (모든 판단을 넣으면 근거가 흐려진다.) 확정 여부를 같이 적는다.
"""
from __future__ import annotations

from typing import Any

from law_agent.state import COMPUTED_FACTS, AgentState, Question, Verdict, when_dependencies

# 사실 이름 → 그 사실을 확정하는 질문
FACT_PRODUCERS: dict[str, str] = {
    "waste_code": "A-1",
    "waste_status": "A-2",
    "r_codes": "D-1",
    "treatment_codes": "F-1",
    "plan_check": "H-2",
}
PLAN_CHECK = {"yes": "처리계획 확인 대상", "no": "처리계획 확인 대상 아님", "conditional": "조건에 따라 처리계획 확인 대상"}
# 이 단계 질문은 FACT_PRODUCERS 를 기다리지 않는다 (분류 단계는 파트 1 값으로 시작)
NO_WAIT_STAGES = ("A",)

STATUS_FROM_A2 = {"yes": "designated", "no": "non_designated", "conditional": "designated_suspect"}


def _agreed(v: Verdict | None) -> bool:
    return v is not None and v.status == "agreed" and v.answer != "unknown"


def fact_dependencies(q: Question) -> list[str]:
    """q 가 사실 때문에 기다려야 하는 질문 id (when 과 별도)."""
    if q.stage in NO_WAIT_STAGES:
        return []
    deps = []
    for fact in q.needs_facts:
        p = FACT_PRODUCERS.get(fact)
        if p and p != q.base_id and p not in deps:
            deps.append(p)
    return deps


def all_dependencies(q: Question) -> list[str]:
    """when 의존 + 사실 의존 (중복 없음, 순서 고정)."""
    return list(dict.fromkeys(when_dependencies(q.when) + fact_dependencies(q)))


def effective_facts(state: AgentState) -> dict:
    """파트 1 사실 + 확정된 판단. 형식은 extract_facts 와 같고 'confirmed', 'unconfirmed' 를 더한다.
      confirmed   : {사실 이름: 확정한 질문 id}  — 판단으로 확정된 사실
      unconfirmed : {사실 이름: 확정할 질문 id}  — 확정할 질문이 끝났지만 합의가 안 돼 파트 1 값 그대로인 사실
    """
    base = state.get("facts") or {}
    values = dict(base.get("values") or {})
    missing = set(base.get("missing") or [])
    verdicts = state.get("verdicts") or {}
    confirmed: dict[str, str] = {}
    unconfirmed: dict[str, str] = {}

    a1 = verdicts.get("A-1")
    if a1 is not None and a1.final:
        items = (a1.value or {}).get("items") or []
        if _agreed(a1) and a1.answer in ("yes", "conditional") and items:
            values["waste_code"] = items[0]
            confirmed["waste_code"] = "A-1"
            missing.discard("waste_code")
        elif not (_agreed(a1) and a1.answer in ("yes", "conditional")):
            unconfirmed["waste_code"] = "A-1"
        else:                                         # A-1 yes 인데 번호를 따로 안 적음 = 파트 1 번호가 맞음
            confirmed["waste_code"] = "A-1"

    a2 = verdicts.get("A-2")
    if a2 is not None and a2.final:
        if _agreed(a2) and a2.answer in STATUS_FROM_A2:
            values["waste_status"] = STATUS_FROM_A2[a2.answer]
            confirmed["waste_status"] = "A-2"
            missing.discard("waste_status")
        else:
            unconfirmed["waste_status"] = "A-2"

    for fact, qid in (("r_codes", "D-1"), ("treatment_codes", "F-1")):
        v = verdicts.get(qid)
        if v is not None and v.final:
            if _agreed(v):
                values[fact] = list((v.value or {}).get("items") or [])
                if fact == "r_codes":                  # 뒤 질문(D-2·D-3·D-4…)에는 범위 안 R코드만 (D-3 이 11개 유형 기준을 다 읽어 4분 걸림)
                    from law_agent.scope import in_scope_rcodes
                    values[fact] = in_scope_rcodes(values[fact])
                confirmed[fact] = qid
            else:
                unconfirmed[fact] = qid

    h2 = verdicts.get("H-2")
    if h2 is not None and h2.final:
        if h2.status == "not_applicable":
            values["plan_check"] = "해당 없음 (지정폐기물 아님)"
            confirmed["plan_check"] = "H-2"
        elif _agreed(h2) and h2.answer in PLAN_CHECK:
            values["plan_check"] = PLAN_CHECK[h2.answer]
            confirmed["plan_check"] = "H-2"
        else:
            unconfirmed["plan_check"] = "H-2"

    provisional = {}
    if unconfirmed.get("waste_code") == "A-1" and _code_in_table(values.get("waste_code")):
        provisional["waste_code"] = "A-1"            # 확정은 안 됐지만 파트 1 번호가 별표 4에 있는 번호 → 잠정으로 씀
    return {**base, "values": values, "missing": sorted(missing), "confirmed": confirmed, "unconfirmed": unconfirmed,
            "provisional": provisional}


def _code_in_table(code: Any) -> bool:
    """파트 1 분류번호가 별표 4 분류번호 표(사실 표)에 있는지. 표를 못 읽으면 False (잠정으로 쓰지 않음)."""
    if not code:
        return False
    try:
        from law_agent.tools.fact_tools import waste_code_exists
        return bool(waste_code_exists(str(code)))
    except Exception:                                  # noqa: BLE001 — 데이터 경로 없음 등
        return False


DERIVED_ONLY = ("r_codes", "treatment_codes", "gate", "plan_check")    # 사례에 없고 판단(또는 경로)에서만 나오는 사실


def question_facts(q: Question, state: AgentState, eff: dict | None = None) -> dict:
    """judge 에 넘길 사례 사실: 그 질문의 needs_facts 만, 값마다 출처를 붙인다.
      {"waste_status": {"값": "non_designated", "출처": "A-2 판단"}, ...}
    출처: "A-2 판단" (확정) / "파트 1" / "파트 1 (A-2 판단 확정 안 됨)" /
          "없음 (파트 1이 확인하지 못함)" (case.missing 에 명시) / "제공되지 않음 (파트 1 입력 항목에 없음)".
    r_codes·treatment_codes 는 확정됐을 때만 넣는다 (gate 는 경로 단계 값이라 넣지 않음).
    하위 질문이면 그 성분 정보를 더한다."""
    eff = eff if eff is not None else effective_facts(state)
    values = eff.get("values", {})
    confirmed, unconfirmed = eff.get("confirmed", {}), eff.get("unconfirmed", {})
    missing = set(eff.get("missing", []))
    explicit = set(eff.get("explicit_missing", []))
    out: dict[str, Any] = {}
    for n in q.needs_facts:
        if n in DERIVED_ONLY:
            if n in confirmed:
                out[n] = {"값": values.get(n), "출처": f"{confirmed[n]} 판단"}
            continue
        if n in confirmed:
            src = f"{confirmed[n]} 판단"
        elif n in unconfirmed:
            src = f"파트 1 ({unconfirmed[n]} 판단 확정 안 됨)"
        elif n in explicit:
            src = "없음 (파트 1이 확인하지 못함)"
        elif n in missing or values.get(n) is None:
            src = "제공되지 않음 (파트 1 입력 항목에 없음)"
        elif n in COMPUTED_FACTS:
            src = COMPUTED_FACTS[n]
        else:
            src = "파트 1"
        out[n] = {"값": values.get(n), "출처": src}
    if q.component:
        comps = (state.get("case") or {}).get("components") or []
        if 0 < q.component <= len(comps):
            out["이 질문의 성분"] = {"값": comps[q.component - 1], "출처": "파트 1"}
    return out


def _summary(v: Verdict) -> dict:
    status = {"agreed": "확정", "needs_info": "확인 안 됨 — 배출업체 사실 필요",
              "needs_human": "확인 안 됨 — 사람 검토", "not_applicable": "해당 없음"}.get(v.status, v.status)
    d: dict[str, Any] = {"상태": status, "답": v.answer}
    items = (v.value or {}).get("items")
    if items:
        d["항목"] = items
    if (v.value or {}).get("result"):
        d["비교"] = {k: v.value.get(k) for k in ("criterion", "case_value", "result") if k in v.value}
    if (v.value or {}).get("text"):
        d["내용"] = v.value["text"][:300]
    if v.conditions:
        d["조건"] = [c.get("text", "") for c in v.conditions][:5]
    if v.missing_facts:
        d["빠진 사실"] = v.missing_facts
    return d


def prior_findings(q: Question, state: AgentState) -> dict[str, dict]:
    """[앞 판단] 칸: {질문 id: 요약}. 최종 판단이 난 의존 질문만 (when + 사실). 하위 질문이면 같은 성분의
    하위 판단을 먼저 쓴다 (예: D-1.c2 → B-1.c2 가 있으면 B-1.c2, 없으면 B-1)."""
    verdicts = state.get("verdicts") or {}
    qtext = {x.id: x.text for x in state.get("questions") or []}
    out: dict[str, dict] = {}
    for dep in all_dependencies(q):
        ids = [f"{dep}.c{q.component}", dep] if q.component else [dep]
        for i in ids:
            v = verdicts.get(i)
            if v is not None and v.final:
                out[i] = {"질문": qtext.get(i, ""), **_summary(v)}
                break
    return out
