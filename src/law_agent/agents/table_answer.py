"""표 답 노드 — 법 조문 자체가 '분류번호별 표'인 질문은 판정표로 답한다 (2026.11.18 설계 변경, 규칙 2 개정).

  A-1  분류번호 확인          ← waste_codes_4.csv (시행규칙 별표 4). 기관 회신·기존 신고로 정해진 번호만 '예',
                                 별표 4에 없으면 '아니오'. AI 추정·출처 불명 번호는 LLM (이름·성상 대조가 필요)
  D-1  허용 R코드·사전 분석   ← recycling_types_4_3.csv (시행규칙 별표 4의3) + not_in_4_3.csv
  C-1  순환자원 지정 품목     ← designated_resources.csv (순환자원 지정 고시 별표 1·2)
  F-1  처분 방법·처리방법 코드 ← disposal_methods_5.csv (시행규칙 별표 5)
  F-2  처분 조건·수치 기준     ← disposal_methods_5.csv (F-1 과 같은 행)

왜: 이 법들은 원문이 이미 '분류번호 → 답' 표다. LLM 이 원문 표를 읽게 하면 행 잘림·발췌 위치 문제로 흔들리고
    비용이 크다 (데모 1: D-1 재검색 2회, F-1 근거 155자). 해석이 필요한 질문(A-2, B-1, D-2, D-3·D-4, G·I·J …)은 LLM.

규칙
- 찾는 열쇠는 분류번호(코드)뿐이다. 단어로 찾지 않는다 → '단어가 안 맞아 못 찾음'이 생기지 않는다.
- 분류번호가 판단으로 확정(A-1)됐을 때만 표로 답한다. 확정 안 됐거나 표에 없으면 None → 기존 LLM 경로.
  (표에 없음 = 불가 가 아니다)
- C-1 에서 품목이 한정된 행(예: 51-03-01 중 IC-Tray)만 있으면 품목 대조가 필요한 해석 → LLM 으로 넘김.
- 표로 답한 판단에도 근거를 남긴다: 표 행의 '근거' 칸(법 원문 키)과, 있으면 원문 문장(근거_인용·적용범위_원문).
- 어느 표를 몇 번 썼는지 TABLE_USES 에 기록 (감사·평가: 이 질문들은 판정표와 비교해도 같은 값이 나오는 게 정상).
"""
from __future__ import annotations

import re
import time
from typing import Any

from law_agent.agents.context import effective_facts
from law_agent.agents.schedule import ready_questions
from law_agent.state import AgentState, Question, Verdict
from law_agent.tools import fact_tools

TABLE_QUESTIONS = ("A-1", "D-1", "C-1", "F-1", "F-2", "E-1")
# A-1 을 표로 확정하는 분류번호 출처 (2026.11.20): 기관 회신·기존 신고처럼 이미 확인된 번호. AI 추정·출처 불명은 LLM.
CONFIRMED_SOURCES = {"current_case_authority_reply", "authority_reply", "competent_authority", "existing_declaration",
                     "previous_declaration", "allbaro", "allbaro_record", "lab_classification"}
FLAG_TABLE = "table_answer"
RX_CODE = re.compile(r"^\d{2}(-\d{2}){0,2}$")


def _rows(name: str) -> list[dict]:
    return fact_tools.read_answer_table(name)


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;,]", v or "") if x.strip()]


def _verdict(qid: str, answer: str, value: dict, quotes: list[dict], conditions: list[dict] | None = None,
             missing: list[str] | None = None) -> Verdict:
    return Verdict(question_id=qid, status="agreed", answer=answer, value=value, conditions=conditions or [],
                   quotes=quotes, votes=[{"model": "판정표", "answer": answer, "valid": True, "drop_reason": ""}],
                   missing_facts=missing or [], flags=[FLAG_TABLE])


# ── A-1 ─────────────────────────────────────────────────────

