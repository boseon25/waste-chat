"""⑧ 출력 — 경로 + 업체 → RouteProposal v1.1 (파트 3), InformationRequest (파트 1), 추천 카드 (화면).

칸 이름과 모양은 route_agent proposal/route_proposal.to_route_proposals 와 같다 (파트 3 계약 그대로).
원본이 판정표를 읽던 칸만 판단 결과로 채운다:
  document_triggers : condition_texts·plan_check 표 → G-1(위탁 기준) · G-2(전자인계) · H-1(배출자 신고)
                      · H-2·H-3(처리계획 확인) · G-4(관문 1 서류 제외) · I-2(신고 업체 범위)
  requirements      : energy_recovery_criteria 표 → E-1 판단 (composer 가 required_tests 에 넣은 것)
  evidences·related_laws : 판정표 근거 칸·적용 법령 목록 → 판단의 인용 조문 (ref + 원문)
  restrictions      : 사전검증 결과 → B-1·B-2 판단
더한 칸 (파트 3은 몰라도 됨): law_review(질문별 판단) · evidence_pack_ids · engine · law_questions(경로가 쓴 질문)
"""
from __future__ import annotations

import re

from statistics import mean
from typing import Any

from law_agent.agents.composer import BASIS_STATUS, Composed
from law_agent.core.lawstore import LAW_NAME
from law_agent.core.proposal.checks import build_checks
from law_agent.core.proposal.info_request import build_information_request
from law_agent.core.proposal.questions import build_questions
from law_agent.core.proposal.route_proposal import (
    SCHEMA_VERSION, _comparison, _main_step, _partner, _partner_evidences, assign_route_ids,
)
from law_agent.state import AgentState, Verdict, parse_ref

ENGINE = "law_agent_v0"
UNCONFIRMED = "확인 필요 (판단 확정 안 됨)"


# ── 조문 ref → 이름 ─────────────────────────────────────────

def article_label(key: str) -> str:
    """"13의2" → "제13조의2", "B4의3:2" → "별표 4의3", "§Ⅵ" → "Ⅵ", "전체" → ""."""
    k = key.split(":", 1)[0].split("/", 1)[0]
    if k == "전체":
        return ""
    if k.startswith("§"):
        return k[1:]
    if k.startswith("B"):
        return f"별표 {k[1:]}"
    num, _, sub = k.partition("의")
    return f"제{num}조" + (f"의{sub}" if sub else "")


def law_of(ref: str) -> dict:
    p = parse_ref(ref) or {}
    law_id = p.get("law", "")
    return {"law_id": law_id, "law": LAW_NAME.get(law_id, law_id), "article": article_label(p.get("key", "")),
            "key": p.get("key"), "effective_date": p.get("effective")}


# ── 서류 (document_triggers) ─────────────────────────────────

def _trig(trigger: str, fact: str, v: Verdict | None, text: str | None = None) -> dict:
    ok = v is not None and v.status == "agreed"
    ref = (v.quotes or [{}])[0].get("ref") if v is not None else None
    return {"trigger": trigger, "fact": fact, "condition_id": f"LA-{v.question_id}" if v is not None else None,
            "text": text, "basis": ref, "basis_status": BASIS_STATUS if ok else (UNCONFIRMED if v is not None else None)}


def _vtext(v: Verdict | None) -> str | None:
    if v is None:
        return None
    t = (v.value or {}).get("text")
    if t:
        return t
    conds = [c.get("text", "") for c in v.conditions if c.get("text")]
    return " / ".join(conds) or None


