"""pipeline 결과 → RouteProposal (계약 v1.1). 경로 하나마다 1개.

route_id는 같은 폐기물·같은 경로면 다시 계산해도 같은 값이어야 한다 (업체 회신을 경로에 연결하기 위해).
  {case_id}-G{관문}-{경로 종류}-{투입 방식}
"""
from __future__ import annotations

import re

from .checks import build_checks
from .documents import build_document_triggers, load_conditions
from .related_laws import related_laws
from .questions import build_questions

SCHEMA_VERSION = "1.1"
PROCESSOR_ROLES = ("recycler", "preprocessor", "treatment", "end_user")
G4_KIND = (("소각", "incineration"), ("매립", "landfill"), ("중간처분", "intermediate"))


def route_id(case_id: str, route: dict) -> str:
    if route["gate"] == 4:
        kind = next((k for t, k in G4_KIND if route["title"].startswith(t)), "disposal")
    elif route["gate"] == 1:
        kind = "designated_resource"
    else:
        kind = route.get("tier") or "intermediate"
    return f"{case_id}-G{route['gate']}-{kind}-{route.get('route_type') or 'none'}"


def assign_route_ids(case_id: str, routes: list[dict]) -> list[str]:
    """경로 목록 → route_id 목록. 같은 종류 경로가 둘이면 뒤에 -2, -3 (pipeline 매칭과 proposal이 같이 씀)"""
    used: dict[str, int] = {}
    out = []
    for r in routes:
        rid = route_id(case_id, r)
        used[rid] = used.get(rid, 0) + 1
        out.append(rid if used[rid] == 1 else f"{rid}-{used[rid]}")
    return out


def _main_step(steps: list[dict]) -> dict | None:
    cands = [s for s in steps if s["role"] in PROCESSOR_ROLES]
    required = [s for s in cands if not s.get("optional")]
    return (required or cands or [None])[-1]


def _partner(c: dict, rank: int, step_order: int) -> dict:
    loc = c.get("location") or {}
    carbon = c.get("carbon")
    keep = ("company_id", "name", "role", "permit_type", "registration_type", "waste_group", "treatment_codes",
            "r_codes", "accepts_designated", "address", "phone", "data_date", "source", "reasons",
            "sido", "sigungu", "scope_text", "capacity", "match_confidence")
    out = {"rank": rank, "step_order": step_order, "status": c.get("status") or "needs_info",
           **{k: c.get(k) for k in keep}}
    out["status"] = "needs_info" if out["status"] in (None, "needs_info") else out["status"]
    out["location"] = {"lat": loc.get("lat"), "lng": loc.get("lng"), "basis": loc.get("basis")} if loc else None
    out["distance_km"] = ({"value": loc.get("distance_km"), "type": "직선",
                           "road_est": (carbon or {}).get("road_km"), "basis": loc.get("basis")}
                          if loc.get("distance_km") is not None else None)
    out["carbon"] = ({"transport_kgco2e": carbon["transport_kgco2e"], "unit": "kgCO2eq/월",
                      "vehicle": carbon["vehicle"], "ton": carbon["ton"], "basis": carbon["basis"]}
                     if carbon else None)
    return out


TRANSPORT_OPTIONS = 3


def transport_options(main_cands: list[dict], steps: list[dict], top: int = TRANSPORT_OPTIONS) -> None:
    """가공업체(주 단계 후보)마다 운송 업체 추천을 붙인다 (2026-11-17).

    운송 한 건 = 차고지 → 배출지(빈 차) → 가공업체(짐) → 차고지(빈 차).
    배출지 → 가공업체 구간은 어느 운송 업체든 같으므로, 업체마다 다른 건 빈 차 거리:
        empty_km = (운송 업체 → 배출지) + (가공업체 → 운송 업체)
    이 값이 짧은 순으로 고른다. 좌표가 없으면 배출지까지 거리만 쓴다 (basis 에 표시).
    업체 주소 기준이라 실제 차고지와 다를 수 있다 → 문의 순서 참고용.
    """
    from partners.geo import haversine_km
    tsteps = [s for s in steps if s["role"] == "transporter" and not s.get("optional")]
    pool = [c for s in tsteps for c in (s.get("candidates") or []) + (s.get("reserve") or [])]
    seen, trans = set(), []
    for c in pool:
        if c["company_id"] not in seen:
            seen.add(c["company_id"])
            trans.append(c)
    for m in main_cands:
        if not trans:
            m["transport_options"] = []
            continue
        mloc = m.get("location") or {}
        rows = []
        for c in trans:
            loc = c.get("location") or {}
            to_origin = (c.get("distance_km") or {}).get("value")
            back = (haversine_km((mloc["lat"], mloc["lng"]), (loc["lat"], loc["lng"]))
                    if None not in (mloc.get("lat"), mloc.get("lng"), loc.get("lat"), loc.get("lng")) else None)
            empty = (to_origin + back) if to_origin is not None and back is not None else to_origin
            rows.append({"company_id": c["company_id"], "name": c["name"], "phone": c.get("phone"),
                         "permit_type": c.get("permit_type"),
                         "to_origin_km": to_origin, "from_destination_km": back,
                         "empty_km": round(empty, 1) if empty is not None else None,
                         "basis": ("운송 업체 → 배출지 + 가공업체 → 운송 업체" if back is not None
                                   else "운송 업체 → 배출지 (좌표 없음)") + f", 위치: {loc.get('basis') or '없음'}"})
        rows.sort(key=lambda r: (r["empty_km"] is None, r["empty_km"] or 0, r["name"]))
        m["transport_options"] = [{"rank": i + 1, **r} for i, r in enumerate(rows[:top])]