def _confirmed_source(case: dict, code: str) -> str | None:
    """이 분류번호가 이미 확인된 출처에서 왔으면 그 설명. 아니면 None (→ LLM 이 이름·성상과 맞는지 판단)"""
    cls = case.get("classification") or {}
    st, src = str(cls.get("status") or ""), str(cls.get("source_type") or "")
    if src in CONFIRMED_SOURCES or "authority" in st:
        return "기관 회신으로 정해진 번호"
    dec = case.get("existing_declaration") or {}
    if dec.get("exists") and str(dec.get("waste_code") or "") == code:
        return "기존 배출자 신고·올바로 기록의 번호"
    return None


def a1(case: dict, code: str, qid: str = "A-1") -> Verdict | None:
    """분류번호 확인. 별표 4에 없으면 '아니오'(표로 확정). 있고 출처가 확인된 번호면 '예'. 그 밖(AI 추정 등)은 None → LLM"""
    info = fact_tools.waste_code_info(code)              # 표 파일이 없으면 OSError → answer() 가 LLM 으로 넘김
    if info is None:
        return _verdict(qid, "no", {"items": [], "reason": f"{code} 는 시행규칙 별표 4에 없는 번호"},
                        [{"ref": "WMA_RULE:B4", "text": "폐기물의 종류별 세부분류는 별표 4와 같다"}])
    why = _confirmed_source(case, code)
    if why is None:
        return None
    cls = case.get("classification") or {}
    name = info.get("명칭") or ""
    value = {"items": [code], "name": name, "basis": f"{why} — 별표 4에 있는 번호라 표로 확정 (이름·성상 대조는 회신에서 끝남)"}
    if cls.get("conditions"):
        value["scope"] = str(cls["conditions"])
    return _verdict(qid, "yes", value, [{"ref": info.get("근거") or "WMA_RULE:B4", "text": f"{code} {name}".strip()}])


# ── D-1 ─────────────────────────────────────────────────────

def d1(code: str, qid: str = "D-1") -> Verdict | None:
    """별표 4의3: 그 분류번호의 원래 R코드(펼친 세부 코드 말고)와 사전 분석. 표에 없으면 not_in_4_3 → 그래도 없으면 None."""
    rows = [r for r in _rows("recycling_types_4_3.csv") if r.get("분류번호") == code]
    if not rows:
        miss = [r for r in _rows("not_in_4_3.csv") if r.get("분류번호") == code]
        if miss:
            return _verdict(qid, "no", {"items": []},
                            [{"ref": "WMA_RULE:B4의3", "text": f"{code}: {miss[0].get('상태', '별표 4의3에 유형 없음')}"}],
                            [{"text": "별표 4의3에 재활용 유형이 없음 — 재활용환경성평가(R-11)를 받은 경우만 재활용 가능",
                              "ref": "WMA_RULE:B4의3"}])
        return None
    if any((r.get("재활용_금지") or "").upper() == "Y" for r in rows):
        return _verdict(qid, "no", {"items": []}, [{"ref": rows[0].get("근거", ""), "text": f"{code}: 재활용 금지 표시"}])
    items: list[str] = []
    attrs: dict[str, dict] = {}
    for r in rows:
        if "에서 펼침" in (r.get("상태") or ""):           # 원문에 없는, 상위 코드를 펼친 행
            continue
        rc = (r.get("r_code") or "").strip()
        if not rc:
            continue
        pre = (r.get("사전분석확인") or "").strip() not in ("", "해당 없음")
        if rc not in items:
            items.append(rc)
        attrs[rc] = {"pre_analysis": bool(attrs.get(rc, {}).get("pre_analysis")) or pre}   # 두 묶음에 나오면 참 쪽
    if not items:
        return None
    name = rows[0].get("폐기물의_종류", "")
    quote = {"ref": rows[0].get("근거", ""), "text": f"{code} {name}: {', '.join(items)}"}
    # 사전 분석은 R코드마다 attrs 로만 둔다 → composer 가 그 R코드의 경로에만 required_tests 를 붙인다.
    # (답 전체를 conditional 로 하면 사전 분석이 없는 재생이용 경로까지 조건부가 됨 — 2026.11.17 데모 1 R-10 문제)
    return _verdict(qid, "yes", {"items": items, "attrs": attrs}, [quote])