def document_triggers(route: dict, vs: dict[str, Verdict], ton_month: float | None, has_report: bool) -> list[dict]:
    codes = ", ".join(route.get("waste_codes") or [])
    designated = route.get("waste_status") == "designated"
    kind = "지정" if designated else "사업장일반"
    if route["gate"] == 1:
        g4 = vs.get("G-4")
        return [_trig("폐기물 위탁 서류 해당 없음", f"{codes} 지정 순환자원 판매 (고시 기준을 지키는 동안 폐기물이 아님)", g4,
                      _vtext(g4) or "위탁계약·수탁처리능력 확인·전자인계 대상이 아닙니다. 고시의 용도·방법 기준을 지켜야 합니다.")]
    if route["regulatory"] == "rejected":
        return []
    g1 = vs.get("G-1")
    out = [_trig("위탁계약서", f"{kind} {codes} 처리 위탁 (관문 {route['gate']})", g1, _vtext(g1)),
           _trig("수탁처리능력 확인", "위탁 계약 전 처리 업체의 수탁처리능력확인서", g1, _vtext(g1))]
    g2 = vs.get("G-2")
    if g2 is not None and g2.status == "agreed" and g2.answer == "no":
        out.append(_trig("전자인계 제외", f"{kind} {codes}", g2, _vtext(g2)))
    elif g2 is not None and g2.status != "not_applicable":
        out.append(_trig("전자인계 입력 (올바로)", f"{kind} {codes} 위탁", g2,
                         _vtext(g2) or "전자정보처리프로그램(올바로)으로 인계·인수 내용을 입력합니다."))
    h1 = vs.get("H-1")
    if h1 is not None and h1.status != "not_applicable":
        res = (h1.value or {}).get("result")
        if res != "not_met":
            out.append(_trig("사업장폐기물배출자 신고", f"신고 대상 기준: {(h1.value or {}).get('criterion') or '확인 필요'}"
                             f" / 사례: {(h1.value or {}).get('case_value')}", h1,
                             "신고 대상입니다." if res == "met" else "신고 대상 여부를 확인해야 합니다."))
    if designated:
        h2, h3 = vs.get("H-2"), vs.get("H-3")
        kg = round(ton_month * 1000, 1) if ton_month else None
        fact = "지정폐기물 위탁" + (f", 월 평균 {kg:g}kg" if kg else ", 월 배출량 모름")
        if h2 is not None and h2.status != "not_applicable":
            res = (h2.value or {}).get("result")
            label = {"met": "확인 대상", "not_met": "확인 대상 아님"}.get(res, "확인 필요")
            t = _trig("지정폐기물 처리계획 확인", f"{fact} → {label}", h2, _vtext(h3) if res != "not_met" else None)
            t["plan_check"] = {"decision_label": label, "criterion": (h2.value or {}).get("criterion"),
                               "case_value": (h2.value or {}).get("case_value"), "from": "H-2 판단"}
            out.append(t)
    if has_report:
        i2 = vs.get("I-2")
        out.append(_trig("신고 범위 확인 자료", "추천 업체 중 폐기물처리 신고 업체 있음", i2,
                         _vtext(i2) or "신고 업체는 신고 범위와 하루 처리량 안에서만 받을 수 있습니다."))
    return out


# ── 근거·제한 ────────────────────────────────────────────────

def evidences(route: dict, used: list[str], vs: dict[str, Verdict]) -> list[dict]:
    """경로가 쓴 판단의 인용 조문. 같은 조문이면 하나로, used_for 에 질문 id."""
    out: dict[str, dict] = {}
    for qid in used:
        v = vs.get(qid)
        for q in (v.quotes if v is not None else []):
            ref = q.get("ref")
            if not ref:
                continue
            lw = law_of(ref)
            e = out.setdefault(ref, {"evidence_id": f"LAW-{lw['law_id']}-{(lw['key'] or '').split(':')[0]}",
                                     "source": f"{lw['law']} {lw['article']}".strip(), "ref": ref, "law": lw["law"],
                                     "article": lw["article"], "effective_date": lw["effective_date"], "used_for": [],
                                     "text": q.get("text"), "text_basis": "판단 인용 (원문 대조 통과)"})
            if qid not in e["used_for"]:
                e["used_for"].append(qid)
    # 경로를 가르는 판단(재활용 유형·처분 방법·순환자원·금지)의 조문을 앞에, 공통(분류·위탁·신고)은 뒤에
    return sorted(out.values(), key=lambda e: min(EVIDENCE_ORDER.get(u.split(".")[0][0], 9) for u in e["used_for"]))


EVIDENCE_ORDER = {"D": 0, "E": 0, "F": 0, "C": 0, "B": 1, "A": 2, "G": 3, "H": 3, "I": 3, "J": 4}


