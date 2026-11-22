# route_agent/src/doc_review/__init__.py 에서 복사 (2026.11.19). 바꾼 점: DOC_TYPES 에 서류 3종 추가 (판정 방식은 그대로)
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""서류 검토 — 파트 3이 만든 서류 초안을 법 필수 항목·경로와 대조한다.

입력:  DocumentReviewRequest (파트 3 → 파트 2), 그 경로의 RouteProposal, 파트 1 사례(WasteCase 원본 dict)
출력:  DocumentReviewResult (파트 2 → 파트 3)
판정은 doc_requirements.csv 규칙으로만 한다 (LLM 없음). 문서 안의 글은 데이터로만 다룬다.

    from doc_review import review, load_requirements
    result = review(request, proposal, case, load_requirements(tables_dir))
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

SCHEMA_VERSION = "1.0"
DOC_TYPES = {
    "entrust_contract": "위탁계약서",
    "capacity_check": "수탁처리능력 확인서 (별지 제5호서식)",
    "treatment_plan": "폐기물 처리계획서 (별지 제12호서식)",
    "emitter_report": "사업장폐기물배출자 신고서 (별지 제6호서식)",           # 2026.11.19
    "acceptance_check": "폐기물 수탁확인서 (별지 제5호서식, 지정폐기물 처리계획용)",  # 2026.11.19
    "hazard_info": "폐기물 유해성 정보자료 (별지 제14호의4서식)",            # 2026.11.19
}
TABLE = "doc_requirements.csv"
APPROX_TOL = 0.3
_EMPTY = (None, "", [], {})


# ── 표 읽기 ──────────────────────────────────────────────────────────
def load_requirements(tables_dir: str | Path | None = None) -> list[dict]:
    """tables_dir/doc_requirements.csv, 없으면 이 폴더의 초안"""
    p = Path(tables_dir) / TABLE if tables_dir else None
    if not p or not p.exists():
        p = Path(__file__).with_name(TABLE)
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ── 값 꺼내기 ────────────────────────────────────────────────────────
def values(doc: dict, path: str) -> list[tuple[str, Any]]:
    """"a.b" → [("a.b", 값)], "items[].x" → [("items[0].x", 값), …] (목록이 비면 [("items[]", None)])"""
    out = [("", doc)]
    for part in path.split("."):
        nxt = []
        for loc, cur in out:
            is_list = part.endswith("[]")
            name = part[:-2] if is_list else part
            v = cur.get(name) if isinstance(cur, dict) else None
            here = f"{loc}.{name}" if loc else name
            if is_list:
                if not isinstance(v, list) or not v:
                    nxt.append((f"{here}[]", None))
                else:
                    nxt.extend((f"{here}[{i}]", x) for i, x in enumerate(v))
            else:
                nxt.append((here, v))
        out = nxt
    return out


def _at(doc: dict, loc: str) -> Any:
    """values() 가 돌려준 위치("items[0].x")의 값"""
    cur: Any = doc
    for part in loc.split("."):
        m = re.match(r"^(.*?)\[(\d+)\]$", part)
        name, idx = (m.group(1), int(m.group(2))) if m else (part, None)
        cur = cur.get(name) if isinstance(cur, dict) else None
        if idx is not None:
            cur = cur[idx] if isinstance(cur, list) and idx < len(cur) else None
    return cur


def _item_prefix(loc: str) -> str | None:
    m = re.match(r"^(.*?\[\d+\])", loc)
    return m.group(1) if m else None


def _when_ok(doc: dict, when: str, loc: str | None) -> bool:
    """when "a[].b==값" — 같은 목록 항목 기준으로 비교 (loc 이 그 항목 안이면 그 항목만)"""
    if not when:
        return True
    m = re.match(r"^(.+?)(==|!=)(.*)$", when)
    if not m:
        return True
    path, op, want = m.groups()
    got = values(doc, path)
    pre = _item_prefix(loc or "")
    if pre:
        got = [(l, v) for l, v in got if l.startswith(pre)] or got
    hit = any(str(v).strip() == want for _, v in got)
    return hit if op == "==" else not hit


