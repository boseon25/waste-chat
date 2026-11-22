"""서류 작성 에이전트.

단순히 사용자 말을 칸에 옮기는 데서 끝나지 않고, 한 차례마다 다음을 한다.
  1) 상황 파악   서식의 빈칸·담당 업체·거래방의 다른 서류·현재 검토 결과를 모은다.
  2) 근거 찾기   질문과 선택한 칸으로 법령 원문(조문·별표·별지서식)을 검색한다. 서식별 핵심 조문은 항상 포함한다.
  3) 판단·제안   모델이 답변·칸 값·인용·다음 할 일을 JSON으로 낸다. 모델이 없으면 규칙 기반으로 처리한다.
  4) 검증        제안 값을 형식·법정 표(별표 4 분류번호 등)·PDF 칸 크기로 확인하고, 통과한 값만 돌려준다.
  5) 다음 행동   우리 몫의 다음 빈칸을 고르거나, 다른 업체 몫이면 거래방에 보낼 요청문을 만든다.
검토(review)는 법정 필수 항목 규칙(engine/tables/doc_requirements.csv)과 서류 간 일관성·분류번호 표로 한다. 모델을 쓰지 않는다.
"""
import csv
import importlib.util
import threading
import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from . import advisor, catalog, conversation, envfile, lawbase, paths, pdf_writer, tables

ROOT = Path(__file__).resolve().parents[1]
PDF_LOCK = threading.Lock()  # app.py가 서식 출력 잠금으로 바꿔 끼운다.

# 서식별로 늘 함께 보는 조문(조각 id 앞부분). 검색 결과에 더해 프롬프트에 넣는다.
DOC_BASIS = {
    "contract": ["WMA_RULE:16의13", "WMA:17/항1", "WMA:15의2"],
    "form5_b": ["WMA_RULE:BF5", "WMA_RULE:16의13"],
    "form5_c": ["WMA_RULE:BF5", "WMA_RULE:16의13"],
    "designated_acceptance": ["WMA_RULE:BF5", "WMA_RULE:18의2"],
    "form6": ["WMA_RULE:18", "WMA_RULE:BF6", "GUIDE_DECL:§Ⅳ"],
    "dplan": ["WMA_RULE:18의2", "WMA_RULE:BF12", "GUIDE_DECL:§Ⅴ"],
    "analysis": ["WMA_RULE:BF13", "WMA:17의2"],
    "hazard": ["WMA_RULE:BF14의4", "WMA:17의2"],
    "intake": ["WMA_RULE:B4", "WMA_DEC:B1"],
    "rfq": ["WMA:17/항1", "WMA_RULE:16의13"],
    "process_note": ["WMA_RULE:B4", "WMA_DEC:B1"],
    "analysis_request": ["WMA:17의2", "WMA_DEC:B1", "WMA_RULE:B1"],
    "sampling_record": ["WMA:17의2", "WMA_RULE:B1"],
    "classify_query": ["WMA_RULE:B4", "WMA_DEC:B1", "WMA:2/호4"],
    "settlement": ["WMA:18/항3", "ALLBARO_NOTICE"],
    "ledger": ["WMA:36", "WMA_RULE:58"],
}
ROLE_OF_PREFIX = {"generator": "emitter", "hauler": "hauler", "receiver": "processor"}
ROLE_KO = {"emitter": "배출업체", "hauler": "운반업체", "processor": "처리업체"}
CODE_FIELDS = {"waste_classification", "designated_waste_code"}
SAME_ACROSS = ("generator_name", "generator_registration", "generator_address", "generator_representative",
               "hauler_name", "hauler_registration", "receiver_name", "receiver_registration",
               "waste_classification", "waste_name", "waste_state", "waste_state_code", "treatment_method")