def _law_evidences(route: dict, store=None) -> list[dict]:
    """store(LawStore)가 있으면 근거마다 원문에서 이 판정에 해당하는 부분만 발췌해 text에 넣는다."""
    out = []
    codes = route.get("waste_codes") or []
    for l in route.get("laws") or []:
        if not l.get("law"):
            continue
        if l.get("key"):
            head = l["key"].split("@", 1)[0]
            eid = "LAW-" + head.replace(":", "-")
            ref = l["key"]
        else:
            eid = "LAW-" + re.sub(r"\s+", "", f'{l["law"]}-{l["article"] or ""}')
            ref = None
        ev = {"evidence_id": eid, "source": f'{l["law"]} {l["article"] or ""}'.strip(), "ref": ref,
              "law": l["law"], "article": l["article"], "effective_date": l.get("effective_date"),
              "used_for": l.get("used_for") or [], "text": l.get("text"), "text_basis": None}
        lookup = ref or (store.find_by_name(l["law"], l["article"]) if store is not None else None)
        if store is not None and lookup:
            ex = store.excerpt(lookup, codes=codes)
            ev["text"], ev["text_basis"] = ex.get("text"), ex.get("text_basis")
            if ex.get("same_effective") is False:
                ev["text_basis"] = f'{ev["text_basis"]} (근거 시행일 {ex.get("effective_in_key")} ≠ 저장 조문 {ex.get("effective_in_store")}, 재확인 필요)'
        out.append(ev)
    return out


def _partner_evidences(cands: list[dict]) -> list[dict]:
    out, seen = [], set()
    for c in cands:
        src = c.get("source") or ""
        if not src or c["company_id"] in seen:
            continue
        seen.add(c["company_id"])
        file, _, rest = src.partition("#")
        out.append({"evidence_id": f'PARTNER-{c["company_id"]}', "source": re.sub(r"\.(xlsx|xls|csv)$", "", file),
                    "ref": rest or None})
    return out


def _comparison(route: dict, main_candidates: list[dict]) -> dict:
    ref = route.get("carbon_reference") or {}
    first = next((c for c in main_candidates if c.get("carbon")), None)
    missing = ["원료 대체 계수", "quote"]
    if first is None:
        missing.append("업체 위치")
    if ref.get("treatment_kgco2e") is None:
        missing.append("처리 공정 계수")
    return {"carbon_transport": first["carbon"]["transport_kgco2e"] if first else None,
            "carbon_treatment_ref": ref.get("treatment_kgco2e"),
            "carbon_treatment_note": ref.get("note"),
            "carbon_total": None, "carbon_saving": None, "cost_total": None,
            "missing": missing,
            "basis": ref.get("source")}


def _restrictions(pre: dict) -> list[dict]:
    r = pre.get("restriction") or {}
    out = []
    for rule in r.get("matched_rules") or []:
        if isinstance(rule, dict):
            out.append({k: rule.get(k) for k in ("rule_id", "action", "description", "basis") if k in rule} or rule)
        else:
            out.append({"rule": rule})
    return out