def restrictions(vs: dict[str, Verdict]) -> list[dict]:
    out = []
    for qid in ("B-1", "B-2"):
        v = vs.get(qid)
        if v is None or v.status == "not_applicable" or (v.status == "agreed" and v.answer == "no"):
            continue
        out.append({"rule_id": qid, "action": v.answer if v.status == "agreed" else v.status,
                    "description": _vtext(v) or ", ".join((v.value or {}).get("items") or []) or None,
                    "basis": (v.quotes or [{}])[0].get("ref")})
    return out


EXCERPT_CHARS = 2500        # 관리자 화면에 남길 근거 원문 길이 (조각 하나)


def _judge_trace(state: AgentState) -> dict[str, list[dict]]:
    """질문별 judge 기록 (차례마다): 모델에게 준 사실·앞 판단, 부른 모델, 3번째 모델 부른 이유."""
    out: dict[str, list[dict]] = {}
    n = 0
    for a in state.get("audit") or []:
        if a.get("node") != "judge":
            continue
        n += 1
        for qid, m in (a.get("output") or {}).items():
            out.setdefault(qid, []).append({"try": n, "inputs": m.get("inputs") or {}, "escalated": m.get("escalated"),
                                            "light": m.get("light"), "answers": m.get("answers"),
                                            "errors": m.get("errors"),
                                            "models": [{"model": x.get("model"), "cached": x.get("cached"),
                                                        "seconds": x.get("seconds")} for x in m.get("models") or []]})
    return out


def law_review(state: AgentState) -> list[dict]:
    """질문별 판단 요약 (재현·감사·사람 검토용). 관리자 화면은 이것으로 판단 과정을 처음부터 끝까지 보여준다."""
    qtext = {q.id: q.text for q in state.get("questions") or []}
    answers = state.get("answers") or {}
    packs = state.get("packs") or {}
    jt = _judge_trace(state)
    out = []
    for qid in [q.id for q in state.get("questions") or []]:
        v = (state.get("verdicts") or {}).get(qid)
        if v is None:
            continue
        p = packs.get(qid)
        out.append({"question_id": qid, "question": qtext.get(qid), "status": v.status, "answer": v.answer,
                    "value": v.value, "conditions": [c.get("text") for c in v.conditions],
                    "missing_facts": v.missing_facts, "flags": v.flags, "retries": v.retries,
                    "votes": [{k: x.get(k) for k in ("model", "answer", "valid", "drop_reason")} for x in v.votes],
                    "quotes": [{"ref": q.get("ref"), "text": (q.get("text") or "")[:200]} for q in v.quotes],
                    "models": [{"model": a.model, "answer": a.answer, "value": a.value, "reason": (a.reason or "")[:1500],
                                "missing_facts": a.missing_facts, "evidence_gaps": getattr(a, "evidence_gaps", []),
                                "quotes": [{"ref": getattr(x, "ref", None) or (x.get("ref") if isinstance(x, dict) else None),
                                            "text": (getattr(x, "text", None) or (x.get("text") if isinstance(x, dict) else "")
                                                     or "")[:300]} for x in a.quotes],
                                "conditions": [getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else str(c))
                                               for c in a.conditions]}
                               for a in answers.get(qid) or []],
                    "judge_trace": jt.get(qid, []),
                    "evidence": [{"source": i.source, "law": i.law, "key": i.key, "title": i.title,
                                  "chars": len(i.text or ""), "note": "; ".join(i.problems or []),
                                  "excerpt": bool(getattr(i, "excerpt", False)),
                                  "text": (i.text or "")[:EXCERPT_CHARS]}
                                 for i in (p.items if p is not None else [])],
                    "evidence_truncated": bool(p and p.truncated),
                    "note": "; ".join(v.retry_hint) if v.status == "not_applicable" and v.retry_hint else "",
                    "pack_hash": v.pack_hash})
    return out


# ── 결과 만들기 ──────────────────────────────────────────────

def summary_preliminary(state: AgentState, eff: dict) -> dict:
    """route_agent 결과의 'preliminary' 자리에 넣을 요약 (추천 카드·정보 요청이 읽는 칸만, 판정표가 아니라 판단에서)."""
    vals = eff.get("values") or {}
    st = "designated" if vals.get("waste_status") in ("designated", "designated_suspect") else "general"
    return {"status": "law_agent", "waste_status": st,
            "classification": {"normalized_waste_code": vals.get("waste_code"),
                               "confirmed_by": (eff.get("confirmed") or {}).get("waste_code")},
            "from": "law_agent 판단 (A-1·A-2)"}


