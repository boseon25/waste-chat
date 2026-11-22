"""waste_agent 파트 2(법령 판단 엔진)와의 연결.

1) 사례 만들기  화면에서 모은 폐기물 사실(품목·분류번호·공정·성상·성분·오염·지정 의심·월 물량·주소)을
                파트 2 입력 계약 1.0 형식의 case로 바꾼다. 비어 있는 사실은 missing에 넣고 추측하지 않는다.
2) 판단 실행    별도 프로세스(python -m law_agent.bridge.run_one)로 돌린다. 서로 다른 회사 모델 2~3개가 질문 30개를
                법령 원문 인용과 함께 판단하고, 관문별 경로·업체 순위·운송 탄소·필요한 서류를 만든다. 수십 초 걸리므로
                백그라운드 작업으로 두고 화면은 진행 로그를 읽는다.
3) 결과 정리    화면에서 쓰는 모양(경로·업체·서류·추가 질문·질문별 판단)으로 줄인다.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import envfile, lawbase, paths, store, tables

ROOT = Path(__file__).resolve().parents[1]
AGENT = paths.AGENT
PY = sys.executable
_LOCK = threading.Lock()
_RUNNING = {}  # job_id → Popen
FACT_KEYS = ("name", "code", "process", "form", "components", "contamination", "contamination_detail",
             "hazard", "monthly_ton", "address", "industry", "facility_type", "mixture", "separable", "analysis")
FACT_LABELS = {
    "name": "폐기물 이름", "code": "분류번호(00-00-00)", "process": "발생 공정", "form": "현재 상태(형태)",
    "components": "구성 성분과 비율", "contamination": "오염 여부", "contamination_detail": "오염 물질",
    "hazard": "지정폐기물 의심", "monthly_ton": "월 발생량(톤)", "address": "배출 사업장 주소",
    "industry": "업종", "facility_type": "배출시설 여부", "mixture": "섞임 여부", "separable": "분리 가능 여부",
    "analysis": "성분 분석 결과",
}
QUESTION_TOPIC = {  # 파트 2 질문 → 파트 1 확인 경로 안내(실무 자료)
    "classification": "classification", "waste_code": "classification", "hazardous_suspect": "hazard",
    "designated_analysis": "hazard", "quantity": "monthly_generation", "components": "composition",
    "foreign_matter": "composition", "analysis": "fuel_properties", "previous_treatment": "treatment",
}


def _dirs():
    data = store.DATA_DIR / "engine"
    data.mkdir(parents=True, exist_ok=True)
    return data


def _law_data():
    """실제 판단은 waste_agent/data/law_agent(모델 답 캐시·감사 기록·조문 요약)를 함께 쓴다.
    시험용 답(키 없음·WASTE_DRY)일 때는 이 앱 데이터 폴더 안에 따로 둔다 — 시험 기록이 실제 기록에 섞이지 않게."""
    if envfile.api_key() and os.environ.get("WASTE_DRY") != "1":
        return paths.LAW_AGENT_DATA
    own = _dirs() / "law_agent"
    own.mkdir(parents=True, exist_ok=True)
    src = paths.LAW_AGENT_DATA / "law_summaries.json"
    if src.is_file() and not (own / "law_summaries.json").is_file():
        shutil.copyfile(src, own / "law_summaries.json")
    return own


def env():
    """엔진이 읽는 경로와 키. 키는 waste-chat의 .env 값을 그대로 넘긴다(파일에 쓰지 않는다)."""
    e = dict(os.environ)
    e.update({
        # waste_agent/env.sh 와 같은 값 (모델 답 캐시·조문 쉬운 요약은 waste_agent/data/law_agent 를 함께 쓴다)
        "ROOT": str(AGENT),
        "WASTE_DATA_DIR": str(paths.TABLES),
        "WASTE_REFERENCE_DIR": str(paths.REFERENCE),
        "WASTE_LAW_DIR": str(paths.LAW_DIR),
        "LAW_AGENT_DATA_DIR": str(_law_data()),
        "PYTHONPATH": str(paths.SRC),
        "PART1_DIR": str(paths.PART1),
        "PYTHONUTF8": "1",
        "TIMELY_API_KEY": envfile.api_key() or "",
    })
    if not e["TIMELY_API_KEY"]:
        e["WASTE_DRY"] = "1"  # 키가 없으면 엔진의 시험용 답으로 형식만 확인
    return e


# ── 1. 사례 만들기 ────────────────────────────────────────────────────
def _num(text):
    m = re.search(r"\d+(?:\.\d+)?", str(text or "").replace(",", ""))
    return float(m.group()) if m else None


def monthly_ton(text):
    """'월 5톤' → 5, '연 60톤' → 5, '250kg/월' → 0.25. 기간을 모르면 None."""
    if not text:
        return None
    t = str(text).replace(" ", "")
    n = _num(t)
    if n is None:
        return None
    if re.search(r"kg|킬로", t, re.I):
        n /= 1000
    if re.search(r"연|년", t):
        return round(n / 12, 4)
    if re.search(r"주", t):
        return round(n * 52 / 12, 4)
    if re.search(r"일", t) and not re.search(r"월", t):
        return round(n * 365 / 12, 4)
    if re.search(r"월|달", t):
        return n
    return None


def _components(text):
    out = []
    # '·'는 '런너·스프루'처럼 한 성분 이름 안에도 쓰여서 나누지 않는다. 비율이 붙은 덩어리는 쉼표·/·+·및으로 나눈다
    for part in re.split(r"[,/+]|및", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", part)
        name = re.sub(r"\d+(?:\.\d+)?\s*%", "", part).strip(" :")
        if name:
            out.append({"material": name, "share_pct": float(m.group(1)) if m else None, "separable": None,
                        "waste_code": None, "status": "reported"})
    return out


def _form(text):
    for name, rx in (("액상", r"액상|액체|오일|기름|용액|폐액"), ("분말", r"분말|가루|분진|먼지"), ("슬러지", r"슬러지|오니|진흙"),
                     ("파쇄", r"파쇄|조각|칩|자투리|절단|분쇄|스크랩|필름"), ("원형보존", r"원형|통째|그대로|완제품|팔레트|박스")):
        if re.search(rx, text or ""):
            return name
    return None


def _region(address):
    toks = re.split(r"[\s,]+", (address or "").strip())
    sido_names = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "광주광역시",
                  "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도",
                  "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도",
                  "경남": "경상남도", "제주": "제주특별자치도"}
    if not toks or not toks[0]:
        return None
    sido = next((full for short, full in sido_names.items() if toks[0].startswith(short)), None)
    rest = toks[1:] if sido else toks
    sgg = rest[0] if rest and re.match(r"^\S+(시|군|구)$", rest[0]) else None
    if sgg and len(rest) > 1 and sgg.endswith("시") and re.match(r"^\S+구$", rest[1]):
        sgg = f"{sgg} {rest[1]}"
    if sgg and not sido:  # 시군구 이름이 한 시도에만 있으면 그 시도
        base = sgg.split()[0]
        codes = {code for code, rows in tables._load()["centers"].items() for n, _a, _b in rows if n.startswith(base)}
        short = {v: k for k, v in tables.SIDO.items()}
        if len(codes) == 1 and next(iter(codes)) in short:
            sido = sido_names[short[next(iter(codes))]]
    if not (sido or sgg):
        return None
    return {"sido": sido, "sigungu": sgg, "address": address}


def build_case(owner, facts, version):
    f = {k: (str(facts.get(k) or "").strip()) for k in FACT_KEYS}
    missing = []

    def need(path, v):
        if v in (None, "", [], {}):
            missing.append(path)
        return v

    comps = _components(f["components"])
    foreign = []
    if len(comps) > 1 and all(c["share_pct"] is not None for c in comps):
        major = [c for c in comps if c["share_pct"] >= 5]
        if major:
            foreign = [c for c in comps if c["share_pct"] < 5]
            comps = major
    hazard = {"예": True, "의심": True, "아니오": False, "아님": False}.get(f["hazard"])
    # 성분 분석 결과서(분석기관 발급)가 있으면 그 결과가 진술보다 앞선다
    analysed = {"있음·지정 아님": False, "있음·지정 해당": True}.get(f["analysis"])
    if analysed is not None:
        hazard = analysed
    contamination = None
    if f["contamination"] in ("깨끗", "오염"):
        contamination = {"level": f["contamination"],
                         "substances": [s.strip() for s in re.split(r"[,·]", f["contamination_detail"]) if s.strip()]}
    monthly = _num(f["monthly_ton"])
    separable = {"가능": True, "불가능": False}.get(f["separable"])
    code = f["code"] if re.fullmatch(r"\d{2}-\d{2}-\d{2}", f["code"]) else ""
    return {
        "schema_version": "1.0", "case_id": f"wc-{owner}", "case_version": version, "synthetic": False,
        "name": need("name", f["name"] or None), "process_description": need("process_description", f["process"] or None),
        "classification": {"reported_code": None, "selected_code": need("classification.selected_code", code or None),
                           "status": "reviewed" if code else "needs_info"},
        "waste_code_candidates": [c["code"] for c in tables.find_codes(f["name"], 3)] if not code else [],
        "components": [{**c, "separable": separable} for c in comps],
        "mixed_with_other": {"혼합": True, "단일": False}.get(f["mixture"], (len(comps) > 1) if comps else None),
        "foreign_matter": {"items": [f"{x['material']} {x['share_pct']:g}%" for x in foreign], "separable": separable},
        "material_form": need("material_form", _form(f["form"] + " " + f["name"])),
        "contamination": need("contamination", contamination),
        "hazardous_suspect": need("hazardous_suspect", hazard),
        "analysis": {"heating_value_kcal": None, "moisture_pct": None, "chlorine_pct": None,
                     "leaching_ok": None if analysed is None else not analysed},
        "quantity": {"monthly_ton": [monthly] if monthly is not None else [], "unit": "ton",
                     "status": "reported" if monthly is not None else "needs_info"},
        "region": need("region", _region(f["address"])),
        "missing": sorted(set(missing)), "not_applicable": [],
        "part1_v2": {"case_id": f"wc-{owner}", "physical_state": f["form"], "industry": f["industry"],
                     "facility_type": f["facility_type"], "source": "waste-chat 4"},
    }


def facts_from(profile, need=None, docs=()):
    """가입 정보·거래 조건·작성 중인 서류 값으로 사실을 미리 채운다. 사용자가 고칠 수 있다."""
    need = need or {}
    out = {"address": profile.get("address", ""), "industry": profile.get("industry", ""),
           "process": profile.get("process", "")}
    wastes = need.get("wastes") or []
    detail = need.get("detail") or ""
    out["name"] = detail if detail and len(detail) <= 40 else (" · ".join(wastes) if wastes else detail[:40])
    q = monthly_ton(need.get("quantity"))
    if q is not None:
        out["monthly_ton"] = f"{q:g}"
    if need.get("quality"):
        out["contamination"] = "깨끗" if re.search(r"없|깨끗|양호", need["quality"]) else "오염"
        out["contamination_detail"] = need["quality"] if out["contamination"] == "오염" else ""
    for vals in docs:  # 서류에 적은 값이 더 구체적이다
        pick = lambda *keys: next((vals[k] for k in keys if (vals.get(k) or "").strip()), "")
        for fact, keys in (("name", ("waste_name",)), ("code", ("waste_classification", "designated_waste_code")),
                           ("process", ("generation_process", "process_steps")), ("form", ("waste_state",)),
                           ("components", ("material_resin", "raw_materials")),
                           ("contamination_detail", ("contamination_detail",))):
            v = pick(*keys)
            if v:
                out[fact] = v
        if vals.get("contamination_level"):
            out["contamination"] = "깨끗" if "없" in vals["contamination_level"] else "오염"
        if vals.get("hazard_status"):
            out["hazard"] = {"없음": "아니오", "있음": "예"}.get(vals["hazard_status"], "모름")
        q = monthly_ton(f"{vals.get('quantity_value', '')}{vals.get('quantity_unit', '')} {vals.get('quantity_period', '')}")
        if q is not None:
            out["monthly_ton"] = f"{q:g}"
    return {k: v for k, v in out.items() if v}


# ── 2. 실행 ─────────────────────────────────────────────────────────
def _handoff_path(owner):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner or ""):
        raise ValueError("잘못된 대상입니다.")
    d = store.DATA_DIR / "handoff"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{owner}.json"


def set_handoff(owner, case, base):
    """전달 파일의 원래 건과 그때 읽은 사실(base)을 보관한다. base와 달라진 사실만 사용자가 고친 것으로 본다."""
    _handoff_path(owner).write_text(json.dumps({"case": case, "base": base}, ensure_ascii=False), encoding="utf-8")


def handoff(owner):
    try:
        return json.loads(_handoff_path(owner).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start(owner, facts):
    """판단 작업을 시작하고 job id를 돌려준다. 같은 대상의 작업이 돌고 있으면 그것을 돌려준다."""
    cur = store.casefile(owner)
    job = cur.get("job") or {}
    if job.get("status") == "running" and job.get("id") in _RUNNING:
        return job
    version = (cur.get("version") or 0) + 1
    ho = handoff(owner)
    if ho:  # 분류 에이전트 전달 파일로 시작한 건: 원래 건을 그대로 넘기고 고친 사실만 덮는다
        from . import intake_handoff
        case = intake_handoff.engine_case(ho["case"], facts, ho.get("base") or {}, owner, version)
    else:
        case = build_case(owner, facts, version)
    jid = uuid.uuid4().hex[:12]
    work = _dirs() / jid
    (work / "out").mkdir(parents=True)
    (work / "case.json").write_text(json.dumps({"schema_version": "1.0", "cases": [case]}, ensure_ascii=False), encoding="utf-8")
    log = (work / "log.txt").open("w", encoding="utf-8")
    proc = subprocess.Popen([PY, "-m", "law_agent.bridge.run_one", str(work / "case.json"), "--outbox", str(work / "out")],
                            cwd=str(AGENT), env=env(), stdout=log, stderr=subprocess.STDOUT)
    job = {"id": jid, "status": "running", "started": time.time(), "version": version, "dry": not envfile.api_key()}
    with _LOCK:
        _RUNNING[jid] = proc
    store.save_casefile(owner, facts=facts, job=job, version=version)

    def wait():
        code = proc.wait()
        log.close()
        with _LOCK:
            _RUNNING.pop(jid, None)
        done = {**job, "status": "done" if code == 0 and list((work / "out").glob("*_result.json")) else "failed",
                "finished": time.time()}
        store.save_casefile(owner, job=done)
        if _on_done:
            try:
                _on_done(owner, done)
            except Exception:  # noqa: BLE001 — 알림 실패가 작업 결과를 지우지 않게
                pass

    threading.Thread(target=wait, daemon=True).start()
    return job


_on_done = None


def on_done(fn):
    global _on_done
    _on_done = fn


def progress(job):
    """진행 로그의 쉬운 말 줄 몇 개."""
    if not job:
        return []
    path = _dirs() / job["id"] / "log.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    keep = [l.strip() for l in lines if l.strip() and not l.strip().startswith(("감사 기록", "Traceback", "File "))]
    return keep[-6:]


# ── 3. 결과 정리 ─────────────────────────────────────────────────────
def _quote_label(ref):
    cid = (ref or "").split("@")[0].split("#")[0]
    law, _, key = cid.partition(":")
    hit = lawbase.get(cid)
    return hit["label"] if hit else lawbase.label(law, key)


ANSWER_KO = {"yes": "예", "no": "아니오", "conditional": "조건부", "unknown": "판단 보류"}
STATUS_KO = {"agreed": "모델 합의", "needs_human": "사람 확인 필요", "not_applicable": "해당 없음", "disagreed": "의견 갈림",
             "table": "판정표", "insufficient": "근거 부족"}


def result(job):
    if not job or job.get("status") != "done":
        return None
    out = _dirs() / job["id"] / "out"
    files = list(out.glob("*_result.json"))
    if not files:
        return None
    r = json.loads(files[0].read_text(encoding="utf-8"))
    rec = r.get("recommendation") or {}
    steps, detail = _partner_steps(r)
    routes = []
    for o in rec.get("options") or []:
        partners = []
        for p in o.get("추천업체") or []:
            partners.append({"rank": p.get("순위"), "key": p.get("company_id"), "name": p.get("업체명"), "role": p.get("역할"), "region": p.get("지역"),
                             "address": p.get("주소"), "phone": p.get("연락처"), "license": p.get("허가"),
                             "rcodes": p.get("재활용유형") or [], "methods": p.get("처리방법코드") or [],
                             "km": p.get("거리_km"), "carbon": p.get("운송탄소_kgCO2eq_월"), "why": p.get("추천이유") or [],
                             "basis": p.get("위치_근거"), "source": p.get("데이터_기준"),
                             **detail.get(p.get("company_id"), {})})
        routes.append({"title": o.get("처리"), "kind": o.get("구분"), "legal": o.get("법적상태"), "secured": o.get("업체확보"),
                       "laws": o.get("근거법령") or [], "conditions": [c[:400] for c in o.get("조건") or []],
                       "partners": partners, "partner_total": o.get("조건맞는_업체_수"),
                       "emitter_checks": o.get("배출업체가_확인할_것") or [], "partner_checks": o.get("가공업체가_알려줘야_할_것") or [],
                       "next_roles": o.get("다른_단계") or [], "carbon": o.get("처리탄소_참고")})
    docs = []
    seen = set()
    for p in r.get("route_proposals") or []:
        for t in p.get("document_triggers") or []:
            key = t.get("trigger")
            if key in seen:
                continue
            seen.add(key)
            docs.append({"title": key, "why": t.get("fact"), "basis": _quote_label(t.get("basis")), "status": t.get("basis_status")})
    questions = []
    for q in (r.get("information_request") or {}).get("requests") or []:
        topic = QUESTION_TOPIC.get(q.get("field", "").split(".")[0])
        guide = lawbase.get(f"PRACTICE:guide:{topic}") if topic else None
        questions.append({"field": q.get("field"), "text": q.get("text"), "to": q.get("to"),
                          "guide": lawbase.public(guide, 900) if guide else None})
    checks = []
    for q in r.get("law_review") or []:
        if q.get("status") == "not_applicable":
            continue
        quotes = [{"label": _quote_label(x.get("ref")), "text": (x.get("text") or "")[:400]} for x in q.get("quotes") or []][:4]
        votes = [{"model": v.get("model"), "answer": ANSWER_KO.get(v.get("answer"), v.get("answer")), "reason": (v.get("reason") or "")[:400]}
                 for v in q.get("models") or []]
        question = re.sub(r"파트\s*1이\s*준\s*", "입력한 ", q.get("question") or "").replace("파트 1", "입력 정보").replace("파트 2", "판단")
        checks.append({"id": q.get("question_id"), "question": question, "status": STATUS_KO.get(q.get("status"), q.get("status")),
                       "answer": ANSWER_KO.get(q.get("answer"), q.get("answer")), "conditions": q.get("conditions") or [],
                       "missing": q.get("missing_facts") or [], "quotes": quotes, "votes": votes})
    pre = r.get("preliminary") or {}
    run = r.get("run") or {}
    # A-1(분류번호가 폐기물과 맞는가)이 '아니오'면 그 번호로 낸 경로·업체는 쓸 수 없다 → 화면 맨 위에서 막는다
    mismatch = None
    code_now = (pre.get("classification") or {}).get("normalized_waste_code")
    a1 = next((c for c in checks if c["id"] == "A-1"), None)
    if a1 and a1["answer"] == "아니오" and code_now:
        row = tables.code(code_now) or {}
        cf = _case_file(out)
        case0 = (cf.get("cases") or [cf])[0]
        name = case0.get("name") or (rec.get("waste") or {}).get("name") or ""
        mismatch = {"code": code_now, "code_name": re.sub(r"\(.*", "", row.get("명칭", "")), "name": name,
                    "reason": next((v["reason"] for v in a1["votes"] if v["answer"] == "아니오" and v["reason"]), ""),
                    "candidates": [c for c in tables.find_codes(name, 6) if c["code"] != code_now][:4]}
    return {"waste": rec.get("waste") or {}, "status": pre.get("waste_status"), "code": (pre.get("classification") or {}).get("normalized_waste_code"),
            "routes": routes, "none": rec.get("추천_없음") or [], "docs": docs, "questions": questions, "checks": checks,
            "code_mismatch": mismatch, "cards": _cards(r.get("law_cards")), "steps": steps,
            "input": r.get("input_check") or {}, "calls": run.get("calls"), "seconds": run.get("seconds"),
            "proposal": (r.get("route_proposals") or [None])[0], "case": _case_file(out)}


# 업체를 뺀 이유 → 쉬운 말 + 근거 (law_agent output/demo_html.REASON과 같은 뜻)
REJECT_KO = {
    "자가처리만 신고": ("남의 폐기물을 위탁받지 않는 업체", "폐기물관리법 제18조 제1항"),
    "지정/일반 범위 밖": ("지정·일반폐기물 허가 구분이 다름", "폐기물관리법 제25조"),
    "처리 방법 다름": ("허가받은 처리방법이 다름", "폐기물관리법 제25조"),
    "재활용 유형 다름": ("허가받은 재활용 유형이 다름", "폐기물관리법 제25조 · 시행규칙 별표 4의3"),
    "혼합물 일부만 허가": ("섞인 성분 중 일부만 허가", "폐기물관리법 제25조"),
    "영업 중단": ("영업 중이 아님", "폐기물관리법 제25조"),
    "건설폐기물만": ("건설폐기물만 운반하는 업체", "건설폐기물법"),
    "대상 폐기물 다름": ("허가받은 운반 대상 폐기물이 아님", "폐기물관리법 제25조"),
    "혼합물 일부만 대상": ("섞인 성분 중 일부만 운반 가능", "폐기물관리법 제25조"),
}
ROLE_KO = {"recycler": "재활용", "preprocessor": "중간 가공", "transporter": "운반", "treatment": "처분", "end_user": "구매처"}
ALWAYS_ASK = [("허가증(신고증명서) 사본", "시행규칙 제16조의13 제1호 가목 — 업체에서 받기"),
              ("수탁처리능력 확인서 (제5호서식)", "폐기물관리법 제17조제1항제3호 — 업체 회신")]


def _partner_steps(r):
    """경로별 업체 거르기 과정(전체 → 뺀 이유별 → 남은 곳)과 업체마다 확인표(✓ 근거 · ! 주의 · ? 받을 서류)."""
    steps, detail = [], {}
    for route in r.get("routes") or []:
        for s in (route.get("partners") or {}).get("steps") or []:
            cands = s.get("candidates") or []
            if not cands:
                continue
            found = s.get("total_found") or len(cands)
            rej = s.get("rejected_summary") or {}
            steps.append({"role": s.get("role"), "role_ko": ROLE_KO.get(s.get("role"), s.get("role")), "found": found,
                          "total": found + sum(rej.values()),
                          "rejected": [{"why": REJECT_KO.get(k, (k, ""))[0], "law": REJECT_KO.get(k, (k, ""))[1] or "서비스 기준", "n": n}
                                       for k, n in sorted(rej.items(), key=lambda x: -x[1])]})
            for c in cands:
                loc = c.get("location") or {}
                detail.setdefault(c.get("company_id"), {
                    "evidence": [{"item": e.get("item"), "value": str(e.get("value"))[:300], "law": e.get("law") or ""} for e in c.get("evidence") or []],
                    "cautions": [str(x)[:300] for x in c.get("cautions") or []],
                    "ask": [{"item": a, "law": b} for a, b in ALWAYS_ASK],
                    "road_km": loc.get("road_km"), "duration_min": loc.get("duration_min"),
                    "distance_basis": "도로" if loc.get("road_km") is not None else (loc.get("basis") or "")})
        if steps:
            break  # 첫 경로만 (law_agent 시연과 같은 범위)
    return steps, detail


def _quotes(xs, n=4):
    return [{"where": q.get("where") or _quote_label(q.get("ref")), "quote": (q.get("quote_short") or q.get("quote") or "")[:500],
             "role": q.get("role") or ""} for q in (xs or [])[:n] if isinstance(q, dict)]


def _cards(c):
    """law_agent 법률 카드 → 화면용(결론 근거 · 해야 할 일 · 확인할 것 · 판단 근거 · 맡길 업체가 지킬 기준)."""
    if not isinstance(c, dict):
        return None
    seen, checks = set(), []
    for x in c.get("checks") or []:
        k = (x.get("kind"), x.get("title"), x.get("text"))
        if k not in seen:
            seen.add(k)
            checks.append({"kind": x.get("kind") or "", "title": x.get("title") or "", "text": (x.get("text") or "")[:400]})
    con = c.get("conclusion") or {}
    return {
        "reviewed": bool(c.get("reviewed")),
        "conclusion": {"status": con.get("status") or "", "text": con.get("text") or "", "notes": con.get("notes") or [],
                       "quotes": _quotes(con.get("basis_quotes"), 3)},
        "todos": [{"title": t.get("title") or "", "when": t.get("when") or "", "what": t.get("what") or "", "doc": t.get("doc") or "",
                   "basis": t.get("basis") or "", "key": t.get("key") or "", "quotes": _quotes(t.get("basis_quotes"), 2)}
                  for t in c.get("todos") or []],
        "checks": checks,
        "details": [{"id": d.get("question_id"), "title": d.get("title") or "", "label": d.get("label") or "",
                     "summary": d.get("summary") or "", "detail": d.get("detail_short") or d.get("detail") or "",
                     "conditions": [str(x)[:300] for x in d.get("conditions") or []], "quotes": _quotes(d.get("basis"), 3)}
                    for d in c.get("details") or []],
        "standards": [{"title": s.get("title") or "", "who": s.get("who") or "", "when": s.get("when") or "",
                       "lines": [{"where": ln.get("where") or "", "text": (ln.get("text") or "")[:600]} for ln in s.get("lines") or []][:4]}
                      for s in c.get("partner_standards") or []],
    }


def _case_file(out):
    files = list(out.glob("*_case.json"))
    try:
        return json.loads(files[0].read_text(encoding="utf-8")) if files else {}
    except ValueError:
        return {}


def view(owner):
    cur = store.casefile(owner)
    job = cur.get("job") or {}
    if job.get("status") == "running" and job.get("id") not in _RUNNING:
        job = {**job, "status": "failed", "error": "서버가 다시 시작되어 판단이 중단되었습니다. 다시 실행해 주세요."}
    res = result(job)
    if res:
        res = {k: v for k, v in res.items() if k not in ("proposal", "case")}
    return {"facts": cur.get("facts") or {}, "labels": FACT_LABELS, "job": job, "progress": progress(job) if job else [],
            "result": res, "ready": bool(envfile.api_key())}