# ── C-1 ─────────────────────────────────────────────────────

def c1(code: str, case: dict, qid: str = "C-1") -> Verdict | None:
    """순환자원 지정 품목. 그 분류번호 행이 없으면 '아님'. 품목 한정 없는 행이 있으면 '예'.
    품목 한정 행만 있으면: 품목 이름이 사례 이름에 그대로 있으면 '조건부', 아니면 None (LLM 이 품목을 판단)."""
    rows = [r for r in _rows("designated_resources.csv") if code in _split(r.get("분류번호", ""))]
    if not rows:
        return _verdict(qid, "no", {}, [{"ref": "CEA_DESIG:B1", "text": f"{code}: 순환자원 지정 고시 품목에 없음"}])
    free = [r for r in rows if (r.get("품목_한정") or "").upper() != "Y"]
    use = free or [r for r in rows if _item_in_case(r.get("품목", ""), case)]
    if not use:
        return None
    r = use[0]
    rcs = sorted({x for u in use for x in _split(u.get("허용_R코드", ""))})
    quote = {"ref": r.get("근거", ""), "text": (r.get("적용범위_원문") or r.get("품목", ""))[:300]}
    conds = [{"text": (u.get("방법기준_원문") or "")[:500], "ref": u.get("근거", "")} for u in use if u.get("방법기준_원문")]
    answer = "conditional" if conds or not free else "yes"
    return _verdict(qid, answer, {"items": [u.get("품목", "") for u in use], "r_codes": rcs}, [quote], conds)


def _item_in_case(item: str, case: dict) -> bool:
    """'폐합성수지류 중 아이씨트레이(IC-Tray)' → 괄호 안·'중' 뒤의 품목 이름이 사례 이름·공정 설명에 있으면 참."""
    text = re.sub(r"\s+", "", f"{case.get('name', '')} {case.get('process_description', '')}").lower()
    tail = item.split("중", 1)[-1]
    words = re.findall(r"[가-힣A-Za-z][가-힣A-Za-z\-]{1,}", tail)
    return any(re.sub(r"\s+", "", w).lower() in text for w in words if len(w) >= 2)


# ── F-1 · F-2 ───────────────────────────────────────────────

def _disposal_rows(code: str) -> list[dict]:
    return [r for r in _rows("disposal_methods_5.csv")
            if code in _split(r.get("적용_분류번호", "")) and (r.get("status") or "confirmed") == "confirmed"]


def f1(code: str, qid: str = "F-1") -> Verdict | None:
    rows = _disposal_rows(code)
    if not rows:
        return None                                    # 분류번호 행이 없는 일반 규정(예: D3-1가)은 사례 해석 → LLM
    tcs: list[str] = []
    for r in rows:
        for c in _split(r.get("처리방법_코드", "")):
            if c not in tcs:
                tcs.append(c)
    if not tcs:
        return None
    quotes = [{"ref": r.get("근거_청크", ""), "text": (r.get("근거_인용") or r.get("처리방법_원문", ""))[:300]}
              for r in rows]
    conds = [{"text": " / ".join(x for x in (r.get("조건"), r.get("수치_기준")) if x), "ref": r.get("근거_청크", "")}
             for r in rows if r.get("조건") or r.get("수치_기준")]
    answer = "conditional" if conds else "yes"
    return _verdict(qid, answer, {"items": tcs, "methods": [r.get("처리방법_원문", "") for r in rows]}, quotes, conds)


