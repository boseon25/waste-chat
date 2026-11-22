"""필요 서류·절차 판단 (v1.1 7절 document_triggers).

어떤 서류가 필요해지는지 판단할 사실과 근거만 낸다. 어떤 서식으로 쓸지는 파트 3이 정한다.
문구와 근거는 condition_texts 표(의무 문구, status=draft → "원문 대조됨, 검토 대기")에서 가져온다.
처리계획 확인 대상 여부는 plan_check_targets 표(시행규칙 제18조의2 제1항)로 판정한다.
표가 draft면 decision은 "확인 필요"로 두고 계산값은 draft_decision에 남긴다.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from law_tables import plan_check


@lru_cache(maxsize=4)
def load_conditions(tables_dir: str) -> dict[str, dict]:
    path = Path(tables_dir) / "condition_texts.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["condition_id"]: r for r in csv.DictReader(f)}


def _status(row: dict) -> str:
    return "확정" if row.get("status") == "confirmed" else "원문 대조됨, 검토 대기"


def _trig(conds: dict, cid: str, trigger: str, fact: str) -> dict:
    row = conds.get(cid) or {}
    return {"trigger": trigger, "fact": fact, "condition_id": cid if row else None,
            "text": row.get("안내문구"), "basis": row.get("근거_청크"),
            "basis_status": _status(row) if row else None}


def build_document_triggers(route: dict, tables_dir: str, ton_month: float | None,
                            has_report_partner: bool) -> list[dict]:
    conds = load_conditions(tables_dir)
    codes = route.get("waste_codes") or []
    code_txt = ", ".join(codes)
    designated = route.get("waste_status") == "designated"
    kind = "지정" if designated else "사업장일반"

    if route["gate"] == 1:
        ev = next((e for e in route.get("evidence_ids") or [] if e.startswith("CEA_DESIG")), None)
        return [{"trigger": "폐기물 위탁 서류 해당 없음",
                 "fact": f"{code_txt} 지정 순환자원 판매 (고시 기준을 지키는 동안 폐기물이 아님)",
                 "condition_id": None, "text": "위탁계약·수탁처리능력 확인·전자인계 대상이 아닙니다. 고시의 용도·방법 기준을 지켜야 합니다.",
                 "basis": ev, "basis_status": "확정" if ev else None}]
    if route["regulatory"] == "rejected":
        return []

    out = [
        _trig(conds, "CT-VI-7", "위탁계약서", f"{kind} {code_txt} 처리 위탁 (관문 {route['gate']})"),
        _trig(conds, "CT-VI-4", "수탁처리능력 확인", "위탁 계약 전 처리 업체의 수탁처리능력확인서"),
    ]
    exempt = set(((conds.get("CT-VII-2-EXEMPT") or {}).get("적용_분류번호") or "").split(";")) - {""}
    if not designated and codes and all(c in exempt for c in codes):
        out.append(_trig(conds, "CT-VII-2-EXEMPT", "전자인계 제외", f"{code_txt}는 폐지·고철(비철 포함)"))
    else:
        out.append(_trig(conds, "CT-18-3", "전자인계 입력 (올바로)", f"{kind} {code_txt} 위탁"))
    if designated:
        kg = round(ton_month * 1000, 1) if ton_month else None
        pc = plan_check.judge(codes, kg, plan_check.load(tables_dir))
        fact = "지정폐기물 위탁" + (f", 월 평균 {kg:g}kg" if kg else ", 월 배출량 모름")
        t = _trig(conds, "CT-17-5", "지정폐기물 처리계획 확인", f"{fact} → {pc['decision_label']}: {pc['reason']}")
        t["plan_check"] = pc
        out.append(t)
    if has_report_partner:
        out.append({"trigger": "신고 범위 확인 자료", "fact": "추천 업체 중 폐기물처리 신고 업체 있음",
                    "condition_id": None, "text": "신고 업체는 신고 범위(시행규칙 별표 16)와 하루 처리량 안에서만 받을 수 있습니다.",
                    "basis": "폐기물관리법 제46조, 시행규칙 별표 16", "basis_status": None})
    return out
