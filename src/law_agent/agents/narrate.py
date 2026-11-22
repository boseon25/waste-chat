"""실시간 로그를 사람이 읽는 말로 (발표·사용자용 -v). 관리자용 숫자 로그는 graph._trace_line (-vv).

노드 이름 대신 하는 일, 질문 번호 대신 질문 문장, 결과는 질문마다 한 줄:
  ▶ 2번째 검토: 3가지 확인
     - 재활용이 금지된 폐기물인가? (B-1)
     법령 찾기: 폐기물관리법 제13조의2, 시행규칙 별표 5의3 …
     원문 첨부: 위탁 기준 (G-1) ← 폐기물관리법 시행규칙 별표 5
     AI 판단 (모델 2개): 2가지
     ✔ 재활용이 금지된 폐기물인가? → 아니오
     ? 재활용 금지 조건 확인 → 파트 1에 질문: PCB 농도
     ↻ 근거 부족 — 법령을 더 찾아 다시 판단
"""
from __future__ import annotations

from law_agent.core.lawstore import LAW_NAME
from law_agent.output.proposal import article_label

ANSWER_KO = {"yes": "예", "no": "아니오", "conditional": "조건부", "unknown": "모름"}
SHORT = 46


def _q(st: dict, qid: str) -> str:
    from law_agent.question_titles import title
    return title(qid)


def _law(law: str, key: str) -> str:
    return f"{LAW_NAME.get(law, law)} {article_label(key)}".strip()


def _answer(v) -> str:
    items = (v.value or {}).get("items") if isinstance(v.value, dict) else None
    if items:
        return ", ".join(map(str, items[:6])) + (" …" if len(items) > 6 else "")
    if isinstance(v.value, dict) and v.value.get("text") and v.answer in (None, "yes", "text"):
        txt = " ".join(str(v.value["text"]).split())
        return txt[:40] + ("…" if len(txt) > 40 else "")
    return ANSWER_KO.get(v.answer, str(v.answer))


def line(name: str, st: dict, upd: dict) -> str | None:
    """노드 하나가 끝났을 때 보여줄 글 (없으면 None)."""
    if name == "planner":
        qs = upd.get("questions") or st.get("questions") or []
        skipped = sum(1 for v in (upd.get("verdicts") or {}).values() if v.status == "not_applicable")
        return f"■ 법률 검토 시작: 확인할 질문 {len(qs) - skipped}가지 (이 사례와 상관없는 {skipped}가지는 건너뜀)"
    if name == "round_start":
        ids = upd.get("round_ids") or []
        body = "\n".join(f"     - {_q(st, i)} ({i})" for i in ids[:10])
        more = f"\n     … 외 {len(ids) - 10}가지" if len(ids) > 10 else ""
        return f"\n▶ {upd.get('round')}번째 검토: {len(ids)}가지 확인\n{body}{more}"
    if name == "table_answer":
        vs = upd.get("verdicts") or {}
        if not vs:
            return None
        return "\n".join(f"     ✔ 판정표로 답: {_q(st, k)} → {_answer(v)}" for k, v in vs.items())
    if name == "retriever":
        laws, seen = [], set()
        for cands in (upd.get("candidates") or {}).values():
            for c in cands:
                if c.source == "min_laws" and (c.law, c.key) not in seen:
                    seen.add((c.law, c.key))
                    laws.append(_law(c.law, c.key))
        n_all = sum(len(c) for c in (upd.get("candidates") or {}).values())
        head = ", ".join(laws[:4]) + (" …" if len(laws) > 4 else "")
        return f"     법령 찾기: {head or '검색'} (조문 {n_all}개)"
    if name == "attach":
        vs = upd.get("verdicts") or {}
        if not vs:
            return None
        out = []
        for k, v in vs.items():
            refs = sorted({q.get("ref", "").split("@")[0] for q in v.quotes or []})
            src = ", ".join(dict.fromkeys(_law(*r.split(":", 1)) for r in refs if ":" in r))   # 같은 별표의 조각 여럿 → 한 번만
            out.append(f"     원문 첨부: {_q(st, k)} ← {src or '법령 원문'}")
        return "\n".join(out)
    if name == "judge":
        n = len(upd.get("answers") or {})
        return f"     AI 판단 (서로 다른 회사 모델 2~3개): {n}가지" if n else None
    if name == "verifier":
        out = []
        for k, v in (upd.get("verdicts") or {}).items():
            if v.status == "agreed":
                out.append(f"     ✔ {_q(st, k)} → {_answer(v)}")
            elif v.status == "needs_info":
                facts = ", ".join(v.missing_facts[:3]) or "관련 사실"
                out.append(f"     ? {_q(st, k)} → 파트 1에 질문: {facts}")
            elif v.status == "retry":
                out.append(f"     ↻ {_q(st, k)} → 근거 부족·의견 갈림, 법령을 더 찾아 다시 판단")
            elif v.status == "needs_human":
                out.append(f"     ⚠ {_q(st, k)} → 사람 확인 필요")
        return "\n".join(out) if out else None
    return None