def match_partners(composed: Composed, route_dicts: list[dict], wcase: Any, partner_data: Any, factors: Any,
                   top_n: int, reserve_n: int = 5) -> None:
    from law_agent.core.carbon import attach_carbon
    from law_agent.core.partners.matcher import match_routes
    from law_agent.core.partners import geocode, road
    road.reset_stats()
    geocode.reset_stats()
    matches = match_routes(composed.routes, wcase, None, partner_data, top_n + reserve_n)
    rs = dict(road.STATS)
    if rs["calls"]:
        print(f"  카카오 도로 거리: {rs['ok']}/{rs['calls']}곳 조회 ({rs['seconds']}초)"
              + (f" · 중단: {rs['stopped']}" if rs["stopped"] else ""), flush=True)
    gs = dict(geocode.STATS)
    if gs["calls"]:
        print(f"  카카오 주소 → 좌표: {gs['ok']}곳 찾음 · {gs['failed']}곳 못 찾음 (중심점 사용) · {gs['seconds']}초"
              + (f" (업체 이름으로 {gs['by_name']}곳)" if gs.get("by_name") else "")
              + (f" · 중단: {gs['stopped']}" if gs["stopped"] else ""), flush=True)
        try:
            import os
            from pathlib import Path
            root = Path(os.environ.get("LAW_AGENT_DATA_DIR") or Path(os.environ.get("ROOT") or Path.cwd()) / "data" / "law_agent")
            n = geocode.save_missed(root / "geocode_missed.csv", getattr(wcase, "case_id", "") or "")
            if n:
                print(f"  못 찾은 주소 {n}건 → {root / 'geocode_missed.csv'} (주소를 고쳐 업체 표에 반영하세요)", flush=True)
        except OSError:
            pass
    for d, m in zip(route_dicts, matches):
        md = m.to_dict()
        for s in md["steps"]:
            s["reserve"] = s["candidates"][top_n:]
            s["candidates"] = s["candidates"][:top_n]
        d["partners"] = {"secured": md["secured"], "steps": md["steps"],
                         "road": {**rs, "source": "카카오모빌리티 자동차 길찾기" if rs["ok"] else "직선 × 1.3 추정"}}
        if factors is not None:
            attach_carbon(d, wcase, factors)


def to_route_proposals(result: dict, raw_case: dict, wcase: Any, vs: dict[str, Verdict],
                       used: list[list[str]]) -> list[dict]:
    monthly = [v for v in ((wcase.quantity or {}).get("monthly_ton") or []) if isinstance(v, (int, float))]
    ton = round(mean(monthly), 3) if monthly else None
    ton_txt = f"월 {ton}톤" if ton else "발생량(미확인)"
    waste_name = raw_case.get("name") or ""
    proposals = []
    for r, rid, u in zip(result["routes"], assign_route_ids(result["case_id"], result["routes"]), used):
        p = r.get("partners") or {}
        steps = p.get("steps") or []
        opt = {s["step_order"]: s.get("optional", False) for s in steps}
        main = _main_step(steps) if steps else None
        main_cands = [_partner(c, i + 1, main["step_order"]) for i, c in enumerate(main["candidates"])] if main else []
        reserve = ([_partner(c, len(main_cands) + i + 1, main["step_order"]) for i, c in enumerate(main.get("reserve") or [])]
                   if main else [])
        other = [_partner(c, i + 1, s["step_order"]) for s in steps if s is not main for i, c in enumerate(s["candidates"])]
        checks, why = build_checks(r, main_cands)
        ev = evidences(r, u, vs)
        codes_txt = ", ".join(r.get("waste_codes") or [])
        reqs = [t for t in r.get("required_tests") or [] if t.get("test") == "에너지 회수기준 확인"]
        proposals.append({
            "schema_version": SCHEMA_VERSION,
            "case_id": result["case_id"], "case_version": result["case_version"],
            "route_id": rid, "route_version": 1, "stale": False, "synthetic": bool(raw_case.get("synthetic", False)),
            "gate": r["gate"], "waste_status": r["waste_status"], "tier": r.get("tier"),
            "route_type": r.get("route_type"), "title": r["title"],
            "steps": [{"order": s["order"], "process": s["process"], "role": s["role"],
                       "treatment_codes": s.get("treatment_codes") or [], "r_codes": s.get("r_codes") or [],
                       "optional": opt.get(s["order"], False),
                       "treatment_code": (s.get("treatment_codes") or [None])[0],
                       "r_code": (s.get("r_codes") or [None])[0]} for s in r["steps"]],
            "checks": checks, "check_reasons": why,
            "secured": p.get("secured"),
            "main_step_order": main["step_order"] if main else None,
            "partner_candidates": main_cands + other,
            "reserve_candidates": reserve,
            "excluded_partners": [],
            "waste_ton_month": ton,
            "questions": build_questions(main, steps, f"{codes_txt} {waste_name}".strip(), ton_txt) if main else [],
            "conditions": r.get("conditions") or [],
            "required_tests": [t for t in r.get("required_tests") or [] if t not in reqs],
            "requirements": reqs,
            "alternatives": None,
            "restrictions": restrictions(vs),
            "evidences": ev + _partner_evidences(main_cands),
            "related_laws": [{"law": e["law"], "article": e["article"], "ref": e["ref"], "used_for": e["used_for"]}
                             for e in ev],
            "document_triggers": document_triggers(r, vs, ton, any(c.get("permit_type") == "신고" for c in main_cands)),
            "comparison": _comparison(r, main_cands),
            "rejected_summary": (main or {}).get("rejected_summary") or {},
            "applied_feedback_ids": [],
            # ── law_agent 가 더한 칸 ──
            "engine": ENGINE,
            "law_questions": u,
        })
    return proposals


