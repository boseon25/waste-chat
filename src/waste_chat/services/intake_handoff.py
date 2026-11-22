"""분류 에이전트가 넘긴 전달 파일(handoff JSON, part2-example-1.0 등)을 읽는다.

- 분류번호는 사람이 고르지 않는다: classification.selected_code를 그대로 쓴다(별표 4 말단 번호인지 검사만).
- 화면용 사실(facts)은 Word 입력서와 같은 모양으로 만들고, 원래 건(case)은 그대로 보관해 법령 판단 엔진에 넘긴다
  (분석값·월별 기록·순환자원 인정·근거 목록 같은 자세한 값이 사실 칸으로 줄면서 사라지지 않게).
- 값을 추측해 채우지 않는다. 없는 값은 빈칸으로 둔다.
"""
import json
import re

from . import tables

MAX_CASES = 20
MAX_CASE_BYTES = 200_000

CLEAN_WORDS = ("안 보", "없음", "깨끗", "양호")
DESIGNATED = {"designated": "예", "non_designated": "아니오"}
YES_NO = {"예": True, "아니오": False, "yes": True, "no": False, True: True, False: False}


def _txt(v, n=400):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _address(region):
    region = region if isinstance(region, dict) else {}
    addr, sido, sigungu = _txt(region.get("address")), _txt(region.get("sido")), _txt(region.get("sigungu"))
    if addr and sido and not addr.startswith(sido[:2]):
        if sigungu and not addr.startswith(sigungu):
            addr = f"{sigungu} {addr}"
        addr = f"{sido} {addr}"
    return addr or " ".join(x for x in (sido, sigungu) if x)


def _monthly(q):
    vals = [x for x in (_num(v) for v in (q or {}).get("monthly_ton") or []) if x is not None]
    return f"{round(sum(vals) / len(vals), 3):g}" if vals else ""


def facts_of(case):
    """전달 건 → 화면 사실. 키는 engine.FACT_KEYS와 같다."""
    comps = [c for c in case.get("components") or [] if isinstance(c, dict) and c.get("material")]
    contam = case.get("contamination") if isinstance(case.get("contamination"), dict) else {}
    level = _txt(contam.get("level"))
    subs = [s for s in (_txt(x, 60) for x in contam.get("substances") or []) if s]
    analysis = case.get("analysis") if isinstance(case.get("analysis"), dict) else {}
    designated = DESIGNATED.get(case.get("designated_waste_status"))
    seps = {YES_NO.get(c.get("separable")) for c in comps} - {None}
    obs = case.get("source_observations") if isinstance(case.get("source_observations"), dict) else {}
    if not obs.get("physical_state") and isinstance(case.get("part1_v2"), dict):  # 파트 1 내보내기를 바꾼 건: 원문 겉모양
        obs = {**obs, "physical_state": case["part1_v2"].get("physical_state")}
    form = _txt(case.get("material_form"), 60)
    if obs.get("physical_state") and _txt(obs["physical_state"], 80) not in form:
        form = f"{form} ({_txt(obs['physical_state'], 80)})" if form else _txt(obs["physical_state"], 80)
    facts = {
        "name": _txt(case.get("name"), 80),
        "code": _txt((case.get("classification") or {}).get("selected_code"), 8),
        "process": _txt(case.get("process_description")),
        "form": form,
        "components": ", ".join(f"{_txt(c['material'], 60)} {c['share_pct']:g}%" if _num(c.get("share_pct")) is not None
                                else _txt(c["material"], 60) for c in comps),
        "contamination": ("깨끗" if any(w in level for w in CLEAN_WORDS) else "오염") if level else "",
        "contamination_detail": ", ".join(subs) or (_txt(contam.get("raw_observation")) if level and not any(w in level for w in CLEAN_WORDS) else ""),
        "hazard": designated or {True: "의심", False: "아니오"}.get(case.get("hazardous_suspect"), ""),
        "monthly_ton": _monthly(case.get("quantity")),
        "address": _address(case.get("region")),
        "mixture": {True: "혼합", False: "단일"}.get(case.get("mixed_with_other"), ""),
        "separable": ("가능" if seps == {True} else "불가능" if False in seps else "") if len(comps) > 1 else "",
        # 분석 결과서가 있고(시료 번호·보고일) 지정 여부가 정해져 있을 때만
        "analysis": ({"예": "있음·지정 해당", "아니오": "있음·지정 아님"}.get(designated, "")
                     if analysis.get("report_date") or analysis.get("sample_id") else ""),
    }
    if facts["hazard"] == "의심":
        facts["hazard"] = "예"
    return {k: v for k, v in facts.items() if v}


