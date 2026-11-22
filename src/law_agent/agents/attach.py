"""원문 첨부 노드 — 설명형 질문(checklist attach: true)은 모델을 부르지 않고 근거 원문을 답으로 붙인다.

D-3 재활용 기준, D-4 업체 준수사항, G-1 위탁 기준, G-3 운반 기준, H-3 제출 서류 같은 질문은
'판단' 이 아니라 '기준이 무엇인가' 를 정리하는 것이고 경로 상태를 바꾸지 않는다 (route_agent 도 원문만 붙임).
→ evidence 가 만든 근거 묶음에서 지정 조문(min_laws) 조각을 골라 그대로 붙인다. 인용은 원문 그대로라 검증이 필요 없다.
근거 묶음이 비면 붙이지 않고 모델로 넘긴다 (judge 가 기존대로).
지정 조문이 묶음에 없으면 다른 조문을 대신 붙이지 않는다 → needs_human + evidence_gap
(2026.11.20 세척액 D-3: 별표 5의3 이 빠지자 검색에 걸린 제13조의2 가 '재활용 기준'으로 붙었음).
"""
from __future__ import annotations

import time
from typing import Any

from law_agent.agents.schedule import ready_questions
from law_agent.state import FLAG_EVIDENCE_GAP, AgentState, Verdict

FLAG_ATTACH = "attached_text"
TEXT_CHARS = 400          # value.text (경로 조건 문장) 길이
QUOTE_CHARS = 300         # 인용 하나 길이
STANDARD_CHARS = 8000     # 기준 원문 (카드의 기준 한 줄·원문 보기용, 판단에는 안 씀)


def _items(pack, candidates) -> list:
    mins = {(c.law, c.key) for c in candidates or [] if c.source == "min_laws"}
    if not mins:                                      # 지정 조문이 없는 질문만 묶음 앞 2개
        return list(pack.items[:2])
    bases = {(l, k.split(":")[0]) for l, k in mins}
    return [it for it in pack.items if (it.law, it.key) in mins or (it.law, it.key.split(":")[0]) in bases]


def attach_verdict(q, pack, candidates) -> Verdict | None:
    if pack is None or not pack.items:
        return None
    items = _items(pack, candidates)
    if not items:
        want = ", ".join(sorted({f"{c.law}:{c.key}" for c in candidates or [] if c.source == "min_laws"}))
        return Verdict(question_id=q.id, status="needs_human", answer="unknown",
                       value={"text": f"정해진 원문({want})을 근거에서 찾지 못해 붙이지 않았습니다. 사람이 확인해 주세요."},
                       flags=[FLAG_ATTACH, FLAG_EVIDENCE_GAP], pack_hash=pack.pack_hash)
    quotes = [{"ref": it.ref, "text": it.text[:QUOTE_CHARS]} for it in items]
    text = " / ".join(f"{it.title}: {' '.join(it.text.split())[:TEXT_CHARS]}" for it in items)[:TEXT_CHARS * 2]
    # 자르지 않은 기준 원문 — 카드가 기준마다 한 줄 요약·원문을 보여 준다 (2026.11.20). 원문 그대로라 검증 대상 아님
    standards = [{"ref": it.ref, "title": it.title, "text": it.text[:STANDARD_CHARS]} for it in items]
    return Verdict(question_id=q.id, status="agreed", answer="yes", value={"text": text, "standards": standards},
                   quotes=quotes,
                   flags=[FLAG_ATTACH], pack_hash=pack.pack_hash)


def node(state: AgentState) -> dict:
    t0 = time.time()
    packs, cands = state.get("packs") or {}, state.get("candidates") or {}
    out: dict[str, Verdict] = {}
    for q in ready_questions(state):
        if not q.attach:
            continue
        v = attach_verdict(q, packs.get(q.id), cands.get(q.id))
        if v is not None:
            out[q.id] = v
    upd: dict[str, Any] = {"audit": [{"node": "attach", "seconds": round(time.time() - t0, 3),
                                      "summary": {"attached": sorted(out)}}]}
    if out:
        upd["verdicts"] = out
    return upd
