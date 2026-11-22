# route_agent/src/proposal/route_proposal.py 에서 복사 (2026.11.17). 바꾼 점: to_route_proposals 는 뺌 —
# 원본은 판정표(condition_texts, plan_check_targets)를 읽으므로 law_agent/output/proposal.py 가 판단 결과로 새로 만든다.
# 여기에는 판정표를 읽지 않는 도우미(route_id, _partner, _comparison …)만 남긴다.
"""pipeline 결과 → RouteProposal (계약 v1.1). 경로 하나마다 1개.

route_id는 같은 폐기물·같은 경로면 다시 계산해도 같은 값이어야 한다 (업체 회신을 경로에 연결하기 위해).
  {case_id}-G{관문}-{경로 종류}-{투입 방식}
"""
from __future__ import annotations

import re

from .checks import build_checks
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
            "sido", "sigungu", "scope_text", "capacity", "match_confidence", "evidence", "cautions")
    out = {"rank": rank, "step_order": step_order, "status": c.get("status") or "needs_info",
           **{k: c.get(k) for k in keep}}
    out["status"] = "needs_info" if out["status"] in (None, "needs_info") else out["status"]
    out["location"] = {"lat": loc.get("lat"), "lng": loc.get("lng"), "basis": loc.get("basis")} if loc else None
    out["distance_km"] = ({"value": loc.get("distance_km"), "type": "직선",
                           "road_est": (carbon or {}).get("road_km"), "basis": loc.get("basis"),
                           "road_km": loc.get("road_km"), "duration_min": loc.get("duration_min"),
                           "toll_won": loc.get("toll_won"), "road_basis": loc.get("road_basis") or "직선 × 1.3 추정"}
                          if loc.get("distance_km") is not None or loc.get("road_km") is not None else None)
    from law_agent.core.partners.evidence import distance_text
    out["distance_text"] = distance_text(loc or None)
    out["carbon"] = ({"transport_kgco2e": carbon["transport_kgco2e"], "unit": "kgCO2eq/월",
                      "vehicle": carbon["vehicle"], "ton": carbon["ton"], "basis": carbon["basis"]}
                     if carbon else None)
    return out


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


def mark_stale(proposals: list[dict], current_case_version: int) -> list[dict]:
    """폐기물 정보가 바뀌었으면 (case_version 증가) 이전 경로를 stale로 표시. 이전 판은 지우지 않는다."""
    for p in proposals:
        p["stale"] = p["case_version"] < current_case_version
    return proposals


ROLE_KO = {"preprocessor": "중간 가공업체 (중간 재활용업)", "recycler": "재활용업체", "treatment": "처리(처분)업체",
           "transporter": "운송업체 (수집·운반업)", "end_user": "활용 기업"}
ROLE_ORDER = ["preprocessor", "recycler", "treatment", "end_user", "transporter"]


def partners_by_role(proposal: dict) -> list[tuple[str, str, list[dict]]]:
    """경로의 업체 후보를 역할별로 나눠 각자 순위대로: [(role, 한글 이름, [후보…]), …].
    가공·처리 업체가 먼저, 운송업체는 따로 (운송업체 순위와 가공업체 순위는 서로 다른 기준·목록)."""
    groups: dict[str, list[dict]] = {}
    for c in proposal.get("partner_candidates") or []:
        groups.setdefault(c.get("role") or "기타", []).append(c)
    order = [r for r in ROLE_ORDER if r in groups] + [r for r in groups if r not in ROLE_ORDER]
    return [(r, ROLE_KO.get(r, r), sorted(groups[r], key=lambda c: c.get("rank") or 0)) for r in order]


def road_km(c: dict):
    d = c.get("distance_km") or {}
    for k in ("road_km", "road_est", "value"):
        if d.get(k) is not None:
            return d[k]
    return None


def road_text(c: dict) -> str:
    """화면용: '도로 23.4km · 31분 · 통행료 0원 (카카오)' 또는 '약 18.2km (직선 × 1.3 추정)'."""
    d = c.get("distance_km") or {}
    if d.get("road_km") is not None:
        bits = [f'도로 {d["road_km"]}km']
        if d.get("duration_min") is not None:
            bits.append(f'{d["duration_min"]}분')
        if d.get("toll_won") is not None:
            bits.append(f'통행료 {d["toll_won"]:,}원')
        return " · ".join(bits) + " (카카오)"
    km = road_km(c)
    return f"약 {km}km (직선 × 1.3 추정)" if km is not None else "거리 모름"