def sources_of(case):
    """화면에 보여 줄 근거·출처(문장). 판단 사실로는 쓰지 않는다."""
    out = []
    cl = case.get("classification") or {}
    if cl.get("selected_code"):
        head = f"분류번호 {cl['selected_code']} {_txt(cl.get('code_label'), 80)}".strip()
        out.append(head + (f": {_txt(cl.get('basis'), 200)}" if cl.get("basis") else "")
                   + (f" (조건: {_txt(cl.get('conditions'), 200)})" if cl.get("conditions") else ""))
    if case.get("designation_scope"):
        out.append(f"지정 여부 범위: {_txt(case['designation_scope'], 200)}")
    q = case.get("quantity") or {}
    recs = [r for r in q.get("monthly_records") or [] if isinstance(r, dict)]
    if recs:
        out.append("월 발생량: " + ", ".join(f"{_txt(r.get('period'), 10)} {_txt(r.get('value'), 12)}{_txt(r.get('unit'), 6)}" for r in recs))
    lot = q.get("current_lot") or {}
    if lot.get("value") is not None:
        out.append(f"이번 처리량: {_txt(lot.get('value'), 12)}{_txt(lot.get('unit'), 6)}"
                   + (f" ({_txt(lot.get('measured_at'), 12)} 측정)" if lot.get("measured_at") else ""))
    a = case.get("analysis") or {}
    parts = []
    if a.get("heating_value_kcal") is not None:
        parts.append(f"발열량 {a['heating_value_kcal']}{_txt(a.get('heating_value_unit') or 'kcal/kg', 10)}")
    if a.get("moisture_pct") is not None:
        parts.append(f"수분 {a['moisture_pct']}%")
    if a.get("chlorine_pct") is not None:
        parts.append(f"염소 {a['chlorine_pct']}%")
    for r in a.get("leaching_results") or []:
        if isinstance(r, dict):
            parts.append(f"{_txt(r.get('analyte'), 20)} {_txt(r.get('result'), 12)}{_txt(r.get('unit'), 8)}")
    if parts:
        out.append(f"성분 분석({_txt(a.get('sample_id'), 30)}, {_txt(a.get('report_date'), 12)}): " + ", ".join(parts))
    for key, label in (("storage_state", "보관 상태"), ("raw_materials", "원료"), ("chemicals", "약품")):
        if case.get(key):
            out.append(f"{label}: {_txt(case[key], 200)}")
    fm = case.get("foreign_matter") or {}
    if fm.get("items"):
        out.append("이물질: " + ", ".join(_txt(x, 40) for x in fm["items"]))
    cr = case.get("circular_resource") or {}
    if cr.get("status") and cr.get("recognition_reference"):
        out.append(f"순환자원 인정: {_txt(cr.get('recognition_reference'), 40)} ({_txt(cr.get('valid_from'), 12)}~{_txt(cr.get('valid_to'), 12)}) {_txt(cr.get('scope'), 120)}")
    elif cr.get("note"):
        out.append(f"순환자원: {_txt(cr['note'], 160)}")
    ex = case.get("existing_declaration") or {}
    if ex.get("exists"):
        out.append(f"기존 신고: 분류번호 {_txt(ex.get('waste_code'), 10)}, 처리 {_txt(ex.get('treatment_code'), 10)}")
    if case.get("missing"):
        out.append("비어 있다고 표시된 값: " + ", ".join(_txt(m, 40) for m in case["missing"]))
    return out


def _evidence(data):
    rows = data.get("evidence") or (data.get("preparation") or {}).get("evidence") or []
    return {str(e.get("evidence_id") or e.get("id")): e for e in rows if isinstance(e, dict) and (e.get("evidence_id") or e.get("id"))}


