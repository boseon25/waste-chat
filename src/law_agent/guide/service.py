"""서류 작성 가이드 — 칸 하나를 고르면 그 칸의 법 문장 + 이 건에 맞는 값 (2026.11.19).

  g = guide({"case_id": "...", "doc": "form6", "field": "hauler_name"})

돌려주는 것
  law      : 그 칸에 걸린 법 기준 (기준표 doc_requirements.csv 의 문장·근거 조문·원문 인용)
  kind     : suggest_value (우리 판단에서 값 제안) / need_from (받아야 할 값 — 지어내지 않음)
             / write_text (글 칸 — 이 건의 사실을 참고로) / info (안내만)
  suggest  : {value, why, source}  — kind 가 suggest_value 일 때
  need_from: 누구에게 받아야 하는지 — kind 가 need_from 일 때
  facts    : 글 칸에 참고할 이 건의 사실 (성상, 오염, 보관 상태 …) — kind 가 write_text 일 때
AI 를 쓰지 않는다 (같은 입력이면 같은 결과). 법 문장은 기준표 원문 인용 그대로만 보여준다.
칸 이름 대응은 서류 검토와 같은 표(config/review_fields/*.yaml)를 쓴다.
"""
from __future__ import annotations

import re
from statistics import mean
from typing import Any

from law_agent.review import context, field_map
from law_agent.review.service import requirements
from law_agent.review.summaries import DUTY, REVIEWED, SUMMARY

ROLE_KO = {"generator": "배출업체", "hauler": "운반업체", "receiver": "처리업체"}
PROCESSOR_ROLES = ("preprocessor", "recycler", "treatment")

# 칸 이름 끝부분으로 정하는 규칙 (generator_/hauler_/receiver_ 공통)
NEED_SUFFIX = {
    "registration": "사업자등록증", "representative": "사업자등록증", "birthdate": "대표자 확인",
    "phone": "업체 연락처", "industry": "사업자등록증", "technical_capacity": "업체 회신",
    "permit_number": "허가증(신고증명서)", "permit_date": "허가증(신고증명서)", "permit_authority": "허가증(신고증명서)",
    "permit_industry": "허가증(신고증명서)", "permit_waste": "허가증(신고증명서)", "facility_type": "허가증·업체 회신",
    "facility_capacity": "허가증·업체 회신", "vehicle_size": "업체 회신", "vehicle_count": "업체 회신",
    "acceptance": "업체 회신 (수탁 가능 여부는 업체가 확인)", "method": "허가증 (허가된 처리방법)",
}
# 문서마다 다른 이름이지만 뜻이 같은 칸
SAME = {
    "designated_waste_code": "waste_classification", "designated_transporter": "hauler_name",
    "designated_processor": "receiver_name", "designated_treatment_method": "treatment_method",
    "designated_treatment_mode": "treatment_type", "designated_tons_year": "annual_tons",
    "designated_kg_month": "kg_month", "designated_state": "state_text",
    "receiver_entrusted_annual_tons": "annual_tons", "hauler_entrusted_annual_tons": "annual_tons",
    "treatment_amount": "annual_tons", "haul_amount": "annual_tons", "contract_quantity": "contract_quantity",
    "designated_transport_tons_year": "annual_tons", "designated_treatment_tons_year": "annual_tons",
}
# 칸 지정 방식 서식(제5호 수탁확인 · 제14호의4)의 짧은 칸 이름 (2026.11.19)
SAME.update({"contractor": "receiver_name", "acceptance_method": "treatment_method", "acceptance_tons_year": "annual_tons",
             "quantity": "annual_tons", "hazard_waste_code": "waste_classification", "address": "generator_address"})