def f2(code: str, qid: str = "F-2") -> Verdict | None:
    rows = _disposal_rows(code)
    if not rows:
        return None
    conds = [{"text": " / ".join(x for x in (r.get("조건"), r.get("수치_기준")) if x), "ref": r.get("근거_청크", "")}
             for r in rows if r.get("조건") or r.get("수치_기준")]
    quotes = [{"ref": r.get("근거_청크", ""), "text": (r.get("근거_인용") or "")[:300]} for r in rows]
    if not conds:
        return _verdict(qid, "yes", {"text": "별표 5에 이 분류번호의 추가 조건 없음"}, quotes)
    return _verdict(qid, "conditional", {"text": "; ".join(c["text"] for c in conds)}, quotes, conds)


# ── E-1 ─────────────────────────────────────────────────────

OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}


def _case_value(case: dict, path: str):
    cur: Any = case
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    try:
        return float(cur) if cur not in (None, "") else None
    except (TypeError, ValueError):
        return None


def e1(state: AgentState, qid: str = "E-1") -> Verdict | None:
    """에너지 회수기준 (시행규칙 제3조, energy_recovery_criteria). D-1 의 에너지 회수 R코드에 해당하는 행만.
      사례 값(analysis.*) 행 : 계산으로 비교 → 하나라도 미달이면 미충족, 값이 없으면 needs_info (파트 1에 물음)
      업체 값(partner.*) 행  : 배출업체가 답할 수 없는 값 → 조건으로 (업체 확인), needs_info 로 만들지 않는다
    2026.11.18 데모 1: LLM 이 회수효율·회수열 이용(업체 값)을 '빠진 사실'로 적어 에너지 회수 경로가 needs_info 가 됐음."""
    d = (state.get("verdicts") or {}).get("D-1")
    if d is None or d.status != "agreed":
        return None
    energy = [x for x in (d.value or {}).get("items") or [] if x.startswith(("R-8", "R-9"))]
    rows = [r for r in _rows("energy_recovery_criteria.csv")
            if (r.get("상태") or "confirmed") == "confirmed"
            and any(rc == e or rc.startswith(e + "-") or e.startswith(rc) for rc in _split(r.get("r_codes", "")) for e in energy)]
    if not rows:
        return None
    case = state.get("case") or {}
    checks, conds, missing = [], [], []
    for r in rows:
        crit = f"{r.get('항목')} {r.get('비교', '')} {r.get('기준값', '')}{r.get('단위', '')}".strip()
        src = r.get("입력") or ""
        if src.startswith("partner."):
            conds.append({"text": f"업체 확인: {r.get('원문') or crit}", "ref": r.get("근거키", "")})
            continue
        if not src.startswith("analysis.") or (r.get("비교") or "").strip() not in OPS:
            # 사례 수치가 아닌 행(용도·처리 방식 등 '확인' 조건) → 조건으로. 파트 1에 묻지 않는다 (2026.11.18 서버 실행)
            conds.append({"text": f"확인: {r.get('원문') or crit}", "ref": r.get("근거키", "")})
            continue
        val = _case_value(case, src) if src else None
        if val is None:
            missing.append(r.get("항목") or src)
            checks.append({"criterion": crit, "case_value": None, "unit": r.get("단위"), "result": "unknown"})
            continue
        op = OPS.get((r.get("비교") or "").strip())
        try:
            ok = op(val, float(r.get("기준값"))) if op else None
        except (TypeError, ValueError):
            ok = None
        checks.append({"criterion": crit, "case_value": val, "unit": r.get("단위"),
                       "result": "met" if ok else "not_met" if ok is False else "unknown"})
    quotes = [{"ref": r.get("근거키", ""), "text": (r.get("원문") or "")[:300]} for r in rows]
    if any(c["result"] == "not_met" for c in checks):
        bad = next(c for c in checks if c["result"] == "not_met")
        return _verdict(qid, "no", {**bad}, quotes, conds)
    if missing:
        v = _verdict(qid, "unknown", {"criterion": checks[0]["criterion"] if checks else None, "result": "unknown"},
                     quotes, conds)
        v.status, v.missing_facts = "needs_info", sorted(set(missing))
        return v
    first = checks[0] if checks else {"criterion": "업체 확인 항목만", "case_value": None, "unit": None, "result": "met"}
    return _verdict(qid, "conditional" if conds else "yes", {**first, "checks": checks}, quotes, conds)