def _num(v: Any) -> float | None:
    """'1.2', '1,200', '1톤', '월 1.2 t', '약 120원/kg' → 숫자 (2026.11.20: 단위·말이 붙어도 첫 숫자를 읽음).
    숫자가 둘 이상이면(범위 '1~2톤' 등) 판단하지 않는다 (None)"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        pass
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    return float(nums[0]) if len(nums) == 1 else None


def _date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{4})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/])\s*(\d{1,2})\s*(?:일|\.)?", s)
    if m:                                                # 2026-12-01 · 2026.12.1. · 2026년 12월 1일 · 2026/12/01
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _enum(v: Any, allowed: list[str]) -> str | None:
    """'가능합니다' → '가능', '불가능해요' → '불가능' (허용값 중 글에 든 가장 긴 것). 없으면 None"""
    s = re.sub(r"[\s\"'“”‘’.,!]", "", str(v or ""))
    if s in allowed:
        return s
    def neg(a: str) -> bool:                             # '불가능'·'미해당'·'비대상' 안의 '가능'·'해당'·'대상'은 맞은 게 아님
        return any(p + a in s for p in ("불", "미", "비", "안", "못")) and not any(p + a in allowed for p in ("불", "미", "비"))
    hit = sorted((a for a in allowed if a and a in s and not neg(a)), key=len, reverse=True)
    if not hit:
        return None
    best = hit[0]
    others = [a for a in hit[1:] if a not in best]       # '가능'은 '불가능' 안에 있으니 겹침이 아님
    return None if others else best


def _norm_name(s: Any) -> str:
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s", "", str(s or ""))
    return s.lower()


def _norm_code(s: Any) -> str:
    return re.sub(r"\s", "", str(s or ""))


# ── 경로·사례에서 비교값 ─────────────────────────────────────────────
def context(proposal: dict, case: dict) -> dict:
    cls = case.get("classification") or {}
    codes = {_norm_code(c) for c in [cls.get("selected_code"), cls.get("reported_code")] if c}
    codes |= {_norm_code(c.get("waste_code")) for c in case.get("components") or [] if c.get("waste_code")}
    tcodes = {_norm_code(t) for s in proposal.get("steps") or [] for t in s.get("treatment_codes") or []}
    partners = (proposal.get("partner_candidates") or []) + (proposal.get("reserve_candidates") or [])
    months = ((case.get("quantity") or {}).get("monthly_ton")) or []
    try:
        mon = mean(float(x) for x in months) if months else None
    except (TypeError, ValueError):
        mon = None
    plan = next((t.get("plan_check") for t in proposal.get("document_triggers") or [] if t.get("plan_check")), None)
    return {
        "waste_codes": codes,
        "treatment_codes": tcodes,
        "partner_names": {_norm_name(p.get("name")) for p in partners if p.get("name")},
        "partner_ids": {str(p.get("company_id")) for p in partners if p.get("company_id")},
        "ton_year": round(mon * 12, 3) if mon is not None else None,
        "kg_month": round(mon * 1000, 1) if mon is not None else None,
        "waste_status": proposal.get("waste_status"),
        "plan_check": plan,
    }


# ── 판정 ─────────────────────────────────────────────────────────────
def _finding(row: dict, problem: str, field: str | None, found: Any = None, expected: Any = None,
             message: str | None = None) -> dict:
    return {
        "req_id": row["req_id"], "severity": row["severity"], "item": row["item"],
        "field": field, "problem": problem,
        "found": found, "expected": expected,
        "message": message or row["message"],
        "basis": row["basis_key"], "quote": row["quote"],
        "basis_status": "confirmed" if row.get("status") == "confirmed" else "draft",
    }


def _check(row: dict, doc: dict, ctx: dict) -> list[dict]:
    chk, field, param, when = row["check"], row["field"], row["param"], row["when"]
    out: list[dict] = []

    if chk == "info":
        return [_finding(row, "info", None)] if _when_ok(doc, when, None) else []

    if chk == "attachment":
        if not _when_ok(doc, when, None):
            return []
        att = {str(a.get("type") if isinstance(a, dict) else a) for a in doc.get("attachments") or []}
        return [] if param in att else [_finding(row, "missing", "attachments", sorted(att), param)]

    if chk == "date_order":
        a, b = param.split("|")
        da, db = _date(values(doc, a)[0][1]), _date(values(doc, b)[0][1])
        if da and db and db < da:
            return [_finding(row, "invalid", b, str(db), f">= {da}")]
        return []

    if chk == "deadline":
        a, b, days = param.split("|")
        da, db = _date(values(doc, a)[0][1]), _date(values(doc, b)[0][1])
        if not da or not db:
            return [_finding(row, "missing", f"{a}|{b}", None, "두 날짜",
                             f"{row['message']} (기한을 확인하려면 {a}, {b} 날짜가 필요합니다)")]
        if (db - da).days > int(days):
            return [_finding(row, "invalid", b, str(db), f"<= {da} + {days}일")]
        return []

    for loc, v in values(doc, field):
        if not _when_ok(doc, when, loc):
            continue
        if chk == "required":
            if v in _EMPTY or (isinstance(v, str) and not v.strip()):
                out.append(_finding(row, "missing", loc))
        elif chk == "number":
            n = _num(v)
            if n is None or n <= 0:
                out.append(_finding(row, "missing" if v in _EMPTY else "invalid", loc, v, "> 0"))
        elif chk == "enum":
            allowed = param.split("|")
            if v in _EMPTY:
                out.append(_finding(row, "missing", loc, None, allowed))
            elif _enum(v, allowed) is None:
                out.append(_finding(row, "invalid", loc, v, allowed))
        elif chk == "in_route":
            ref = ctx.get(param) or set()
            if v not in _EMPTY and ref and _norm_code(v) not in ref:
                out.append(_finding(row, "mismatch", loc, v, sorted(ref)))
        elif chk == "partner":
            if v in _EMPTY or not (ctx["partner_names"] or ctx["partner_ids"]):
                continue
            owner = _at(doc, loc.rsplit(".", 1)[0]) if "." in loc else doc     # 같은 칸 묶음의 company_id
            cid = owner.get("company_id") if isinstance(owner, dict) else None
            if _norm_name(v) not in ctx["partner_names"] and str(cid) not in ctx["partner_ids"]:
                out.append(_finding(row, "mismatch", loc, v, "추천·예비 업체"))
        elif chk == "approx":
            ref, n = ctx.get(param), _num(v)
            if ref and n and abs(n - ref) / ref > APPROX_TOL:
                out.append(_finding(row, "mismatch", loc, n, ref,
                                    f"{row['message']} (서류 {n:g}, 사례 {ref:g})"))
    return out


def _applicability(doc_type: str, ctx: dict) -> tuple[str | None, dict | None]:
    """처리계획서: 경로의 plan_check 결과로 대상 여부 확인"""
    if doc_type != "treatment_plan":
        return None, None
    pc = ctx.get("plan_check") or {}
    dec = pc.get("decision")
    if ctx.get("waste_status") != "designated":
        return "not_applicable", {"message": "지정폐기물 경로가 아니라 처리계획 확인 대상이 아닙니다"}
    if dec == "not_required":
        return "not_applicable", {"message": f"처리계획 확인 대상이 아닙니다: {pc.get('reason', '')}".strip()}
    if dec in (None, "needs_info"):
        return "needs_info", {"message": "처리계획 확인 대상인지 아직 확인되지 않았습니다 (경로의 plan_check 확인 필요)"}
    return None, None


def review(request: dict, proposal: dict, case: dict, rows: list[dict]) -> dict:
    doc_type = request.get("doc_type")
    base = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request.get("request_id"),
        "case_id": request.get("case_id") or proposal.get("case_id"),
        "case_version": proposal.get("case_version"),
        "route_id": request.get("route_id") or proposal.get("route_id"),
        "doc_type": doc_type, "doc_name": DOC_TYPES.get(doc_type),
        "doc_version": request.get("doc_version"),
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
    }
    if doc_type not in DOC_TYPES:
        return {**base, "verdict": "rejected_request", "summary": {},
                "findings": [], "notes": [f"지원하지 않는 doc_type: {doc_type} (가능: {', '.join(DOC_TYPES)})"]}
    if request.get("route_id") and proposal.get("route_id") and request["route_id"] != proposal["route_id"]:
        return {**base, "verdict": "rejected_request", "summary": {}, "findings": [],
                "notes": ["요청의 route_id와 RouteProposal의 route_id가 다릅니다"]}

    ctx = context(proposal, case)
    notes = []
    if proposal.get("stale"):
        notes.append("이 RouteProposal은 사례가 바뀌어 오래된(stale) 판입니다. 최신 경로로 다시 검토하세요")
    app, app_note = _applicability(doc_type, ctx)
    if app_note:
        notes.append(app_note["message"])

    doc = request.get("fields") or {}
    doc = {**doc, "attachments": request.get("attachments") or []}
    findings = [f for r in rows if r["doc_type"] == doc_type for f in _check(r, doc, ctx)]
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["req_id"], f["field"] or ""))
    summary = {s: sum(f["severity"] == s for f in findings) for s in ("error", "warning", "info")}

    if app == "not_applicable":
        verdict = "not_applicable"
    elif summary["error"]:
        verdict = "fix_required"
    elif app == "needs_info" or summary["warning"]:
        verdict = "check_required"
    else:
        verdict = "pass"
    if any(f["basis_status"] == "draft" for f in findings) or any(r.get("status") != "confirmed"
                                                                  for r in rows if r["doc_type"] == doc_type):
        notes.append("검토 기준표가 초안(draft)입니다. 기준표 확정 전까지 결과는 참고용입니다")
    return {**base, "verdict": verdict, "summary": summary, "findings": findings, "notes": notes}
