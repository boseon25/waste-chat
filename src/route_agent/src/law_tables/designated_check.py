"""지정폐기물 해당 여부 사전 확인 (시행규칙 제17조의2, 배출자신고 지침 Ⅵ.1).

표 designated_check_targets.csv (draft_tables.py 초안 → 사람 확인 → promote → data/tables/active).

  request(case, route_dicts, rows, route_ids) → InformationRequest 항목 1개 또는 None
  - 사업장일반 분류번호(51-xx 등)인데 확인대상(오니류·광재·분진·소각재 …)에 해당하면
  - 또는 오염 물질에 기름 성분이 있으면 (확인대상 "기름성분")
  - 분석 결과(analysis.leaching_ok 등)가 아직 없으면 → 배출업체에 지정폐기물 해당 여부 분석 결과를 묻는다.
  이미 지정폐기물로 분류한 경우(01~4x)는 묻지 않는다 (확인할 대상이 아니라 이미 지정).
  판정(경로 가능·불가)은 바꾸지 않는다. 표가 draft면 질문 문장에 "(근거 표 확인 전)"을 붙인다.
"""
from __future__ import annotations

import csv
from pathlib import Path

FILE = "designated_check_targets.csv"
OIL_WORDS = ("기름", "유류", "윤활", "절삭유", "기계유", "폐유", "oil")
ANALYSIS_KEYS = ("leaching_ok", "designated_test", "hazardous_test")


def load(tables_dir: str | Path) -> list[dict]:
    path = Path(tables_dir) / FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _general(code: str) -> bool:
    try:
        return int(code.split("-")[0]) >= 50
    except ValueError:
        return False


def _prefix_hit(code: str, prefixes: str) -> bool:
    return any(p and (code == p or code.startswith(p + "-")) for p in (prefixes or "").split(";"))


def _has_analysis(case) -> bool:
    a = getattr(case, "analysis", None) or {}
    return any(a.get(k) is not None for k in ANALYSIS_KEYS)


def _oil(case) -> bool:
    c = getattr(case, "contamination", None) or {}
    words = " ".join(str(x) for x in (c.get("substances") or [])) + " " + str(c.get("note") or "")
    return any(w in words for w in OIL_WORDS)


def request(case, route_dicts: list[dict], rows: list[dict], route_ids: list[str]) -> dict | None:
    targets = [r for r in rows if r.get("구분") == "확인대상"]
    if not targets or _has_analysis(case):
        return None
    codes = sorted({c for d in route_dicts for c in (d.get("waste_codes") or []) if _general(c)})
    if not codes and getattr(case, "waste_code", None) and _general(case.waste_code):
        codes = [case.waste_code]
    if not codes:
        return None                                        # 지정폐기물로 이미 분류했거나 분류번호 없음

    hits = []
    for r in targets:
        if r.get("적용_분류번호"):
            matched = [c for c in codes if _prefix_hit(c, r["적용_분류번호"])]
            if matched:
                hits.append((r, f'{", ".join(matched)}: {r["대상"]}'))
        elif r.get("확인_항목") and "기름" in r["확인_항목"] and _oil(case):
            hits.append((r, "오염 물질에 기름 성분"))
    if not hits:
        return None

    draft = any((r.get("status") or r.get("상태")) != "confirmed" for r, _ in hits)
    harm = next((r for r in rows if r.get("구분") == "유해물질"), None)
    timing = [r["대상"] for r in rows if r.get("구분") == "확인시기"]
    why = "; ".join(w for _, w in hits)
    text = (f"이 폐기물({why})은 지정폐기물에 해당하는지 미리 확인해야 하는 대상입니다. "
            f"{'확인할 유해물질: ' + harm['확인_항목'] + '. ' if harm and harm.get('확인_항목') else ''}"
            "폐기물 분석전문기관의 분석 결과(유해물질 함유·용출 시험)가 있나요?"
            + (" (근거 표 확인 전)" if draft else ""))
    return {
        "field": "designated_check",
        "text": text,
        "to": "배출업체 (분석기관 의뢰 필요)",
        "affected_routes": list(route_ids),
        "matched": [{"rule_id": r["rule_id"], "target": r["대상"], "why": w} for r, w in hits],
        "when": timing,
        "basis": sorted({r.get("근거_청크", "") for r, _ in hits} | ({harm.get("근거_청크", "")} if harm else set()) - {""}),
        "basis_status": "draft" if draft else "confirmed",
    }


__all__ = ["load", "request", "FILE"]