LOCAL_NEED = {
    "registration": "배출업체의 사업자등록증", "representative": "배출업체의 사업자등록증", "birth": "배출업체 대표자 확인",
    "generator": "배출업체 (입력서의 상호)", "company": "배출업체 (입력서의 상호)", "contact": "배출업체 담당자",
    "contractor_reg": "처리업체의 사업자등록증", "contractor_rep": "처리업체의 사업자등록증",
    "contractor_birth": "처리업체 대표자 확인", "contractor_industry": "처리업체 (한국표준산업분류 대분류)",
    "acceptance_facility": "처리업체의 허가증 (처분·재활용 시설)", "acceptance_schedule": "처리업체와 협의 (톤/회, 회/년)",
    "waste_category": "배출업체 (대기·수질 배출시설 허가·신고 여부로 1·2 구분)",
    "hazard_properties": "폐기물분석전문기관 분석결과 또는 근거 자료 (추정해 적지 않음)",
    "hazard_leaching": "폐기물분석전문기관 분석결과 (처리계획 확인 때 분석한 결과)",
    "hazard_reactive": "분석결과 또는 근거 자료 (리튬·나트륨·알루미늄 등 포함 여부)",
}
WRITE_TEXT_LOCAL = {"process", "packaging", "handling_protection", "storage_method", "emergency_measures"}
INFO_LOCAL = {"record", "date"}
WRITE_TEXT = {"precautions", "waste_state", "manufacturing_process", "raw_materials_annual", "products_annual",
              "rejection_terms", "designated_cycle", "process_steps", "generation_process"}
INFO = {"contract_period", "transport_price", "processing_price", "payer", "contract_signed_date",
        "pickup_location", "contract_destination", "business_start_date", "submit_date", "industrial_park",
        "designated_industry", "generator_industry", "medical_facility_type"}


def _company(proposal: dict, roles: tuple[str, ...]) -> dict | None:
    for c in proposal.get("partner_candidates") or []:
        if c.get("role") in roles:
            return c
    return None


def _monthly(case: dict) -> float | None:
    vals = [v for v in ((case.get("quantity") or {}).get("monthly_ton") or []) if isinstance(v, (int, float))]
    return round(mean(vals), 3) if vals else None


def _code(case: dict) -> str | None:
    cls = case.get("classification") or {}
    return cls.get("selected_code") or cls.get("reported_code")


def _road(c: dict) -> str:
    d = c.get("distance_km") or {}
    km = d.get("road_km") or d.get("road_est") or d.get("value")
    return f", 도로 {km}km" if km is not None else ""


def _suggest(key: str, case: dict, proposal: dict) -> dict | None:
    """우리 판단에서 이 칸 값. 판단이 없으면 None (추측하지 않음)"""
    k = SAME.get(key, key)
    mon = _monthly(case)
    liquid = (case.get("material_form") or "") == "액상"
    proc = next((s for s in proposal.get("steps") or [] if s.get("role") in PROCESSOR_ROLES), {})
    if k == "waste_classification" and _code(case):
        return {"value": _code(case), "why": "파트 1에서 확인한 분류번호 (법률 판단 A-1에서 별표 4와 대조)",
                "source": "사례 분류번호"}
    if k == "waste_legal_name" and _code(case):
        try:
            from law_agent.tools.fact_tools import waste_code_name
            name = waste_code_name(_code(case))
        except Exception:                               # noqa: BLE001
            name = None
        return {"value": name, "why": f"분류번호 {_code(case)}의 시행규칙 별표 4 명칭", "source": "별표 4"} if name else None
    if k == "waste_state_code":
        return {"value": "2" if liquid else "1", "why": f"성상 '{case.get('material_form') or '-'}' → 고체 1 / 액체 2",
                "source": "사례 성상"}
    if k == "state_text":
        return {"value": "액상" if liquid else "고상", "why": f"성상 '{case.get('material_form') or '-'}'", "source": "사례 성상"}
    if k == "treatment_type":
        return {"value": "위탁", "why": "추천 경로가 업체 위탁(중간 가공)", "source": "법률 판단 경로"}
    if k == "treatment_method" and proc.get("treatment_codes"):
        return {"value": proc["treatment_codes"][0], "why": f"경로의 처리방법 코드 ({proc.get('process') or '중간 가공'})",
                "source": "법률 판단 경로"}
    if k in ("receiver_name", "receiver_address"):
        c = _company(proposal, PROCESSOR_ROLES)
        if c:
            v = c.get("name") if k == "receiver_name" else c.get("address")
            return {"value": v, "why": f"추천 처리업체 1위{_road(c)}", "source": "법률 판단 업체 추천",
                    "company_id": c.get("company_id")} if v else None
    if k in ("hauler_name", "hauler_address"):
        c = _company(proposal, ("transporter",))
        if c:
            v = c.get("name") if k == "hauler_name" else c.get("address")
            return {"value": v, "why": f"추천 운송업체 1위{_road(c)}", "source": "법률 판단 업체 추천",
                    "company_id": c.get("company_id")} if v else None
    if mon is not None:
        if k in ("annual_tons",):
            return {"value": round(mon * 12, 2), "why": f"월 평균 {mon:g}톤 × 12", "source": "사례 월별 발생량"}
        if k == "monthly_tons":
            return {"value": mon, "why": "최근 월별 발생량 평균", "source": "사례 월별 발생량"}
        if k == "kg_month":
            return {"value": round(mon * 1000), "why": f"월 평균 {mon:g}톤", "source": "사례 월별 발생량"}
        if k == "contract_quantity":
            return {"value": f"월 {mon:g}톤", "why": "최근 월별 발생량 평균 (계약 기간·단위는 당사자가 정함)",
                    "source": "사례 월별 발생량"}
    if k == "generator_address":
        addr = (case.get("region") or {}).get("address")
        return {"value": addr, "why": "파트 1 입력서의 사업장 주소", "source": "사례 주소"} if addr else None
    return None