def to_route_proposals(result: dict, raw_case: dict, case, tables_dir: str, law_store=None) -> list[dict]:
    from statistics import mean
    monthly = [v for v in ((case.quantity or {}).get("monthly_ton") or []) if isinstance(v, (int, float))]
    ton = round(mean(monthly), 3) if monthly else None
    ton_txt = f"월 {ton}톤" if ton else "발생량(미확인)"
    pre = result["preliminary"]
    waste_name = raw_case.get("name") or (pre.get("classification") or {}).get("waste_name") or ""

    try:
        conds = load_conditions(tables_dir)
        exempt = set(((conds.get("CT-VII-2-EXEMPT") or {}).get("적용_분류번호") or "").split(";")) - {""}
    except FileNotFoundError:
        exempt = set()

    proposals = []
    for r, rid in zip(result["routes"], assign_route_ids(result["case_id"], result["routes"])):
        p = r.get("partners") or {}
        steps = p.get("steps") or []
        opt = {s["step_order"]: s.get("optional", False) for s in steps}
        main = _main_step(steps)
        main_cands = [_partner(c, i + 1, main["step_order"]) for i, c in enumerate(main["candidates"])] if main else []
        reserve = ([_partner(c, len(main_cands) + i + 1, main["step_order"]) for i, c in enumerate(main.get("reserve") or [])]
                   if main else [])
        other_cands = [_partner(c, i + 1, s["step_order"])
                       for s in steps if s is not main for i, c in enumerate(s["candidates"])]
        transport_options(main_cands, [{**s, "optional": opt.get(s["step_order"], False),
                                        "candidates": [_partner(c, 0, s["step_order"]) for c in s["candidates"]],
                                        "reserve": [_partner(c, 0, s["step_order"]) for c in s.get("reserve") or []]}
                                       for s in steps if s is not main])
        checks, why = build_checks(r, main_cands)
        law_ev = _law_evidences(r, law_store)
        if law_store is not None:                  # RAG 검색 후보: 파트 2 내부 점검용 (RouteProposal에는 넣지 않음)
            r["law_search_candidates"] = related_laws(r, law_store, waste_name, exempt,
                                                      {e["ref"] for e in law_ev if e.get("ref")}, search=True)
        codes_txt = ", ".join(r.get("waste_codes") or [])

        proposals.append({
            "schema_version": SCHEMA_VERSION,
            "case_id": result["case_id"], "case_version": result["case_version"],
            "route_id": rid, "route_version": 1,
            "stale": False, "synthetic": bool(raw_case.get("synthetic", False)),
            "gate": r["gate"], "waste_status": r["waste_status"], "tier": r.get("tier"),
            "route_type": r.get("route_type"), "title": r["title"],
            "steps": [{"order": s["order"], "process": s["process"], "role": s["role"],
                       "treatment_codes": s.get("treatment_codes") or [], "r_codes": s.get("r_codes") or [],
                       "optional": opt.get(s["order"], False),
                       "treatment_code": (s.get("treatment_codes") or [None])[0],
                       "r_code": (s.get("r_codes") or [None])[0]} for s in r["steps"]],
            "checks": checks, "check_reasons": why,
            "secured": p.get("secured"),
            "main_step_order": main["step_order"] if main else None,     # 추천 업체(가공·처분·구매)가 있는 단계
            "partner_candidates": main_cands + other_cands,
            "reserve_candidates": reserve,                               # 거절 시 올릴 예비 후보 (주 단계)
            "excluded_partners": [],                                     # 회신에서 거절한 업체
            "waste_ton_month": ton,
            "questions": build_questions(main, steps, f"{codes_txt} {waste_name}".strip(), ton_txt) if main else [],
            "conditions": r.get("conditions") or [],
            "required_tests": r.get("required_tests") or [],
            "requirements": r.get("requirements") or [],
            "alternatives": r.get("alternatives"),                        # 업체 미확보 경로의 대안 후보 (--agent일 때만, 확인 필요)                # 법정 기준 확인 (에너지 회수기준 등)
            "restrictions": _restrictions(pre),
            "evidences": law_ev + _partner_evidences(main_cands),
            "related_laws": related_laws(r, law_store, waste_name, exempt),            # 규칙 조문만 (검색 후보는 r["law_search_candidates"])
            "document_triggers": build_document_triggers(
                r, tables_dir, ton, any(c.get("permit_type") == "신고" for c in main_cands)),
            "comparison": _comparison(r, main_cands),
            "rejected_summary": (main or {}).get("rejected_summary") or {},
            "applied_feedback_ids": [],
        })
    return proposals


def mark_stale(proposals: list[dict], current_case_version: int) -> list[dict]:
    """폐기물 정보가 바뀌었으면 (case_version 증가) 이전 경로를 stale로 표시. 이전 판은 지우지 않는다."""
    for p in proposals:
        p["stale"] = p["case_version"] < current_case_version
    return proposals