def information_request(result: dict, proposals: list[dict], vs: dict[str, Verdict], qtext: dict[str, str]) -> dict:
    """경로 질문 + 경로에 안 붙은 판단의 빠진 사실(예: 관문 2를 못 만든 D-1, 신고 판단 H-1)."""
    ir = build_information_request(result, proposals)
    have = {r["field"] for r in ir["requests"]}
    for qid, v in vs.items():
        if v.status != "needs_info":
            continue
        for m in v.missing_facts or ["관련 사실"]:
            field = f"{qid}.{m}"
            if field in have:
                continue
            have.add(field)
            ir["requests"].append({"request_id": f"IR-{len(ir['requests']) + 1}", "field": field,
                                   "text": f"{qtext.get(qid, qid)[:60]} — 필요한 사실: {m}", "to": "배출업체",
                                   "affected_routes": []})
            ir["fields"].append(field)
    return merge_same_facts(ir)


RX_Q_FIELD = re.compile(r"^([A-J]-\d+(?:\.c\d+)?)\.(.+)$")


def merge_same_facts(ir: dict) -> dict:
    """같은 사실을 여러 질문이 물으면 하나로 (2026.11.17 데모 2: '할로겐족 유기용제 함유 여부'를 A-1·F-1·F-2 가 따로).
    필드 '질문id.사실' 의 사실 이름(공백 무시)이 같으면 합치고, 어느 질문이 필요로 하는지 question_ids 에 남긴다."""
    out, seen = [], {}
    for r in ir.get("requests") or []:
        m = RX_Q_FIELD.match(r.get("field") or "")
        if not m or r.get("to") not in (None, "배출업체"):
            out.append(r)
            continue
        qid, fact = m.group(1), m.group(2)
        k = re.sub(r"\s+", "", fact)
        if k in seen:
            first = seen[k]
            first.setdefault("question_ids", [])
            if qid not in first["question_ids"]:
                first["question_ids"].append(qid)
            first["affected_routes"] = sorted(set(first.get("affected_routes") or []) | set(r.get("affected_routes") or []))
            continue
        r = {**r, "question_ids": [qid]}
        seen[k] = r
        out.append(r)
    for r in out:
        if len(r.get("question_ids") or []) > 1:
            fact = RX_Q_FIELD.match(r["field"]).group(2)
            r["text"] = f"{fact} — 판단 {', '.join(r['question_ids'])}에 필요합니다."
    for i, r in enumerate(out, 1):
        r["request_id"] = f"IR-{i}"
    return {**ir, "requests": out, "fields": [r["field"] for r in out]}
