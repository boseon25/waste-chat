"""에너지 회수기준 확인 (시행규칙 제3조 제1항) — 에너지 회수 경로(R-8·R-9)의 요건.

표 energy_recovery_criteria.csv 는 law_api/build_tables.py 가 만든다
  (원문 data/law/watched → staged → diff → 사람 승인 → promote → data/tables/active).

  requirements(route, case, rows) → 경로의 R코드에 걸리는 기준마다
    {"row_id", "item", "criterion", "value", "decision": met | not_met | needs_info, "decision_label": 충족 | 미충족 | 확인 필요,
     "reason", "to", "basis", "condition"}   (키·판정값은 영어, 사람이 읽는 글은 한국어)
  - 저위발열량은 파트 1 analysis.heating_value_kcal(kcal/kg)로 비교한다. 혼합 상태 값이면 확인 필요.
  - 회수효율·회수열 이용·고형연료 품질은 처리 시설 값이라 업체 확인(to = partner).
  - 제3호(시멘트 소성로 연료)는 지정폐기물이면 미충족.
  confirmed 행만 판정한다. draft 행은 decision="확인 필요", draft_decision에 계산값.
  경로의 가능·불가(regulatory)는 바꾸지 않는다. 결과는 requirements 칸과 질문으로만 나간다.
"""
from __future__ import annotations

import csv
from pathlib import Path

FILE = "energy_recovery_criteria.csv"
LABEL = {"met": "충족", "not_met": "미충족", "needs_info": "확인 필요"}


def load(tables_dir: str | Path) -> list[dict]:
    path = Path(tables_dir) / FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def route_rcodes(route: dict) -> set[str]:
    return {c for s in route.get("steps") or [] for c in (s.get("r_codes") or [])}


def _designated(route: dict, case) -> bool:
    if route.get("waste_status") == "designated":
        return True
    for c in route.get("waste_codes") or []:
        try:
            if int(c.split("-")[0]) < 50:
                return True
        except ValueError:
            pass
    return False


def _lhv(case) -> tuple[float | None, str | None]:
    a = getattr(case, "analysis", None) or {}
    v = a.get("heating_value_kcal")
    unit = (a.get("heating_value_unit") or "kcal/kg").replace(" ", "")
    if not isinstance(v, (int, float)):
        return None, None
    if unit not in ("kcal/kg", "kcal"):
        return None, f"발열량 단위가 {unit} — kcal/kg로 바꿔야 비교 가능"
    return float(v), None


def _mixed(case) -> bool:
    return bool(getattr(case, "is_mixed", None) or getattr(case, "mixed_with_other", None))


def _one(row: dict, route: dict, case) -> dict:
    item, cmp_, base = row["항목"], row["비교"], row["기준값"]
    crit = f"{item} {cmp_} {base}{row['단위']}".strip() if cmp_ in (">=", "<") else (row["조건"] or item)
    out = {"row_id": row["행ID"], "item": item, "criterion": crit, "value": None, "to": "partner",
           "basis": row["근거키"], "condition": row["조건"]}
    if item == "저위발열량":
        out["to"] = "emitter"
        v, err = _lhv(case)
        if err:
            return {**out, "decision": "needs_info", "reason": err}
        if v is None:
            return {**out, "decision": "needs_info", "reason": "저위발열량 분석값 없음",
                    "question": "폐기물의 저위발열량(kcal/kg) 분석값이 있나요? 다른 물질과 섞이지 않은 상태 기준입니다."}
        out["value"] = f"{v:g} kcal/kg"
        if _mixed(case):
            return {**out, "decision": "needs_info",
                    "reason": "혼합 폐기물 — 기준은 다른 물질과 섞지 않은 상태의 발열량"}
        ok = v >= float(base) if cmp_ == ">=" else v < float(base)
        return {**out, "decision": "met" if ok else "not_met",
                "reason": f"저위발열량 {v:g} {'≥' if ok else '<'} {float(base):g} kcal/kg"}
    if row["비교"] == "제외":
        if _designated(route, case):
            return {**out, "to": None, "decision": "not_met", "reason": "지정폐기물은 제3조 제1항 제3호 대상에서 제외"}
        return {**out, "decision": "needs_info", "reason": f"품목이 제3호 목록({row['조건']})에 해당하는지 확인"}
    if "고시" in (row["조건"] or ""):
        return {**out, "decision": "needs_info", "reason": "고시 대상일 때만 적용 — 처리 업체 확인"}
    return {**out, "decision": "needs_info", "reason": f"처리 시설 확인 사항 ({row['조건'] or item})"}


def requirements(route: dict, case, rows: list[dict]) -> list[dict]:
    if route.get("tier") != "energy_recovery" or not rows:
        return []
    rc = route_rcodes(route)
    out = []
    for row in rows:
        want = {x for x in (row["r_codes"] or "").split(";") if x}
        if not (want & rc):
            continue
        r = _one(row, route, case)
        if row.get("상태") != "confirmed":
            r = {**r, "draft_decision": r["decision"], "decision": "needs_info",
                 "reason": f"{r['reason']} (표가 아직 draft — 원문 대조 후 confirmed)"}
        r["decision_label"] = LABEL[r["decision"]]
        out.append(r)
    return out


def emitter_questions(reqs: list[dict]) -> list[dict]:
    """배출업체에 물을 것 → route questions (to=part1)로 붙이면 InformationRequest에 모인다."""
    return [{"field": "heating_value", "to": "part1", "text": r["question"]}
            for r in reqs if r.get("question") and r.get("to") == "emitter"]


__all__ = ["load", "requirements", "emitter_questions", "FILE"]
