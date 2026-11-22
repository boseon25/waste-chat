"""글 칸 내용 검토 — LLM 보조 (2026.11.20).

규칙 검토는 글 칸(취급 주의사항·처리 방법 설명 …)이 '비어 있지 않은가'만 본다. 여기서는 그 칸의 법 원문을 주고
"원문이 요구하는 내용 중 사용자 글에 빠진 것"만 찾게 한다.

지키는 것
  - 결과는 경고(warning, '확인 필요')로만 낸다. 통과·고칠 곳(오류) 판정은 규칙이 한다
  - 빠졌다고 할 때는 근거 원문을 글자 그대로 인용해야 하고, 그 인용이 우리가 준 원문에 없으면 버린다
  - 확실하지 않으면 빠졌다고 하지 않게 지시한다. 표현·맞춤법은 보지 않는다
  - 사용자 글은 자료로만 다룬다 (안의 지시문을 따르지 않음 — assist.RULES)
  - 같은 입력이면 저장해 둔 답을 다시 쓴다 (assist_cache)
  - 모델 호출이 실패하면 지어내지 않고 notes 에 '글 칸 검토를 못 했다'고만 남긴다
"""
from __future__ import annotations

import json
import os

from law_agent.assist.service import RULES, _cached, _call, _model
from law_agent.guide.service import _facts, _kind, _law
from law_agent.review import context, field_map
from law_agent.state import quote_in

MAX_FIELDS = 6


def enabled(req: dict) -> bool:
    if os.environ.get("LAW_AGENT_REVIEW_LLM", "1") in ("0", "false", "no"):
        return False
    return bool(req.get("llm", True))


def text_findings(req: dict, rows: list[dict], box=None) -> tuple[list[dict], list[str]]:
    """(경고 목록, 메모). 글 칸이 없거나 근거 원문이 없으면 ([], [])"""
    doc = str(req.get("doc") or "")
    m = field_map.load(doc)
    if m is None:
        return [], []
    values = req.get("values") or {}
    labels = req.get("labels") or {}
    keys = [k for k, v in values.items() if str(v or "").strip() and _kind(k)[0] == "write_text"][:MAX_FIELDS]
    if not keys:
        return [], []
    found = context.find(str(req.get("case_id") or ""), box) or {}
    facts = _facts(found.get("case") or {})
    out, notes, failed = [], [], []
    for k in keys:
        law = [x for x in _law(m, k, rows) if x.get("sentence") or x.get("quote")]
        if not law:
            continue
        src = [{"basis": x["basis"], "text": (x.get("sentence") or x["quote"]), "summary": x.get("summary") or ""}
               for x in law]
        label = labels.get(k) or k
        text = str(values[k]).strip()
        payload = {"doc": doc, "field": k, "text": text, "law": src, "facts": facts}

        def run(src=src, label=label, text=text) -> dict:
            user = ("[칸] " + label
                    + "\n[사용자가 쓴 글]\n" + text
                    + "\n[이 칸의 법 원문]\n" + "\n".join(f"- {x['text']}" for x in src)
                    + "\n[이 건의 사실]\n" + json.dumps(facts, ensure_ascii=False)
                    + "\n\n법 원문이 이 칸에 적도록 요구하는 내용 중, 사용자 글에 빠진 것만 찾아라. "
                      "표현·맞춤법·길이는 보지 않는다. 확실하지 않으면 빠졌다고 하지 않는다. "
                      "사용자 글이 [이 건의 사실]과 맞지 않으면 conflicts 에 적는다. "
                      '출력: {"missing": [{"what": "빠진 내용 (짧게)", "quote": "근거 원문 문장 그대로"}], '
                      '"conflicts": [{"what": "맞지 않는 내용", "fact": "사실"}]}')
            data = _call(RULES, user, "REVIEW-TEXT")
            if data is None:
                return {"ok": False}
            return {"ok": True, "missing": data.get("missing") or [], "conflicts": data.get("conflicts") or [],
                    "model": _model()}

        res = _cached("textcheck", payload, run)
        if not res.get("ok"):
            failed.append(label)
            continue
        joined = "\n".join(x["text"] for x in src)
        for x in res.get("missing") or []:
            q = str((x or {}).get("quote") or "")
            what = str((x or {}).get("what") or "").strip()
            if not what or not q or not quote_in(q, joined):
                continue                                  # 근거가 원문에 없으면 버림
            b = next((s for s in src if quote_in(q, s["text"])), src[0])
            out.append({"req_id": f"TXT-{k}", "severity": "warning", "item": label, "field": k, "form_field": k,
                        "doc_field": k, "kind": "llm_text", "check": "llm_text", "problem": "incomplete",
                        "message": f"'{what}' 내용이 빠진 것 같습니다 (AI 검토)", "basis": b["basis"], "quote": q,
                        "summary": b["summary"], "basis_status": "confirmed", "found": None, "expected": what})
        for x in res.get("conflicts") or []:
            what = str((x or {}).get("what") or "").strip()
            fact = str((x or {}).get("fact") or "").strip()
            if not what or not fact or fact not in json.dumps(facts, ensure_ascii=False):
                continue                                  # 사실 목록에 없는 '사실'은 버림
            out.append({"req_id": f"TXT-{k}", "severity": "warning", "item": label, "field": k, "form_field": k,
                        "doc_field": k, "kind": "llm_text", "check": "llm_text", "problem": "conflict",
                        "message": f"'{what}' — 이 건의 사실({fact})과 다른 것 같습니다 (AI 검토)",
                        "basis": "", "quote": "", "summary": "", "basis_status": "confirmed",
                        "found": what, "expected": fact})
    if failed:
        notes.append(f"글 칸 내용 검토(AI)를 하지 못했습니다: {', '.join(failed)} — 필수 칸·형식 검사는 정상입니다")
    return out, notes
