"""파트 1 패키지(wastecase_part1 0.5.x, WasteCase schema 2.0) 출력 → law_agent 입력(파트 1 계약 1.0) 변환.

받는 파일 (자동 판별):
  - phase3.py export   : {"export_kind": "phase3-document-workflow-review", "case": {...}, "source": {...}}
  - db_intake.py export: {"cases": [{...2.0}], "sites": [{"site_id", "address": Fact}], ...}
  - Handoff            : {"snapshot": {...2.0}, "published_event": ..., ...}
  - 이미 1.0           : {"cases": [{"schema_version": "1.0", ...}]} → 그대로

규칙 (추측하지 않는다):
  - Fact 의 value 만 쓴다. status 가 needs_info 이거나 value 가 없으면 그 칸은 비우고 missing 에 넣는다.
  - 분류번호: classification.selected_code (파트 1이 검토로 확정) → 없으면 reported_code.value (신고 번호).
    candidates 는 waste_code_candidates 로.
  - 월 물량(quantity.monthly_ton): generation(amount·unit·period)이 있을 때만 월 톤으로 환산.
    quantity(이번 반출 1회분, scope=current_lot)는 월 물량이 아니므로 current_lot 으로만 넘긴다.
  - 지역: 주소 앞머리의 시도·시군구. 시도가 없으면 시군구 중심점 표에서 그 이름이 한 시도에만 있을 때만 채운다.
  - 형태(material_form): physical_state 글에 뚜렷한 말(액상·분말·파쇄·원형 등)이 있을 때만.
  - 오염: visible_contamination not_visible → 깨끗, partial·widespread → 오염. substances 이름을 함께.
  - 지정 의심: hazard_declaration 값 (예·의심 → True, 아니오 → False, 모름 → 비움).
  - 원본 2.0 사례는 part1_v2 칸에 그대로 보존 (감사·추적용).
"""
from __future__ import annotations

import csv
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

V2_KIND = "phase3-document-workflow-review"
MINOR_PCT = 5.0           # 이 비율 미만 성분은 이물질로
UNIT_TO_TON = {"t": 1.0, "kg": 0.001, "g": 1e-6}
PERIOD_TO_MONTH = {"month": 1.0, "year": 1 / 12, "week": 52 / 12, "day": 365 / 12}
SIDO_RX = re.compile(r"^(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
                     r"경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|전라남도|경상북도|경상남도|"
                     r"제주특별자치도|서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)$")
SGG_RX = re.compile(r"^\S+(시|군|구)$")
FORM_WORDS = [("액상", r"액상|액체|오일|기름|용액|폐액|세척액"), ("분말", r"분말|가루|분진|먼지"),
              ("슬러지", r"슬러지|오니|진흙"), ("파쇄", r"파쇄|조각|칩|자투리|절단|분쇄|스크랩"),
              ("원형보존", r"원형|통째|그대로|완제품|팔레트|박스")]


# ── 판별 ─────────────────────────────────────────────────────