# ── 노드 ───────────────────────────────────────────────────

def _code_for(q: Question, state: AgentState, eff: dict) -> str | None:
    """표 조회에 쓸 분류번호. 사례 분류번호는 A-1 로 확정됐을 때만. 성분 하위 질문은 파트 1 성분 분류번호."""
    if q.component is not None:                        # 1부터 센다 (planner.sub_questions)
        comps = [c for c in (state.get("case") or {}).get("components") or [] if isinstance(c, dict)]
        c = comps[q.component - 1] if 0 < q.component <= len(comps) else {}
        code = str((c or {}).get("waste_code") or "")
        return code if RX_CODE.match(code) and code.count("-") == 2 else None
    if eff.get("confirmed", {}).get("waste_code") != "A-1" and eff.get("provisional", {}).get("waste_code") != "A-1":
        return None
    code = str(eff.get("values", {}).get("waste_code") or "")
    return code if RX_CODE.match(code) and code.count("-") == 2 else None


def answer(q: Question, state: AgentState, eff: dict) -> Verdict | None:
    if q.base_id not in TABLE_QUESTIONS:
        return None
    if q.base_id == "E-1":
        try:
            return e1(state, q.id) if q.component is None else None
        except (OSError, KeyError, RuntimeError):
            return None
    if q.base_id == "A-1":
        if q.component is not None:
            return None
        code = str((eff.get("values") or {}).get("waste_code") or "")
        if not (RX_CODE.match(code) and code.count("-") == 2):
            return None
        try:
            return a1(state.get("case") or {}, code, q.id)
        except (OSError, KeyError, RuntimeError):
            return None
    code = _code_for(q, state, eff)
    if not code:
        return None
    try:
        v = {"D-1": lambda: d1(code, q.id), "C-1": lambda: c1(code, state.get("case") or {}, q.id),
             "F-1": lambda: f1(code, q.id), "F-2": lambda: f2(code, q.id)}[q.base_id]()
    except (OSError, KeyError, RuntimeError):          # 표 파일·데이터 경로 없음 (시험 환경 등) → LLM 경로
        return None
    if v is not None and q.component is None and eff.get("confirmed", {}).get("waste_code") != "A-1":
        # 잠정 분류번호(A-1 미확정)로 찾은 답 → 조건부 + 이유 (2026.11.18 데모 2: 04-02-00 할로겐족 여부 미확인)
        v.answer = "conditional" if v.answer == "yes" else v.answer
        v.conditions = [{"text": f"분류번호 {code} 가 확정되지 않음 (A-1) — 이 번호 기준의 잠정 결과", "ref": ""}] + v.conditions
        v.flags = v.flags + ["provisional_code"]
    return v


def node(state: AgentState) -> dict:
    """표 답 노드. 이번 차례 질문 중 표로 답할 수 있는 것은 verdict 를 바로 확정 (모델 호출 없음)."""
    t0 = time.time()
    eff = effective_facts(state)
    out: dict[str, Verdict] = {}
    skipped: dict[str, str] = {}
    for q in ready_questions(state):
        if q.base_id not in TABLE_QUESTIONS:
            continue
        v = answer(q, state, eff)
        if v is None:
            skipped[q.id] = "표에 없음·분류번호 미확정 → LLM"
        else:
            out[q.id] = v
    upd: dict[str, Any] = {"audit": [{"node": "table_answer", "seconds": round(time.time() - t0, 3),
                                      "summary": {"answered": sorted(out), "to_llm": skipped},
                                      "output": {k: {"answer": v.answer, "value": v.value} for k, v in out.items()}}]}
    if out:
        upd["verdicts"] = out
    return upd