def _kind(key: str) -> tuple[str, str | None]:
    if key in LOCAL_NEED:
        return "need_from", LOCAL_NEED[key]
    if key in WRITE_TEXT_LOCAL:
        return "write_text", None
    if key in INFO_LOCAL:
        return "info", None
    m = re.fullmatch(r"(generator|hauler|receiver)_(.+)", key)
    if m and m.group(2) in NEED_SUFFIX:
        return "need_from", f"{ROLE_KO[m.group(1)]}의 {NEED_SUFFIX[m.group(2)]}"
    if key in WRITE_TEXT:
        return "write_text", None
    if key in INFO:
        return "info", None
    return "suggest_value", None


def _facts(case: dict) -> dict:
    cont = case.get("contamination") or {}
    keep = {"폐기물 이름": case.get("name"), "발생 공정": case.get("process_description"),
            "성상": case.get("material_form"), "오염": cont.get("level"),
            "오염 물질": ", ".join(cont.get("substances") or []) or None,
            "이물질": ", ".join((case.get("foreign_matter") or {}).get("items") or []) or None,
            "가연성": {True: "예", False: "아니오"}.get(case.get("combustible")),
            "보관 상태": case.get("storage_state")}
    return {k: v for k, v in keep.items() if v not in (None, "")}


PARAM_KO = {"waste_codes": "분류번호", "treatment_codes": "처리방법 코드", "ton_year": "연간 물량", "kg_month": "월 물량"}


def _subject(r: dict) -> str:
    """검토 문장 '⑨수탁자 대표자가 없습니다' → '⑨수탁자 대표자' (칸을 콕 집어 말하려고). 못 뽑으면 묶음 이름"""
    m = re.match(r"^(.+)(?:이|가) 없습니다", r.get("message") or "")
    return m.group(1).strip() if m else r["item"]


def _howto(r: dict) -> str:
    """기준표 행 → 작성 안내 문장 (2026.11.19: 칸 이름을 넣어 구체적으로)"""
    item, chk, param = _subject(r), r["check"], r.get("param") or ""
    if chk == "guide":
        return r["message"]
    if chk == "required":
        return f"{item}: 꼭 적어야 합니다"
    if chk == "number":
        return f"{item}: 숫자로 꼭 적어야 합니다"
    if chk == "enum":
        return f"{r['item']}: {' / '.join(param.split('|'))} 중 하나로 적습니다 ({r['message']})"
    if chk == "date_order":
        return f"{r['item']}: 시작일이 끝나는 날보다 앞이어야 합니다"
    if chk == "deadline":
        return f"{r['item']}: 기준일부터 {param.split('|')[-1]}일 안에 내야 합니다"
    if chk == "attachment":
        return f"{r['item']}: {r['message']}"
    if chk == "in_route":
        return f"{r['item']}: 법률 판단 경로의 {PARAM_KO.get(param, param)}와 같아야 합니다"
    if chk == "partner":
        return f"{r['item']}: 추천·예비 업체 중에서 고릅니다 (다른 업체면 허가 범위를 직접 확인)"
    if chk == "approx":
        return f"{r['item']}: 실제 발생량과 크게 다르지 않아야 합니다 (±30%)"
    return r["message"]


