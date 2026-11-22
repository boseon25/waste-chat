"""지정폐기물 처리계획 확인 대상 판정 (시행규칙 제18조의2).

표 plan_check_targets.csv 는 law_api/build_tables.py 가 만든다
  (원문 data/law/watched → staged → diff → 사람 승인 → promote → data/tables/active).
여기서는 active 표를 읽어 판정만 한다.

  judge(codes, monthly_kg, rows) → {"decision": required | not_required | needs_info, "decision_label": 필요 | 불필요 | 확인 필요, ...}
  confirmed 행만 판정에 쓴다. draft 행으로 나온 결과는 decision="needs_info", draft_decision에 계산값을 둔다.

판정하지 않는 것 (사람 확인): 제1항 괄호의 중간가공 폐기물 배출자 제외, 제2항 단서(제21조제1항 각 호의 자),
  제3의2호 슬레이트 철거 도급 특례, 제9호 고시 기준량.
"""
from __future__ import annotations

import csv
from pathlib import Path

FILE = "plan_check_targets.csv"
LABEL = {"required": "필요", "not_required": "불필요", "needs_info": "확인 필요"}


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------
def load(tables_dir: str | Path) -> list[dict]:
    path = Path(tables_dir) / FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _match(code: str, rows: list[dict]) -> dict | None:
    best = None
    for r in rows:
        for p in (r["분류번호_앞자리"] or "").split(";"):
            if p and (code == p or code.startswith(p + "-")) and (best is None or len(p) > best[0]):
                best = (len(p), r)
    return best[1] if best else None


def judge(codes: list[str], monthly_kg: float | None, rows: list[dict]) -> dict:
    """분류번호들과 월 평균 배출량(kg)으로 처리계획 확인 대상인지.
    한 case의 물량은 합계 하나라서, 같은 호에 분류번호가 여럿이면 품목별 물량을 알 수 없다 → 애매하면 확인 필요."""
    targets = [r for r in rows if r["구분"] == "대상"]
    proc = {r["항"]: r for r in rows if r["구분"] == "절차"}
    if not targets:
        return {"decision": "needs_info", "decision_label": LABEL["needs_info"], "reason": f"{FILE} 없음",
                "matched": [], "basis": []}
    matched = [(c, _match(c, targets)) for c in codes]
    hits = [(c, r) for c, r in matched if r]
    out = {"matched": [{"code": c, "item_no": r["호"], "target": r["대상"], "row_id": r["행ID"]} for c, r in hits],
           "basis": sorted({r["근거키"] for _, r in hits}),
           "documents": (proc.get("4") or {}).get("내용"), "deadline": (proc.get("5") or {}).get("내용")}
    if not hits:
        dec, why = "not_required", "제18조의2 제1항 각 호에 해당하는 분류번호 없음"
        draft = any(r["상태"] != "confirmed" for r in targets)
    else:
        dec, why = _decide(hits, monthly_kg)
        draft = any(r["상태"] != "confirmed" for _, r in hits)
    if draft:
        return {**out, "decision": "needs_info", "decision_label": LABEL["needs_info"], "draft_decision": dec,
                "reason": f"{why} (표가 아직 draft — 원문 대조 후 confirmed)"}
    return {**out, "decision": dec, "decision_label": LABEL[dec], "reason": why}


def _decide(hits: list[tuple[str, dict]], kg: float | None) -> tuple[str, str]:
    by_ho: dict[str, list[dict]] = {}
    for _, r in hits:
        by_ho.setdefault(r["호"], []).append(r)
    unknown = []
    for ho, rs in by_ho.items():
        each = rs[0]["각각_kg_월"]
        if each == "고시":
            unknown.append(f"제{ho}호 기준량은 고시 확인 필요")
            continue
        each = float(each or 0)
        total = float(rs[0]["합계_kg_월"]) if rs[0]["합계_kg_월"] else None
        if each == 0:
            return "required", f"제{ho}호 {rs[0]['대상']}: 양과 관계없이 대상"
        if kg is None:
            unknown.append(f"제{ho}호 기준 월 평균 {each:g}kg" + (f"(합계 {total:g}kg)" if total else "") + " — 배출량 모름")
            continue
        names = {r["대상"] for r in rs}
        if len(names) == 1 and kg >= each:
            return "required", f"제{ho}호 {rs[0]['대상']}: 월 평균 {kg:g}kg ≥ {each:g}kg"
        if total is not None and kg >= total:
            return "required", f"제{ho}호: 품목 합계 월 평균 {kg:g}kg ≥ 합계 기준 {total:g}kg"
        if len(names) > 1 and kg >= each:
            unknown.append(f"제{ho}호: 품목 {len(names)}개 합계 {kg:g}kg, 품목별 양을 알아야 판단 (각각 {each:g}kg)")
    if unknown:
        return "needs_info", "; ".join(unknown)
    return "not_required", f"월 평균 {kg:g}kg가 해당 호 기준량 미만"