def read(blob):
    """JSON 바이트 → {"company", "items"}. 형식이 맞지 않으면 ValueError."""
    try:
        data = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("JSON 형식이 아닙니다.") from None
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("전달 파일에 cases(폐기물 건) 목록이 없습니다.")
    co = data.get("company") if isinstance(data.get("company"), dict) else {}
    company = {"company_name": _txt(co.get("name"), 80), "business_number": _txt(co.get("business_number"), 40),
               "contact_name": _txt(co.get("contact_name"), 40), "contact_phone": _txt(co.get("contact_phone"), 40)}
    items = []
    for n, case in enumerate(data["cases"][:MAX_CASES], 1):
        if not isinstance(case, dict) or not case.get("name"):
            continue
        if len(json.dumps(case, ensure_ascii=False).encode()) > MAX_CASE_BYTES:
            raise ValueError(f"{n}번째 건이 너무 큽니다.")
        facts = facts_of(case)
        cl = case.get("classification") or {}
        code_note = ""
        if facts.get("code"):
            good, msg = tables.check_code(facts["code"])
            code_note = f"분류 에이전트가 정한 번호 {facts['code']} {_txt(cl.get('code_label'), 60)}" + ("" if good else f" — {msg}")
            if not good:
                facts.pop("code")
        else:
            code_note = "전달 파일에 분류번호(classification.selected_code)가 없습니다. 분류 에이전트에서 번호를 정한 뒤 다시 올려 주세요."
        items.append({"index": n, "facts": facts, "sources": sources_of(case), "mixture": facts.get("mixture", ""),
                      "history": "", "code_note": code_note, "handoff": case,
                      "code_label": _txt(cl.get("code_label"), 80), "code_basis": _txt(cl.get("basis"), 300),
                      "code_conditions": _txt(cl.get("conditions"), 300),
                      "synthetic": bool(case.get("synthetic") or data.get("synthetic"))})
    if not items:
        raise ValueError("전달 파일에 이름이 있는 폐기물 건이 없습니다.")
    return {"company": {k: v for k, v in company.items() if v}, "items": items, "schema": _txt(data.get("schema_version"), 40),
            "notice": _txt(data.get("sample_notice"), 300)}


BOOL = {"예": True, "아니오": False}


def engine_case(handoff, facts, base, owner, version):
    """보관한 전달 건을 엔진 입력(1.0)으로. 사용자가 '사실 고치기'로 바꾼 값(base와 다른 값)만 build_case 값으로 덮는다."""
    from . import engine
    case = json.loads(json.dumps(handoff))
    built = engine.build_case(owner, facts, version)
    changed = {k for k in engine.FACT_KEYS if (facts.get(k) or "") != (base.get(k) or "")}
    overlay = {
        "name": ["name"], "process": ["process_description"], "form": ["material_form"],
        "components": ["components", "foreign_matter", "mixed_with_other"], "mixture": ["mixed_with_other"],
        "separable": ["components", "foreign_matter"], "contamination": ["contamination"], "contamination_detail": ["contamination"],
        "hazard": ["hazardous_suspect"], "analysis": ["hazardous_suspect", "analysis"], "monthly_ton": ["quantity"], "address": ["region"],
    }
    for k in changed:
        for path in overlay.get(k, []):
            case[path] = built[path]
    if "code" in changed or not (case.get("classification") or {}).get("selected_code"):
        case["classification"] = {**(case.get("classification") or {}), **built["classification"]}
    for c in case.get("components") or []:  # 1.0은 분리 가능 여부를 참/거짓으로 읽는다
        if isinstance(c, dict) and isinstance(c.get("separable"), str):
            c["separable"] = BOOL.get(c["separable"])
    fm = case.get("foreign_matter")
    if isinstance(fm, dict) and isinstance(fm.get("separable"), str):
        fm["separable"] = BOOL.get(fm["separable"])
    case.update({"schema_version": "1.0", "case_id": f"wc-{owner}", "case_version": version,
                 "part1_v2": {**built["part1_v2"], "source": "handoff", "original_case_id": handoff.get("case_id")}})
    return case