# ── 법정 필수 항목 검토(share의 doc_review 모듈을 그대로 사용) ─────────────
def _review_module():
    spec = importlib.util.spec_from_file_location("wc_doc_review", ROOT / "engine" / "doc_review" / "review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REVIEW = _review_module()
with (paths.TABLES / "doc_requirements.csv").open(encoding="utf-8-sig") as _f:
    RULES = list(csv.DictReader(_f))


def _num(text):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return float(m.group().replace(",", "")) if m else None


def _ton_year(text):
    """'월 3톤' → 36, '연 40톤' → 40, '250kg/월' → 3. 기간을 알 수 없으면 None."""
    if not text:
        return None
    n = _num(text)
    if n is None:
        return None
    t = text.replace(" ", "")
    if re.search(r"kg|킬로", t, re.I):
        n /= 1000
    if re.search(r"월|/월|매달|한달", t):
        return round(n * 12, 3)
    if re.search(r"연|년|/년|매년", t):
        return n
    return None


def _dates(text):
    found = re.findall(r"(\d{4})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})", text or "")
    out = []
    for y, m, d in found:
        try:
            out.append(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            pass
    return out


def _state_word(v):
    return {"1": "1", "2": "2", "기타": "기타", "고상": "1", "고체": "1", "액상": "2", "액체": "2"}.get((v or "").strip(), v)


def _build(doc_id, v, ctx):
    """waste-chat 칸 값 → doc_review 입력. (doc_type, fields, attachments, 경로→칸 키)"""
    if doc_id == "contract":
        period = _dates(v.get("contract_period"))
        fee = " / ".join(x for x in (v.get("transport_price"), v.get("processing_price")) if x)
        fields = {
            "consignor": {"name": v.get("generator_name")},
            "consignee": {"name": v.get("receiver_name")},
            "waste_items": [{"waste_code": v.get("waste_classification"),
                             "quantity_ton_year": _ton_year(v.get("contract_quantity")),
                             "properties": v.get("waste_state"), "handling_cautions": v.get("precautions"),
                             "treatment_code": None}],
            "contract": {"start_date": period[0] if period else None, "end_date": period[1] if len(period) > 1 else None,
                         "fee": fee or None, "signed_date": "서명 시 기재"},
        }
        att = ["capacity_check"] if ctx.get("form5_ready") else []
        keys = {"consignor.name": "generator_name", "consignee.name": "receiver_name",
                "waste_items[0].waste_code": "waste_classification", "waste_items[0].quantity_ton_year": "contract_quantity",
                "waste_items[0].properties": "waste_state", "waste_items[0].handling_cautions": "precautions",
                "contract.start_date": "contract_period", "contract.end_date": "contract_period",
                "contract.fee": "processing_price"}
        return "entrust_contract", fields, att, keys
    if doc_id in ("form5_b", "form5_c"):
        p = "hauler" if doc_id == "form5_b" else "receiver"
        fields = {
            "consignor": {"name": v.get("generator_name"), "biz_no": v.get("generator_registration"),
                          "representative": v.get("generator_representative"), "address": v.get("generator_address")},
            "waste_items": [{"waste_code": v.get("waste_classification"), "state": v.get("waste_state_code"),
                             "ton_year": _num(v.get(f"{p}_entrusted_annual_tons"))}],
            "consignee": {"name": v.get(f"{p}_name"), "biz_no": v.get(f"{p}_registration"),
                          "address": v.get(f"{p}_address"), "permit_no": v.get(f"{p}_permit_number"),
                          "permit_agency": v.get(f"{p}_permit_authority")},
            "capacity": {"acceptable": v.get(f"{p}_acceptance")},
        }
        att = [a for a, ok in (("permit_copy", ctx.get(f"permit_{'hauler' if p == 'hauler' else 'processor'}")),
                               ("guarantee_copy", ctx.get("bond"))) if ok]
        keys = {"consignor.name": "generator_name", "consignor.biz_no": "generator_registration",
                "consignor.representative": "generator_representative", "consignor.address": "generator_address",
                "waste_items[0].waste_code": "waste_classification", "waste_items[0].state": "waste_state_code",
                "waste_items[0].ton_year": f"{p}_entrusted_annual_tons", "consignee.name": f"{p}_name",
                "consignee.biz_no": f"{p}_registration", "consignee.address": f"{p}_address",
                "consignee.permit_no": f"{p}_permit_number", "consignee.permit_agency": f"{p}_permit_authority",
                "capacity.acceptable": f"{p}_acceptance"}
        return "capacity_check", fields, att, keys
    if doc_id == "dplan":
        mode = (v.get("designated_treatment_mode") or "").strip()
        fields = {
            "submitter": {"name": v.get("generator_name"), "biz_no": v.get("generator_registration"),
                          "address": v.get("generator_address")},
            "business": {"industry": v.get("designated_industry"), "main_materials": v.get("raw_materials_annual"),
                         "main_products": v.get("products_annual"), "process": v.get("manufacturing_process")},
            "waste_items": [{"waste_code": v.get("designated_waste_code"), "kg_month": _num(v.get("designated_kg_month")),
                             "ton_year": _num(v.get("designated_tons_year")), "cycle": v.get("designated_cycle"),
                             "state": {"1": "고상", "2": "액상"}.get(_state_word(v.get("designated_state")), v.get("designated_state")),
                             "transporter": v.get("designated_transporter"),
                             "treatment_type": "위탁" if "위탁" in mode else ("자가" if "자가" in mode else mode or None),
                             "treatment_company": v.get("designated_processor"),
                             "treatment_code": v.get("designated_treatment_method")}],
        }
        att = [a for a, ok in (("capacity_check", ctx.get("form5_ready")), ("analysis_result", ctx.get("analysis"))) if ok]
        keys = {"submitter.name": "generator_name", "submitter.biz_no": "generator_registration",
                "submitter.address": "generator_address", "business.industry": "designated_industry",
                "business.main_materials": "raw_materials_annual", "business.main_products": "products_annual",
                "business.process": "manufacturing_process", "waste_items[0].waste_code": "designated_waste_code",
                "waste_items[0].kg_month": "designated_kg_month", "waste_items[0].ton_year": "designated_tons_year",
                "waste_items[0].cycle": "designated_cycle", "waste_items[0].state": "designated_state",
                "waste_items[0].transporter": "designated_transporter",
                "waste_items[0].treatment_type": "designated_treatment_mode",
                "waste_items[0].treatment_company": "designated_processor",
                "waste_items[0].treatment_code": "designated_treatment_method"}
        return "treatment_plan", fields, att, keys
    return None


def field_basis(doc_id):
    """칸별 법적 근거(조각 id 목록). 검토 규칙이 있는 서식은 규칙의 근거를 쓴다."""
    built = _build(doc_id, {}, {})
    out = {}
    if built:
        doc_type, _, _, keys = built
        for r in RULES:
            if r["doc_type"] != doc_type or not r["field"]:
                continue
            path = r["field"].replace("[]", "[0]")
            key = keys.get(path)
            if key:
                basis = r["basis_key"].split("@")[0]
                out.setdefault(key, [])
                if basis not in out[key]:
                    out[key].append(basis)
    return out


def from_facts(doc_id, facts):
    """법령 판단에 쓴 사실 → 이 서식의 칸 값. 서식 작성방법이 정한 변환(고체 1·액체 2)만 한다."""
    doc = catalog.DOCS.get(doc_id) or {}
    keys = {f["key"] for f in doc.get("fields", [])}
    out = {}
    code = (facts.get("code") or "").strip()
    if code:
        for k in CODE_FIELDS & keys:
            out[k] = code
    for k, fact in (("waste_name", "name"), ("generation_process", "process"), ("waste_state", "form"),
                    ("contamination_detail", "contamination_detail")):
        if k in keys and facts.get(fact):
            out[k] = facts[fact]
    ton = facts.get("monthly_ton")
    if ton:
        for k in ("contract_quantity", "quantity_period"):
            if k in keys:
                out[k] = f"월 {ton}톤"
    form = facts.get("form", "")
    if "waste_state_code" in keys and form:
        if re.search(r"액상|액체|폐액|오일", form):
            out["waste_state_code"] = "2"
        elif re.search(r"고상|고체|조각|필름|분말|슬러지|덩어리|칩", form):
            out["waste_state_code"] = "1"
    return out


def _cite(basis_key):
    cid = basis_key.split("@")[0]
    r = lawbase.get(cid)
    law, _, key = cid.partition(":")
    return {"id": cid, "label": r["label"] if r else lawbase.label(law, key)}


def _norm_name(s):
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s", "", unicodedata.normalize("NFKC", s or "")).lower()


def review(doc_id, values, ctx):
    """작성본 검토. findings: [{key, label, severity(error|warning|info), message, basis:{id,label}, quote}]"""
    doc = catalog.DOCS.get(doc_id) or {}
    labels = {f["key"]: f["label"] for f in doc.get("fields", [])}
    v = {k: (values.get(k) or "").strip() for k in labels}
    findings = []

    def add(key, severity, message, basis=None, quote=""):
        findings.append({"key": key if key in labels else None, "label": labels.get(key, ""), "severity": severity,
                         "message": message, "basis": _cite(basis) if basis else None, "quote": quote})

    built = _build(doc_id, v, ctx)
    verdict_note = []
    if built:
        doc_type, fields, att, keys = built
        judged = ctx.get("engine") or {}
        proposal = dict(judged.get("proposal") or {})
        proposal["partner_candidates"], proposal["reserve_candidates"] = [], []  # 거래 상대는 앱에서 정했다
        out = _REVIEW.review({"doc_type": doc_type, "fields": fields, "attachments": att},
                             proposal, judged.get("case") or {}, RULES)
        for f in out["findings"]:
            if f["req_id"] in ("EC-11",):  # 서명일은 출력본에 손으로 적는다
                continue
            field = (f.get("field") or "").replace("[]", "[0]")
            key = keys.get(field)
            msg = f["message"]
            if f["problem"] == "missing" and field == "attachments":
                msg += " (거래방 서류 탭에서 해당 항목을 완료하면 첨부된 것으로 봅니다)"
            add(key, f["severity"], msg, f.get("basis"), f.get("quote", ""))
        verdict_note += out.get("notes", [])
    # 분류번호는 별표 4 표와 대조
    for key in CODE_FIELDS & set(labels):
        if v.get(key):
            ok, msg = tables.check_code(v[key])
            if not ok:
                add(key, "error", msg, "WMA_RULE:B4")
            else:
                row = tables.code(v[key])
                if row and row.get("지정폐기물") == "Y" and not ctx.get("flags", {}).get("designated"):
                    add(key, "warning", f"{v[key]}는 지정폐기물 분류번호입니다. 거래방 서류 탭에서 ‘지정폐기물’을 켜면 처리계획서 등 필요한 서류가 추가됩니다.",
                        "WMA_DEC:B1")
    # 거래방 참여 업체와 상호 대조
    members = ctx.get("members", {})
    for prefix, role in ROLE_OF_PREFIX.items():
        key = prefix + "_name"
        if key in labels and v.get(key) and members.get(role):
            names = [_norm_name(n) for n in members[role]]
            if _norm_name(v[key]) not in names:
                add(key, "warning", f"거래방의 {ROLE_KO[role]}({', '.join(members[role])})와 상호가 다릅니다. 사업자등록증의 상호와 같게 적어야 합니다.")
    # 다른 서류와 같은 칸이 다르게 적혔는지
    for key in SAME_ACROSS:
        if key not in labels or not v.get(key):
            continue
        for title, other in ctx.get("others", []):
            ov = (other.get(key) or "").strip()
            if ov and _norm_name(ov) != _norm_name(v[key]):
                add(key, "warning", f"‘{title}’에는 ‘{ov}’로 적혀 있습니다. 같은 거래의 서류끼리 값을 맞추세요.")
                break
    # 올린 원본(사업자등록증·허가증 등)에 적힌 값과 대조
    for o in ctx.get("originals", []):
        for k, ov in (o.get("values") or {}).items():
            if k in labels and v.get(k) and ov and _norm_name(v[k]) != _norm_name(ov):
                add(k, "warning", f"올린 원본 ‘{o.get('kind') or o.get('name')}’에는 ‘{ov}’로 적혀 있습니다. 원본과 같게 적었는지 확인하세요.")
    # 날짜 순서(계약기간)
    if v.get("contract_period"):
        ds = _dates(v["contract_period"])
        if len(ds) >= 2 and ds[1] < ds[0]:
            add("contract_period", "error", "계약 종료일이 시작일보다 빠릅니다.", "WMA_RULE:16의13/호3")
    empty = [labels[k] for k in labels if not v.get(k)]
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    counts = {s: sum(f["severity"] == s for f in findings) for s in order}
    verdict = "fix" if counts["error"] else "check" if counts["warning"] else "pass"
    return {"verdict": verdict, "counts": counts, "findings": findings, "empty": empty,
            "rules": bool(built), "notes": verdict_note}


# ── 값 확인 ────────────────────────────────────────────────────────────
PLACEHOLDER = re.compile(r"^\(?(확인\s*필요|확인\s*중|미정|모름|없음|추후\s*\S*|미확인|미입력|미기재|해당\s*없음|-+|N/?A|TBD)\)?\.?$", re.I)


def _kind_ok(key, value):
    """칸 이름으로 정한 값의 모양: 날짜·사업자등록번호·전화·숫자 칸에 엉뚱한 말이 들어가지 않게."""
    v = value.strip()
    digits = re.sub(r"\D", "", v)
    if key.endswith(("_birthdate", "_date")) or key in ("permit_date",):
        if not re.search(r"\d{2,4}\s*[.\-/년]\s*\d{1,2}", v) and not re.fullmatch(r"\d{6}|\d{8}", digits or "x"):
            return False, "날짜를 적는 칸입니다. 예: 1980-05-12"
    if key.endswith("_registration") and len(digits) != 10:
        return False, "사업자등록번호는 숫자 10자리입니다. 예: 123-45-67890"
    if key.endswith("_phone") and (not re.fullmatch(r"0\d{8,10}", digits) or re.search(r"\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]", v)):
        return False, "전화번호를 적는 칸입니다(0으로 시작하는 9~11자리). 예: 031-123-4567"
    if key.endswith(("_tons", "_count", "_size", "_capacity", "_amount")) and not re.search(r"\d", v):
        return False, "숫자로 적는 칸입니다."
    return True, ""


def validate(doc, key, value):
    """(통과 여부, 이유). 법정 표·형식·PDF 칸 크기로 확인한다."""
    if PLACEHOLDER.match(value.strip()):
        return False, "‘확인 필요’ 같은 자리표시 말은 칸에 넣지 않습니다. 실제 값을 확인한 뒤 적어 주세요."
    ok, why = _kind_ok(key, value)
    if not ok:
        return False, why
    if key in CODE_FIELDS:
        ok, msg = tables.check_code(value)
        if not ok:
            return False, msg
    if key.endswith("waste_state_code"):
        allowed = {"1", "2"} if doc["id"] == "form6" else {"1", "2", "기타"}
        if value not in allowed:
            return False, f"성질·상태 코드는 {', '.join(sorted(allowed))} 중 하나로 적습니다(고체 1, 액체 2)."
    if key.endswith("_acceptance") and value not in ("가능", "불가능"):
        return False, "수탁 가능 여부는 ‘가능’ 또는 ‘불가능’으로 적습니다."
    try:
        with PDF_LOCK:
            pdf_writer.render_document(doc, {key: value})
    except ValueError as e:
        return False, str(e).split(": ", 1)[-1]
    return True, ""


def _owner(key, author):
    pre = key.split("_", 1)[0]
    return ROLE_OF_PREFIX.get(pre, author)


def _asked_fields(message, focus, fields):
    """메시지에 이름이 나온 칸(업체 구분 말은 함께 나와야 한다)과 선택한 칸."""
    text = re.sub(r"\s", "", message or "")
    out = [f for f in fields if f["key"] == focus]
    for f in fields:
        label = re.sub(r"\s", "", f["label"])
        role_word = next((w for w in ROLE_KO.values() if label.startswith(w)), "")
        core = re.sub(r"\(.*?\)", "", label[len(role_word):])
        if len(core) >= 2 and core in text and (not role_word or role_word in text or role_word[:2] in text) and f not in out:
            out.append(f)
    return out[:8]


def _mentioned(message, focus, labels, theirs):
    """메시지나 선택한 칸이 다른 업체 몫 칸을 가리키면 (역할, 칸들)."""
    text = re.sub(r"\s", "", message or "")
    for role, keys in theirs.items():
        if focus in keys:
            return role, [focus]
        word = ROLE_KO[role]
        if word not in text and word.replace("업체", "") not in text:
            continue
        hit = [k for k in keys if re.sub(r"\s", "", labels[k].replace(word, "")) and
               re.sub(r"\s", "", labels[k].replace(word, "")) in text]
        if hit:
            return role, hit
    return None


def _tidy(key, value):
    """칸 값 정리. 분류번호는 번호만 꺼내고, 끝에 붙은 요청 말투는 뗀다."""
    if key in CODE_FIELDS:
        m = re.search(r"\d{2}-\d{2}-\d{2}", value)
        if m:
            return m.group()
    return re.sub(r"\s*(?:(?:으로|로|라고|이라고)\s*)?(?:넣어|적어|입력해|써|해)\s*(?:줘|주세요|줄래|주라)?[.!]?$", "", value).strip() or value


QUESTION = re.compile(r"\?|뭐|무엇|무슨|어디|누가|언제|어떻게|왜|되나|돼\?|인가|일까|알려|설명|근거|법")


def turn(doc_id, message, values, focus, history, ctx):
    """한 차례. 반환: {reply, updates, rejected, citations, related, ask, next, mode}"""
    doc = catalog.DOCS[doc_id]
    fields = doc["fields"]
    labels = {f["key"]: f["label"] for f in fields}
    focus = focus if focus in labels else None
    me_role = ctx["me_role"]
    author = ctx.get("author_role") or me_role
    basis = field_basis(doc_id)
    query = " ".join(x for x in (message, labels.get(focus, ""), next((f.get("help", "") for f in fields if f["key"] == focus), ""),
                                 doc["title"]) if x)
    prefer = DOC_BASIS.get(doc_id, []) + (basis.get(focus, []) if focus else [])
    hits = lawbase.tiered(query, prefer=prefer, k_law=6, k_practice=3)
    current = review(doc_id, values, ctx)

    locked = set(ctx.get("locked") or [])
    # 다음에 물을 칸은 코드가 정한다: 우리 몫의 빈칸(잠긴 칸 제외). 질문 모드에서 AI는 이 칸만 묻는다
    ctx = {**ctx, "ask_next": next((f["key"] for f in fields if not (values.get(f["key"]) or "").strip() and f["key"] != focus
                                    and f["key"] not in locked and _owner(f["key"], author) == me_role), None)}
    result = None
    mode = "rules"
    if advisor.ai_ready():
        try:
            result = _ai(doc, message, values, focus, history, ctx, hits, current, basis)
            mode = "ai"
        except (ValueError, KeyError, TypeError):
            result = None
    if result is None:
        result = _rules(doc, message, values, focus, hits, basis)

    # 질문 모드·칸 선택 상태에서 짧은 답(값)만 왔는데 모델이 칸을 못 정했으면, 고른(물은) 칸에 넣는다
    raw_updates = dict(result.get("updates") or {})
    plain_answer = (focus and focus not in locked and len(message.strip()) <= 80 and not QUESTION.search(message)
                    and not re.search(r"모르|몰라|모름|다음|건너|패스|나중|잘\s*모|없어요|없음|확인\s*(중|필요)|물어봐|알려\s*줘", message)
                    and _owner(focus, author) == me_role)
    misfit = None
    if plain_answer:  # 칸을 골라 둔 상태의 짧은 답은 그 칸 값이다 — 단 그 칸의 모양(날짜·번호 등)에 맞을 때만
        cand = raw_updates.get(focus) or message.strip()
        if _kind_ok(focus, _tidy(focus, cand))[0]:
            raw_updates = {focus: cand}
        else:
            misfit = (labels[focus], _kind_ok(focus, cand)[1])
            raw_updates = {k: v for k, v in raw_updates.items() if k != focus}
    # 제안 값 확인
    updates, rejected, skipped = {}, [], []
    for k, val in raw_updates.items():
        if k not in labels or not isinstance(val, str) or not val.strip() or k in locked:
            continue
        # 다른 업체가 채울 칸은, 그 칸을 고르거나 칸 이름을 직접 말했을 때만 넣는다(엉뚱한 칸에 들어가지 않게)
        if _owner(k, author) != me_role and k != focus and labels[k] not in message:
            skipped.append(labels[k])
            continue
        val = _tidy(k, unicodedata.normalize("NFC", val.strip()))
        if (values.get(k) or "") == val:
            continue
        ok, why = validate(doc, k, val)
        if ok:
            updates[k] = val
        else:
            rejected.append({"key": k, "label": labels[k], "value": val, "why": why})
    merged = {**values, **updates}
    # 다음 할 일: 우리 몫 빈칸 → 없으면 다른 업체에 요청
    mine = [f["key"] for f in fields if not (merged.get(f["key"]) or "").strip() and _owner(f["key"], author) == me_role
            and f["key"] not in locked]
    theirs = {}
    for f in fields:
        if not (merged.get(f["key"]) or "").strip():
            owner = _owner(f["key"], author)
            if owner != me_role and owner in ctx.get("members", {}):
                theirs.setdefault(owner, []).append(f["key"])
    nxt = (ctx.get("ask_next") if ctx.get("ask_next") in mine else None) or (mine[0] if mine else None) if ctx.get("interview") \
        else (result.get("next") if result.get("next") in mine else (mine[0] if mine else None))
    ask = None
    raw_ask = result.get("ask") if isinstance(result.get("ask"), dict) else None
    if raw_ask and raw_ask.get("to") in theirs:
        keys = [k for k in raw_ask.get("fields", []) if k in theirs[raw_ask["to"]]] or theirs[raw_ask["to"]]
        ask = {"to": raw_ask["to"], "fields": keys[:10], "message": str(raw_ask.get("message") or "")[:600]}
    elif theirs and _mentioned(message, focus, labels, theirs):
        role, keys = _mentioned(message, focus, labels, theirs)
        ask = {"to": role, "fields": keys[:10], "message": ""}
    elif not mine and theirs:
        role, keys = next(iter(theirs.items()))
        ask = {"to": role, "fields": keys[:10], "message": ""}
    if ask:
        names = ", ".join(ctx["members"].get(ask["to"], []))
        ask["to_name"] = names
        if not ask["message"]:
            ask["message"] = (f"‘{doc['title']}’ 작성 중입니다. {names}에서 확인해 주실 칸: "
                              + ", ".join(labels[k] for k in ask["fields"]) + ". 거래방 채팅으로 알려 주시면 반영하겠습니다.")
    by_tag = {f"L{i + 1}": h for i, h in enumerate(hits)}
    cited = [lawbase.public(by_tag[t]) for t in result.get("cite", []) if t in by_tag]
    cited_ids = {c["id"] for c in cited}
    related = [lawbase.public(h, 600) for h in hits[:4] if h["id"] not in cited_ids][:3]
    reply = re.sub(r"\[(L\d+)\]", lambda m: f"[{cited.index(next(c for c in cited if c['id'] == by_tag[m.group(1)]['id'])) + 1}]"
                   if m.group(1) in by_tag and by_tag[m.group(1)]["id"] in cited_ids else "", result.get("reply") or "")
    for k, lab in sorted(labels.items(), key=lambda x: -len(x[0])):
        reply = re.sub(r"[‘'\"]?\b" + re.escape(k) + r"\b[’'\"]?", "‘" + lab + "’", reply)
    if rejected and not updates and mode == "rules":
        reply = ""
    if rejected:
        reply += "\n\n넣지 않은 값: " + " / ".join(f"‘{r['label']}’ {r['value']} — {r['why']}" for r in rejected)
    if misfit and focus in mine:
        nxt = focus  # 맞지 않는 답이면 같은 칸을 다시 묻는다
    if ctx.get("interview"):
        # 모델 답에서 다른 칸을 묻는 질문을 덜어 내고, 화면이 고를 칸(nxt)에 대한 질문을 코드가 붙인다
        body = re.split(r"(?<=[.!?。])\s+", reply.strip())
        body = [x for x in body if not (x.endswith("?") or re.search(r"(알려|말씀해|적어)\s*주(시겠|실 수|세요)|있나요|되나요", x))]
        reply = " ".join(body).strip()
        if misfit:
            reply = (f"방금 답은 ‘{misfit[0]}’ 칸에 맞지 않아 넣지 않았어요({misfit[1]}). 어느 칸 값인지 알려 주시거나, 그 칸을 직접 눌러 주세요. "
                     + reply).strip()
        nf = next((f for f in fields if f["key"] == nxt), None)
        if nf:
            hint = re.split(r"(?<=[.。])\s", (nf.get("help") or "").strip())[0][:80]
            reply += f"\n\n다음으로 ‘{nf['label']}’을(를) 알려 주시겠어요?" + (f" {hint}" if hint else "")
    elif misfit:
        reply = f"방금 답은 ‘{misfit[0]}’ 칸에 맞지 않아 넣지 않았어요({misfit[1]}). " + reply
    if focus and not updates and re.search(r"모르|몰라|모름|건너|패스|나중", message) and len(message) <= 30:
        nxt_label = labels.get(ctx.get("ask_next") or "", "")
        reply = (reply if len(reply) > 12 else "") or (f"‘{labels[focus]}’ 칸은 비워 두겠습니다. 확인되면 그때 알려 주세요."
                                                        + (f" 다음으로 ‘{nxt_label}’을(를) 알려 주실 수 있나요?" if nxt_label else ""))
    # 실제로 들어간 값은 코드가 알려 준다(모델 문장과 미리보기가 어긋나지 않게)
    if updates:
        reply += "\n\n✓ 미리보기에 넣음: " + " / ".join(f"‘{labels[k]}’ {v}" for k, v in updates.items())
    elif re.search(r"넣었|넣어\s*전달|전달합니다|입력했|반영했", reply):
        reply += "\n\n(이번에는 칸에 들어간 값이 없습니다. 넣을 칸을 고르거나 칸 이름과 값을 함께 말씀해 주세요.)"
    if skipped:
        reply += "\n\n상대 업체가 채울 칸(" + ", ".join(skipped) + ")은 그 칸을 직접 고른 뒤 말씀하실 때만 넣습니다."
    return {"reply": reply.strip() or "확인했습니다.", "updates": updates, "rejected": rejected, "citations": cited,
            "related": related, "ask": ask, "next": nxt, "mode": mode,
            "review": review(doc_id, merged, ctx)}


def _ai(doc, message, values, focus, history, ctx, hits, current, basis):
    fields = doc["fields"]
    author = ctx.get("author_role") or ctx["me_role"]
    rows = []
    for f in fields:
        k = f["key"]
        rows.append({"key": k, "label": f["label"], "담당": ROLE_KO.get(_owner(k, author), ""),
                     "값": values.get(k, ""), "요령": f.get("help", "")[:140],
                     "근거": [_cite(b)["label"] for b in basis.get(k, [])][:2]})
    law = "\n".join(f"[L{i + 1}] <{lawbase.KIND_LABEL[h['kind']]}> {h['label']} ({h.get('title', '')})\n{lawbase.brief(h, 650)}"
                    for i, h in enumerate(hits))
    others = {title: {k: v for k, v in vals.items() if k in {f['key'] for f in fields}}
              for title, vals in ctx.get("others", [])}
    issues = [{"칸": f["label"], "수준": f["severity"], "내용": f["message"]} for f in current["findings"][:10]]
    system = (
        "당신은 폐기물 위탁 거래 서류를 함께 쓰는 작성 에이전트입니다. 사용자는 "
        f"{ctx['me_name']}({ROLE_KO.get(ctx['me_role'], '')})이고, 지금 ‘{doc['title']}’를 작성합니다. {doc.get('description', '')}\n"
        "할 일:\n"
        "1) 사용자의 말에서 칸에 넣을 값을 찾아 updates에 넣습니다. 사용자가 말했거나 [업체 정보]·[다른 서류]에 있는 값만 씁니다. "
        "허가번호·사업자등록번호·날짜·서명·수량을 추측하거나 만들지 않습니다. 단위를 바꾸지 않습니다.\n"
        "2) 질문이면 [근거 자료]로 답하고, 쓴 조각을 [L1]처럼 본문에 표시하고 cite에 넣습니다. 근거 자료는 등급이 있습니다: "
        "<법령>(지켜야 하는 기준) → <행정규칙·지침>(행정기관 업무 기준) → <서식 작성방법>(서식 뒷면의 칸별 작성법) → "
        "<실무 안내>(확인 경로·준비물·주의사항·앱 작성 요령. 법적 구속력 없음). 법령에 직접 규정이 있으면 그것을 먼저 말합니다. "
        "법령에 없지만 하위 등급 자료에 작성법·확인 방법이 있으면 ‘법령에 직접 정한 내용은 없지만 ○○(등급과 자료 이름)에 따르면 …’처럼 "
        "출처 등급을 밝혀 구체적으로 안내합니다(어디서 확인하는지, 무엇을 준비하는지, 어떻게 적는지). 어떤 자료에도 없을 때만 "
        "확인하지 못했다고 말하고 누구에게 물으면 되는지 안내합니다. 조문 번호나 자료를 지어내지 않습니다. "
        "[근거 자료]에 없는 실무 관행(‘보통 이메일로 받는다’, ‘일반적으로’ 등)을 스스로 덧붙이지 않습니다.\n"
        "3) 칸의 담당이 다른 업체면 그 업체가 확인해야 한다고 알리고 ask에 요청을 만듭니다.\n"
        "4) [검토 결과]에 오류가 있으면 가장 중요한 것 하나를 짧게 짚어 줍니다.\n"
        "5) 끝에 다음에 채울 칸 하나를 제안합니다(next).\n"
        + (f"6) 인터뷰 모드입니다. reply에는 방금 답에 대한 확인·설명만 쓰고 다음 질문은 쓰지 않습니다(화면이 [다음에 물을 칸] 질문을 붙입니다). 서식 용어 대신 "
           "현장에서 답할 수 있는 말로 묻고(예: ‘분류번호’ 대신 ‘신고서나 올바로에 적힌 6자리 번호가 있나요?’), 모를 때 어디서 확인하는지 한 줄 덧붙입니다.\n"
           if ctx.get("interview") else "") +
        "JSON 하나로만 답합니다: {\"reply\": 문자열, \"updates\": {칸key: 값}, \"cite\": [\"L1\"], "
        "\"ask\": null 또는 {\"to\": \"emitter|hauler|processor\", \"fields\": [칸key], \"message\": 요청문}, \"next\": 칸key 또는 null}\n"
        "reply는 한국어 존댓말 2~6문장, 마크다운·목록 기호 없이 씁니다. 값을 넣었다는 말은 updates에 실제로 넣은 칸에 대해서만 합니다(화면이 넣은 값을 따로 표시합니다). "
        "[선택한 칸]이 있고 사용자가 값만 짧게 답하면 그 칸에 넣습니다.\n"
        "칸을 말할 때는 영문 key가 아니라 칸 이름(label)을 씁니다. [칸 목록]의 ‘값’이 이미 있는 칸을 물으면 지금 들어 있는 값을 알려 주고, "
        "그 값이 맞는지 담당 업체가 확인하면 된다고 안내합니다. 이미 값이 있는 칸을 비어 있다고 말하지 않습니다.\n"
        f"[거래방 업체] {json.dumps(ctx.get('members', {}), ensure_ascii=False)}\n"
        f"[선택한 칸] {focus or '없음'}\n"
        + (f"[다음에 물을 칸] {next((f['label'] for f in fields if f['key'] == ctx.get('ask_next')), '없음(모두 채움)')} ({ctx.get('ask_next')})\n" if ctx.get("interview") else "")
        + (f"[잠긴 칸 — 묻지도 넣지도 않음, 업체가 정해진 뒤 상대가 채움] {', '.join(f['label'] for f in fields if f['key'] in set(ctx.get('locked') or []))}\n" if ctx.get("locked") else "") +
        f"[칸 목록] {json.dumps(rows, ensure_ascii=False)}\n"
        f"[업체 정보] {json.dumps(ctx.get('profile', {}), ensure_ascii=False)}\n"
        f"[다른 서류] {json.dumps(others, ensure_ascii=False)[:2500]}\n"
        f"[검토 결과] {json.dumps(issues, ensure_ascii=False)}\n"
        f"[근거 자료]\n{law}")
    judged = ctx.get("engine")
    if judged:
        route = (judged.get("routes") or [{}])[0]
        system += "\n[법령 판단 결과 — 파트 2 엔진, 여러 모델 합의와 원문 인용] " + json.dumps({
            "분류번호": judged.get("code"), "구분": "지정폐기물" if judged.get("status") == "designated" else "일반폐기물",
            "경로": route.get("kind") or route.get("title"), "법적상태": route.get("legal"),
            "조건": [c[:160] for c in route.get("conditions", [])][:3], "필요서류": [d["title"] for d in judged.get("docs", [])],
            "추가확인": [q["text"] for q in judged.get("questions", [])][:4]}, ensure_ascii=False)
    asked = _asked_fields(message, focus, fields)
    if asked:
        system += "\n[질문에 나온 칸의 현재 값 — 답변에 반드시 반영] " + json.dumps(
            {f["label"]: (values.get(f["key"]) or "(비어 있음)") for f in asked}, ensure_ascii=False)
    msgs = [{"role": "system", "content": system}]
    for t in (history or [])[-8:]:
        if t.get("role") in ("user", "assistant") and isinstance(t.get("text"), str):
            msgs.append({"role": t["role"], "content": t["text"][:1500]})
    msgs.append({"role": "user", "content": message})
    # 질문(근거 설명)은 정확한 모델, 값 넣기는 빠른 모델
    asking = bool(QUESTION.search(message)) and "=" not in message
    out = advisor._call(msgs, True, envfile.agent_model() if asking else None)
    if not isinstance(out, dict):
        raise ValueError("형식 오류")
    return {"reply": str(out.get("reply") or ""), "updates": out.get("updates") if isinstance(out.get("updates"), dict) else {},
            "cite": [c for c in out.get("cite") or [] if isinstance(c, str)], "ask": out.get("ask"),
            "next": out.get("next") if isinstance(out.get("next"), str) else None}


def _rules(doc, message, values, focus, hits, basis):
    """모델 없이: 질문이면 칸 요령 + 근거 조문, 값이면 기존 규칙 추출."""
    labels = {f["key"]: f["label"] for f in doc["fields"]}
    if QUESTION.search(message) and "=" not in message:
        parts = []
        f = next((x for x in doc["fields"] if x["key"] == focus), None)
        if f:
            parts.append(f"‘{f['label']}’ · {f.get('help', '')}")
        top = hits[:2]
        if top:
            parts.append("관련 법령 원문은 아래 조문을 참고하세요. " + " ".join(f"[L{i + 1}]" for i in range(len(top))))
        return {"reply": "\n".join(parts) or doc.get("description", ""), "updates": {}, "cite": [f"L{i + 1}" for i in range(len(top))]}
    data = {"message": message, "values": {k: v for k, v in values.items() if k in labels}, "focus": focus,
            "pinned_field": focus}
    try:
        out = conversation.update(doc, data, None)
    except ValueError as e:
        return {"reply": str(e), "updates": {}}
    return {"reply": out.get("reply", ""), "updates": out.get("updates", {}), "cite": []}


# ── 검토 결과를 스스로 고치기 ─────────────────────────────────────────
def fix(doc_id, values, ctx, rounds=3):
    """검토 → 고칠 값 찾기 → 검증 → 다시 검토를 반복한다. 근거 있는 값(다른 서류·업체 정보·법령 판단)만 넣고,
    찾을 수 없는 것은 누구에게 무엇을 물어야 하는지 질문으로 남긴다."""
    doc = catalog.DOCS[doc_id]
    labels = {f["key"]: f["label"] for f in doc["fields"]}
    work = dict(values)
    history, questions = [], []
    facts = ctx.get("facts") or {}
    for n in range(rounds):
        rv = review(doc_id, work, ctx)
        targets = [f for f in rv["findings"] if f["key"] and f["severity"] in ("error", "warning")]
        if not targets:
            history.append({"round": n + 1, "fixed": [], "left": 0})
            break
        found = {}
        # 1) 규칙: 법령 판단 사실과 다른 서류에서 같은 칸
        for k, v in from_facts(doc_id, facts).items():
            if any(t["key"] == k for t in targets) and not (work.get(k) or "").strip():
                found[k] = (v, "법령 판단에 쓴 사실")
        for title, other in ctx.get("others", []):
            for t in targets:
                k = t["key"]
                if k not in found and (other.get(k) or "").strip() and not (work.get(k) or "").strip():
                    found[k] = (other[k].strip(), f"‘{title}’")
        # 2) 모델: 남은 문제를 근거 자료에서 찾거나 질문으로
        rest = [t for t in targets if t["key"] not in found]
        if rest and advisor.ai_ready():
            try:
                out = advisor._call([{"role": "system", "content": (
                    "폐기물 서류의 검토 결과를 고치는 작업입니다. 각 문제에 대해 [자료]에 근거가 있는 값만 updates에 넣고, "
                    "없으면 questions에 누구(배출업체/운반업체/처리업체)에게 무엇을 확인해야 하는지 한 문장으로 적습니다. "
                    "값을 지어내지 않고 ‘확인 필요’ 같은 자리표시 말도 넣지 않습니다. 칸의 뜻에 맞는 값만 넣습니다(성상은 고체·액체 같은 물리적 상태, "
                    "오염 정도는 성상이 아님). 형식이 틀린 값은 같은 내용을 올바른 형식으로 바꿉니다(예: 2026년 12월 1일 → 2026-12-01). "
                    "JSON: {\"updates\": {칸key: 값}, \"questions\": [{\"key\": 칸key, \"to\": 업체구분, \"text\": 문장}]}\n"
                    f"[문제] {json.dumps([{'key': t['key'], '칸': t['label'], '내용': t['message'], '현재값': work.get(t['key'], '')} for t in rest], ensure_ascii=False)}\n"
                    f"[자료] 업체 정보 {json.dumps(ctx.get('profile', {}), ensure_ascii=False)} / 법령 판단 사실 {json.dumps(facts, ensure_ascii=False)} / "
                    f"다른 서류 {json.dumps(dict(ctx.get('others', [])), ensure_ascii=False)[:2500]} / 거래방 업체 {json.dumps(ctx.get('members', {}), ensure_ascii=False)}")},
                    {"role": "user", "content": "고칠 수 있는 것은 고치고, 나머지는 질문으로 남겨 주세요."}], True)
                for k, v in (out.get("updates") or {}).items():
                    if k in labels and isinstance(v, str) and v.strip():
                        found.setdefault(k, (v.strip(), "작성 에이전트(자료 대조)"))
                for q in out.get("questions") or []:
                    if isinstance(q, dict) and q.get("key") in labels and q.get("text"):
                        if all(x["key"] != q["key"] for x in questions):
                            questions.append({"key": q["key"], "label": labels[q["key"]], "to": str(q.get("to") or ""), "text": str(q["text"])[:300]})
            except (ValueError, KeyError, TypeError):
                pass
        fixed = []
        for k, (v, src) in found.items():
            v = _tidy(k, v)
            ok, why = validate(doc, k, v)
            if ok and work.get(k) != v:
                work[k] = v
                fixed.append({"key": k, "label": labels[k], "value": v, "source": src})
        history.append({"round": n + 1, "fixed": fixed, "left": len(targets) - len(fixed)})
        if not fixed:
            break
    final = review(doc_id, work, ctx)
    left_keys = {f["key"] for f in final["findings"] if f["key"] and f["severity"] == "error"}
    for f in final["findings"]:
        if f["key"] in left_keys and all(q["key"] != f["key"] for q in questions):
            questions.append({"key": f["key"], "label": f["label"], "to": "", "text": f["message"]})
    updates = {k: v for k, v in work.items() if values.get(k) != v}
    return {"updates": updates, "rounds": history, "questions": questions[:12], "review": final}


# ── 자료(사진·PDF)에서 값 뽑기 ─────────────────────────────────────────
def extract(doc_id, kind, payload, values, ctx):
    """사업자등록증·허가증·이전 서류 같은 자료에서 칸 값을 제안한다. 바로 넣지 않고 사용자가 고른다.
    kind: 'image'(data URL) | 'text'(PDF에서 뽑은 글)."""
    doc = catalog.DOCS[doc_id]
    fields = [{"key": f["key"], "label": f["label"]} for f in doc["fields"]]
    if not advisor.ai_ready():
        raise ValueError("자료 읽기는 AI 연결이 필요합니다. .env의 키를 확인하세요.")
    system = ("업로드한 자료에서 서식 칸에 들어갈 값을 찾습니다. 자료에 실제로 적힌 글자만 그대로 옮기고, 추측·계산·보정하지 않습니다. "
              "자료가 어떤 문서인지(doc_kind)와 발급 주체(issuer)를 적습니다. 업체 구분(배출·운반·처리)에 맞는 칸에만 넣습니다. "
              "JSON: {\"doc_kind\": 문자열, \"issuer\": 문자열, \"values\": {칸key: 값}, \"note\": 확인할 점 한 문장}\n"
              f"[칸] {json.dumps(fields, ensure_ascii=False)}\n[거래방 업체] {json.dumps(ctx.get('members', {}), ensure_ascii=False)}")
    if kind == "image":
        user = [{"type": "text", "text": "이 자료에서 칸 값을 찾아 주세요."}, {"type": "image_url", "image_url": {"url": payload}}]
    else:
        user = "자료 내용:\n" + payload[:12000]
    out = advisor._call([{"role": "system", "content": system}, {"role": "user", "content": user}], True)
    labels = {f["key"]: f["label"] for f in doc["fields"]}
    suggestions = []
    for k, v in (out.get("values") or {}).items():
        if k not in labels or not isinstance(v, str) or not v.strip():
            continue
        v = _tidy(k, unicodedata.normalize("NFC", v.strip()))
        ok, why = validate(doc, k, v)
        suggestions.append({"key": k, "label": labels[k], "value": v, "current": values.get(k, ""), "ok": ok, "why": why})
    return {"doc_kind": str(out.get("doc_kind") or ""), "issuer": str(out.get("issuer") or ""),
            "note": str(out.get("note") or ""), "suggestions": suggestions}