# ── 근거 원문 문장 전체 (2026.11.19: 짧은 인용 조각 대신 그 조각이 든 문장을 보여 준다) ──
RX_BOX = re.compile(r"[│┃┌┐└┘├┤┬┴┼─━]+")
RX_END = re.compile(r"(?<=[가-힣\)\]」』])다\.")      # 문장 끝 "…합니다." — 목록 기호 " 다. " 는 앞이 공백이라 빠짐
RX_ITEM = re.compile(r"(?:^|\n)\s*(?:\d+\.|[가-하]\.)\s")
RX_NEXT_HO = re.compile(r"\n\s*\d+\.\s")
_SENT: dict = {}


def _norm_pos(text: str, quote: str) -> tuple[int, int] | None:
    """공백을 무시하고 quote 가 text 어디에 있는지 (시작, 끝)"""
    pat = r"\s*".join(re.escape(ch) for ch in quote if not ch.isspace())
    m = re.search(pat, text) if pat else None
    return (m.start(), m.end()) if m else None


def sentence(basis: str, quote: str, store=None) -> str | None:
    """근거 키의 법 원문에서 quote 가 든 문장(작성방법 한 항목) 전체. 법령 데이터가 없거나 못 찾으면 None"""
    k = (basis, quote)
    if k in _SENT:
        return _SENT[k]
    out = None
    try:
        from law_agent.tools import law_tools
        st = store or law_tools.get_store()
        law, rest = basis.split(":", 1)
        top = rest.split("@")[0].split("/")[0]
        c = st.get(law, top)
        if c is not None:
            raw = c.text + "\n".join(ch.text for ch in st.children(law, top))
            text = re.sub(r"[ \t]+", " ", RX_BOX.sub(" ", raw))
            pos = _norm_pos(text, quote)
            if pos:
                a, b = pos
                ends = [m.end() for m in RX_END.finditer(text)]
                # 왼쪽: 바로 앞 문장 끝(…다.) 또는 번호 항목 시작("\n 3." / "가.")까지
                left = max([e for e in ends if e <= a] or [0])
                mnum = list(RX_ITEM.finditer(text[:a]))
                if mnum:
                    left = max(left, mnum[-1].start())
                # 오른쪽: 다음 문장 끝(…다.) — 목록 기호 "다." 는 문장 끝이 아님. 다음 호("\n 4.")가 먼저 오면 거기까지
                right = min([e for e in ends if e >= b] or [len(text)])
                nxt = RX_NEXT_HO.search(text, b)
                if nxt and nxt.start() < right:
                    right = nxt.start()
                if right - b > 400:
                    right = b
                sent = re.sub(r"\s+", " ", text[left:right]).strip()
                if 0 < len(sent) <= 500 and len(sent) >= len(quote.strip()):
                    out = sent
    except Exception:                                   # noqa: BLE001 — 법령 데이터 없는 환경
        out = None
    _SENT[k] = out
    return out