def is_v2(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("export_kind") == V2_KIND or "snapshot" in data:
        return True
    cases = data.get("cases")
    return bool(cases) and isinstance(cases[0], dict) and str(cases[0].get("schema_version", "")).startswith("2")


def convert(data: dict) -> dict:
    """2.0 묶음 → {"schema_version": "1.0", "cases": [...]} . 1.0 이면 그대로."""
    if not is_v2(data):
        return data
    sites: dict[str, str] = {}
    pairs: list[tuple[dict, str | None]] = []
    if "snapshot" in data:
        pairs = [(data["snapshot"], None)]
    elif data.get("export_kind") == V2_KIND:
        src = data.get("source") or {}
        case = dict(data["case"])
        case["_phase3"] = {"guidance_decisions": data.get("guidance_decisions") or [],
                           "intake_records": data.get("intake_records") or {}}
        pairs = [(case, src.get("site_address"))]
    else:
        for s in data.get("sites") or []:
            sites[s.get("site_id")] = _val(s.get("address"))
        pairs = [(c, sites.get(c.get("site_id"))) for c in data.get("cases") or []]
    return {"schema_version": "1.0", "export_kind": "part1_v2_converted", "converted_from": data.get("schema_version"),
            "published": data.get("published"), "cases": [case_v1(c, addr) for c, addr in pairs]}


# ── 사례 하나 ────────────────────────────────────────────────

def _val(f: Any) -> Any:
    """Fact → value (needs_info·없음이면 None). Fact 가 아니면 그대로."""
    if isinstance(f, dict) and "status" in f:
        return None if f.get("status") == "needs_info" else f.get("value")
    return f


def _num(x: Any) -> float | None:
    try:
        return float(Decimal(str(x)))
    except Exception:                                   # noqa: BLE001
        return None


def case_v1(c: dict, address: str | None = None) -> dict:
    missing: list[str] = list(c.get("missing_fields") or [])

    def need(path: str, v: Any) -> Any:
        if v in (None, "", []):
            missing.append(path)
        return v

    cls = c.get("classification") or {}
    reported = _val(cls.get("reported_code"))
    selected = cls.get("selected_code")
    extra = c.get("_phase3") or {}
    supported = _supported_code(extra.get("guidance_decisions") or [], c.get("case_id"))
    code = selected or reported or supported        # 파트 1이 별표 4 대조로 '근거 있음' 기록한 번호도 (A-1 이 다시 판단)
    cands = [x.get("code") for x in cls.get("candidates") or [] if x.get("code") and x.get("state") != "rejected"]

    rec = extra.get("intake_records") or {}
    comps = []
    for comp in (c.get("components") or []) if not rec.get("components") else []:
        comps.append({"material": _val(comp.get("name")), "share_pct": _num(_val(comp.get("proportion_percent"))),
                      "separable": _separable(_val(c.get("separability"))), "waste_code": None,
                      "status": "confirmed" if (comp.get("name") or {}).get("status") == "confirmed" else "reported"})
    for rc in rec.get("components") or []:          # 입력서의 성분표 (이름·비율·출처)
        comps.append({"material": rc.get("name"), "share_pct": _num(rc.get("percent")),
                      "separable": _separable(_val(c.get("separability"))), "waste_code": None,
                      "status": "reported", "source": rc.get("source")})
    # 비율이 아주 작은 성분(5% 미만, 예: PE 필름 98% 의 종이 라벨 2%)은 성분이 아니라 이물질로 —
    # 성분으로 두면 혼합물로 보고 질문을 성분마다 나눠 묻는다 (2026.11.18 서버: 질문 42개)
    foreign = []
    if len(comps) > 1 and all(x["share_pct"] is not None for x in comps):
        major = [x for x in comps if x["share_pct"] >= MINOR_PCT]
        if major:
            foreign = [x for x in comps if x["share_pct"] < MINOR_PCT]
            comps = major
    mixture = _val(c.get("mixture_type"))

    vis = _val(c.get("visible_contamination"))
    subs = [s for s in (_val(x) for x in c.get("substances") or []) if s]
    contamination = None
    if vis is not None or subs:
        contamination = {"level": "오염" if (vis in ("partial", "widespread") or subs) else "깨끗", "substances": subs}

    analysis = _analysis(c.get("analysis_results") or [])
    monthly = _monthly_ton(c.get("generation") or {})
    q = c.get("quantity") or {}
    lot = _num(_val(q))
    months = [(m.get("month"), _num(m.get("ton"))) for m in rec.get("monthly_generation") or []]
    months = [(p, t) for p, t in months if t is not None]
    if months:                                     # 입력서의 월별 발생량 (발생대장 출처)
        quantity = {"monthly_ton": [t for _, t in months], "monthly_periods": [p for p, _ in months],
                    "unit": "ton", "status": "reported"}
        monthly = months[-1][1]
    else:
        quantity = {"monthly_ton": [monthly] if monthly is not None else [], "unit": "ton",
                    "status": "confirmed" if monthly is not None else "needs_info"}
    if lot is not None:
        quantity["current_lot"] = {"value": lot, "unit": q.get("unit")}
    if monthly is None:
        missing.append("quantity.monthly_ton")

    region = _region(address)
    hazard = _hazard(_val(c.get("hazard_declaration")))
    form = _form(_val(c.get("physical_state")))

    out = {
        "schema_version": "1.0",
        "case_id": c.get("case_id"),
        "case_version": c.get("case_version") or 0,
        "synthetic": bool(c.get("synthetic")),
        "name": _val(c.get("name")),
        "process_description": _val(c.get("process_description")),
        "classification": {"reported_code": reported or supported, "selected_code": need("classification.selected_code", code),
                           "status": "reviewed" if selected else ("reported" if reported else
                                                                  ("part1_supported" if supported else "needs_info"))},
        "waste_code_candidates": cands,
        "components": comps,
        "mixed_with_other": (False if len(comps) == 1 and foreign else (None if mixture is None else mixture != "single")),
        "foreign_matter": {"items": [f"{x['material']} {x['share_pct']:g}%" for x in foreign],
                           "separable": None} if foreign else {"items": [], "separable": None},
        "material_form": need("material_form", form),
        "contamination": need("contamination", contamination),
        "hazardous_suspect": need("hazardous_suspect", hazard),
        "analysis": analysis,
        "quantity": quantity,
        "region": need("region", region),
        "missing": sorted(set(missing)),
        "not_applicable": [],
        "part1_v2": {"case_id": c.get("case_id"), "draft_revision": c.get("draft_revision"),
                     "limitations": c.get("limitations") or [], "physical_state": _val(c.get("physical_state")),
                     "industry": _val(c.get("industry")), "site_id": c.get("site_id")},
    }
    return out


def _supported_code(decisions: list[dict], case_id: Any) -> str | None:
    """파트 1 part1.py classification supported --code … 로 남긴 번호 (가장 최근, stale 아님)."""
    ok = [d for d in decisions if d.get("choice") == "supported" and d.get("code") and not d.get("stale")
          and (d.get("case_id") in (None, case_id))]
    ok.sort(key=lambda d: str(d.get("recorded_at") or ""))
    return ok[-1]["code"] if ok else None


def _separable(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).lower()
    if s in ("true", "yes", "예", "separable", "가능"):
        return "예"
    if s in ("false", "no", "아니오", "inseparable", "불가"):
        return "아니오"
    return None


def _hazard(v: Any) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "yes", "예", "의심", "해당", "suspect", "designated"):
        return True
    if s in ("false", "no", "아니오", "비해당", "not_designated", "none"):
        return False
    return None