def _law(m: dict, key: str, rows: list[dict]) -> list[dict]:
    """이 칸에 걸린 기준표 행 (검토 칸 위치가 같거나, 나눠 읽는 칸이면 그 대상)"""
    paths = {p for wc, p in (m.get("fields") or {}).items() if wc == key}
    for d in m.get("derive") or []:
        src = d["from"] if isinstance(d["from"], list) else [d["from"]]
        if key in src:
            paths |= set(d["to"])
    norm = {re.sub(r"\[\d*\]", "[]", p) for p in paths}
    out, seen = [], set()
    for r in rows:
        if r["doc_type"] != m["doc_type"]:
            continue
        fs = set(re.sub(r"\[\d*\]", "[]", x) for x in (r["field"] or "").split("|") if x)
        ps = set(re.sub(r"\[\d*\]", "[]", x) for x in (r.get("param") or "").split("|") if "." in x or "_" in x)
        if not ((fs | ps) & norm):
            continue
        key2 = (r["message"], r["quote"])
        if key2 in seen:
            continue
        seen.add(key2)
        out.append({"req_id": r["req_id"], "check": r["check"], "severity": r["severity"], "text": _howto(r),
                    "problem_text": r["message"],
                    "basis": r["basis_key"], "quote": r["quote"],
                    "summary": r.get("summary") or SUMMARY.get(r["quote"]) or "",
                    "sentence": r.get("sentence") or sentence(r["basis_key"], r["quote"]),
                    "status": "confirmed" if r.get("status") == "confirmed" else "draft",
                    **({"allowed": r["param"].split("|")} if r["check"] == "enum" else {})})
    return out


def guide(req: dict, box=None, rows: list[dict] | None = None) -> dict:
    doc, key = str(req.get("doc") or ""), str(req.get("field") or "")
    m = field_map.load(doc)
    if m is None:
        return {"doc": doc, "field": key, "error": f"안내하지 않는 서류입니다 (가능: {', '.join(field_map.supported())})"}
    found = context.find(str(req.get("case_id") or ""), box) or {"proposals": [], "case": {}, "case_version": None}
    proposal = context.pick(found["proposals"], req.get("route_id"))
    case = found["case"] or {}
    kind, need = _kind(key)
    out = {"doc": doc, "field": key, "case_id": case.get("case_id") or req.get("case_id"),
           "duty": DUTY.get(m["doc_type"], ""), "summary_reviewed": REVIEWED,
           "case_version": found["case_version"], "kind": kind,
           "law": _law(m, key, rows if rows is not None else requirements()),
           "suggest": None, "need_from": need, "facts": None, "notes": []}
    if not case:
        out["notes"].append("이 사례의 법률 판단 결과를 찾지 못해 법 기준만 안내합니다")
    if kind == "suggest_value":
        out["suggest"] = _suggest(key, case, proposal) if case else None
        if case and out["suggest"] is None:
            out["notes"].append("이 칸은 판단 결과로 채울 값이 없습니다. 직접 확인해 적어 주세요")
    elif kind == "write_text":
        out["facts"] = _facts(case) or None
        out["notes"].append("아래 사실을 참고해 직접 적어 주세요. 법 기준에 맞는지는 공유 전 검토에서 확인합니다")
    elif kind == "need_from":
        out["notes"].append("지어내지 않고 받은 자료 그대로 적어야 하는 칸입니다")
    if any(x["status"] == "draft" for x in out["law"]):
        out["notes"].append("법 기준표가 초안입니다 (원문 대조 확정 전)")
    return out


def guide_all(req: dict, box=None, rows: list[dict] | None = None) -> dict:
    """서류 하나의 모든 칸 안내를 한 번에 {doc, case_id, fields: {칸: 안내}} (waste-chat 이 화면 열 때 한 번 부름)"""
    doc = str(req.get("doc") or "")
    m = field_map.load(doc)
    if m is None:
        return {"doc": doc, "error": f"안내하지 않는 서류입니다 (가능: {', '.join(field_map.supported())})"}
    rows = rows if rows is not None else requirements()
    keys = list(m.get("fields") or {})
    for d in m.get("derive") or []:
        keys += [k for k in (d["from"] if isinstance(d["from"], list) else [d["from"]]) if k not in keys]
    fields = {k: guide({**req, "field": k}, box, rows) for k in keys}
    first = next(iter(fields.values()), {})
    return {"doc": doc, "case_id": first.get("case_id"), "case_version": first.get("case_version"), "fields": fields}