def _form(text: Any) -> str | None:
    if not text:
        return None
    for name, rx in FORM_WORDS:
        if re.search(rx, str(text)):
            return name
    return None


def _monthly_ton(g: dict) -> float | None:
    amount, unit, period = _num(_val(g.get("amount"))), _val(g.get("unit")), _val(g.get("period"))
    if amount is None or unit not in UNIT_TO_TON or period not in PERIOD_TO_MONTH:
        return None
    return round(amount * UNIT_TO_TON[unit] * PERIOD_TO_MONTH[period], 4)


ANALYSIS_KEYS = [("heating_value_kcal", r"발열량|heating"), ("moisture_pct", r"수분|moisture"),
                 ("chlorine_pct", r"염소|chlorine"), ("pcb_ppm", r"PCB|폴리클로리네이티드")]


def _analysis(results: list[dict]) -> dict:
    out: dict[str, Any] = {"heating_value_kcal": None, "moisture_pct": None, "chlorine_pct": None, "leaching_ok": None}
    for r in results:
        name = str(r.get("substance") or "")
        for key, rx in ANALYSIS_KEYS:
            if re.search(rx, name, re.I):
                if r.get("qualifier") == "not_detected":
                    out[key] = 0.0
                elif r.get("value") is not None:
                    out[key] = _num(r.get("value"))
    return out


def _region(address: str | None) -> dict | None:
    if not address:
        return None
    toks = re.split(r"[\s,]+", address.strip())
    sido = sgg = None
    if toks and SIDO_RX.match(toks[0]):
        sido = toks[0]
        toks = toks[1:]
    if toks and SGG_RX.match(toks[0]):
        sgg = toks[0]
        if len(toks) > 1 and toks[0].endswith("시") and re.match(r"^\S+구$", toks[1]):
            sgg = f"{toks[0]} {toks[1]}"
    if sgg and not sido:
        sido = infer_sido(sgg.split()[0])
    if not (sido or sgg):
        return None
    return {"sido": sido, "sigungu": sgg, "address": address}


def infer_sido(sigungu: str) -> str | None:
    """시군구 이름이 한 시도에만 있으면 그 시도 (시군구 중심점 표). 표가 없거나 여러 시도면 None."""
    base = os.environ.get("WASTE_REFERENCE_DIR")
    if not base:
        return None
    path = Path(base) / "sigungu_centers_2025_2Q.csv"
    if not path.is_file():
        return None
    from law_agent.core.partners.geo import SGIS_SIDO
    found = set()
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("sig_nm") or "").strip()
            code = (row.get("sig_cd") or "").strip()
            if name.split(" ")[0] == sigungu and code[:2] in SGIS_SIDO:
                found.add(SGIS_SIDO[code[:2]])
    return found.pop() if len(found) == 1 else None
