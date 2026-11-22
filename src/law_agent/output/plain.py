"""판단 결과 → 사용자에게 보여 줄 쉬운 요약 (2026.11.19).

law_review(질문별 판단)마다 title · summary · detail · basis 를 붙이고, 결과 맨 위에 law_summary(카드용 목록)를 만든다.
- 문장은 질문·답마다 정해 둔 틀에 판단 값(분류번호, R코드, 기준·사례 값)만 끼워 넣는다. LLM 을 쓰지 않는다
  → 같은 판단이면 늘 같은 문장, 판단에 없는 말이 들어가지 않는다.
- detail 은 판단 값의 text(근거 원문으로 검증된 답)를 그대로 옮긴다.
- 해당 없음·범위 밖 질문은 law_summary 에 넣지 않는다.
"""
from __future__ import annotations

import re

from law_agent.core.lawstore import LAW_NAME

TITLE = {
    "A-1": "분류번호 확인", "A-2": "지정폐기물 여부", "A-3": "맡기기 전 지정폐기물 확인",
    "A-4": "혼합 여부", "B-1": "재활용 금지 여부", "B-2": "재활용 제한 여부",
    "D-1": "재활용 가능 유형", "D-3": "재활용 기준", "D-4": "재활용 업체 준수사항",
    "D-5": "지정폐기물 재활용 업체 허가", "G-1": "위탁 기준·절차", "G-2": "올바로 입력",
    "G-3": "운반 기준", "H-1": "배출자 신고", "H-2": "처리계획 확인", "H-3": "처리계획 제출",
}

# (질문, 답) → 문장 틀. {code} {name} {items} {criterion} {case} 를 끼운다.
YESNO = {
    "A-1": {"yes": "분류번호 {cn}: 시행규칙 별표 4에 있는 번호이고, 폐기물 이름·성상과 맞습니다.",
            "no": "분류번호 {code}: 이 폐기물과 맞지 않습니다.{alt}",
            "conditional": "분류번호 {code}: 아래 조건을 만족하면 맞습니다."},
    "A-2": {"yes": "지정폐기물입니다. 지정폐기물을 처리할 수 있는 허가 업체에만 맡길 수 있습니다.",
            "no": "지정폐기물이 아닙니다 (사업장일반폐기물).",
            "conditional": "분석 결과에 따라 지정폐기물일 수 있습니다."},
    "A-3": {"yes": "맡기기 전에 지정폐기물인지 분석·확인해야 하는 대상입니다.",
            "no": "맡기기 전 지정폐기물 확인 대상이 아닙니다.",
            "conditional": "아래 조건에 해당하면 맡기기 전에 지정폐기물 여부를 확인해야 합니다."},
    "A-4": {"yes": "여러 폐기물이 섞여 있습니다.",
            "no": "다른 폐기물과 섞이지 않은 폐기물입니다.",
            "conditional": "아래 조건에 따라 섞인 폐기물로 볼 수 있습니다."},
    "B-1": {"yes": "재활용이 금지된 폐기물입니다. 재활용 업체에 맡길 수 없습니다.",
            "no": "재활용 금지 대상이 아닙니다.",
            "conditional": "아래 조건에 해당하면 재활용이 금지됩니다."},
    "G-2": {"yes": "올바로(전자정보처리프로그램)에 인계·인수 내용을 입력해야 합니다.",
            "no": "올바로 입력 대상이 아닙니다.",
            "conditional": "아래 조건에 따라 올바로 입력이 필요합니다."},
}
TEXT = {
    "D-3": "이 재활용 유형에 적용되는 재활용 기준입니다.",
    "D-4": "재활용 업체가 지켜야 할 준수사항입니다.",
    "D-5": "지정폐기물은 아래 허가를 가진 업체에만 맡길 수 있습니다.",
    "G-1": "폐기물을 맡길 때 지켜야 할 기준과 절차입니다.",
    "G-3": "운반 업체가 지켜야 할 운반 기준입니다.",
    "H-3": "처리계획 확인을 받을 때 낼 서류, 제출처, 기한입니다.",
}
# 기준 질문(원문 첨부) — 누가 지키는 기준이고 언제 확인하는지 (법 내용이 아니라 역할 구분, 2026.11.20)
STD_ROLE = {
    "D-3": {"who": "맡길 재활용업체", "when": "업체 고를 때", "kind": "partner"},
    "D-4": {"who": "맡길 재활용업체", "when": "업체 고를 때", "kind": "partner"},
    "D-5": {"who": "맡길 재활용업체", "when": "업체 고를 때", "kind": "partner"},
    "G-3": {"who": "운반업체", "when": "운반업체 고를 때", "kind": "partner"},
    "G-1": {"who": "배출자", "when": "맡기기 전", "kind": "emitter"},
    "H-3": {"who": "배출자", "when": "사업 시작·발생일부터 1개월 안", "kind": "emitter"},
}
STD_LABEL = "기준 찾음"
FLAG_ATTACHED = "attached_text"


def standard_lines(item: dict) -> list[dict]:
    """원문 첨부 질문의 기준마다 {where, text(한 줄), source(원문), plain(요약이면 True), reviewed}.
    한 줄 = 미리 만든 요약(law_summary) → 없으면 원문 첫 문장. 여기서 LLM 을 부르지 않는다."""
    v = item.get("value") or {}
    raw = v.get("standards") or [{"ref": q.get("ref") or "", "title": "", "text": q.get("text") or ""}
                                  for q in item.get("quotes") or []]
    out = []
    for x in raw:
        src = re.sub(r"\s+", " ", x.get("text") or "").strip()
        if not src:
            continue
        hit = None
        try:
            from law_agent.output.law_summary import lookup, standard_target
            t = standard_target(src)
            hit = lookup(t) if t else None
        except Exception:                                # noqa: BLE001
            hit = None
        out.append({"ref": x.get("ref") or "", "where": ref_text(x.get("ref") or "") or x.get("title") or "",
                    "text": hit["summary"] if hit else first_sentence(src, 140),
                    "plain": bool(hit), "reviewed": bool(hit and hit.get("reviewed")), "source": src})
    return out


COMPARE = {
    "H-1": {"met": "사업장폐기물배출자 신고 대상입니다.", "not_met": "사업장폐기물배출자 신고 대상이 아닙니다."},
    "H-2": {"met": "지정폐기물 처리계획 확인 대상입니다.", "not_met": "지정폐기물 처리계획 확인 대상이 아닙니다."},
}
STATUS_LABEL = {"agreed": "확인됨", "needs_info": "정보 필요", "needs_human": "담당자 확인 필요"}
ANSWER_LABEL = {"yes": "예", "no": "아니오", "conditional": "조건부", "unknown": "모름"}
FACT_KO = {"waste_code": "분류번호", "waste_name": "폐기물 이름", "material_form": "성상", "components": "성분",
           "analysis": "분석 결과", "monthly_ton": "월 발생량", "contamination": "오염 여부", "process": "발생 공정"}


def ref_text(ref: str) -> str:
    """'WMA_RULE:16의13/호3@20261112#…' → '폐기물관리법 시행규칙 제16조의13 제3호 (2026.11.12 시행)'"""
    m = re.match(r"^([A-Z_]+):([^@#]+)(?:@(\d{8}))?", ref or "")
    if not m:
        return ref or ""
    law, key, day = m.groups()
    parts = []
    for i, p in enumerate(key.split("/")):
        if i == 0:
            if p.startswith("BF"):
                parts.append(f"별지 제{p[2:]}호서식")
            elif p.startswith("B"):
                head, _, ho = p[1:].partition(":")
                parts.append(f"별표 {head}" + (f" 제{ho.split('~')[0]}호" if ho else ""))
            elif p.startswith("§"):
                parts.append(p[1:])
            else:
                parts.append(f"제{p.replace('의', '조의', 1)}" + ("" if "의" in p else "조"))
        else:
            n = re.sub(r"^(항|호|목)", "", p)
            parts.append({"항": f"제{n}항", "호": f"제{n}호", "목": f"{n}목"}.get(p[:1], p))
    name = LAW_NAME.get(law, law)
    when = f" ({day[:4]}.{day[4:6]}.{day[6:]} 시행)" if day else ""
    return f"{name} {' '.join(parts)}{when}"


def _facts(eff: dict | None) -> dict:
    vals = (eff or {}).get("values") or {}
    code = str(vals.get("waste_code") or "")
    name = str(vals.get("waste_name") or "")
    if code and not name:
        try:
            from law_agent.tools.fact_tools import waste_code_name
            name = waste_code_name(code) or ""
        except Exception:                                # noqa: BLE001 — 표 없는 환경
            name = ""
    code = code or "(분류번호 없음)"
    name = re.split(r"[(（]", name)[0].strip()          # "폐합성수지류(폐염화비닐수지류는 제외한다)" → "폐합성수지류"
    return {"code": code, "name": name, "cn": f"{code}({name})" if name else code}


def _d1(v: dict, f: dict, scope_r: list[str]) -> str:
    items = v.get("items") or []
    if not items:
        return f"{f['cn']}에 허용된 재활용 유형이 없습니다."
    attrs = v.get("attrs") or {}
    s = f"{f['cn']}에 허용된 재활용 유형: {', '.join(items)}."
    for rc in scope_r:
        if rc in items:
            s += f" 중간 가공({rc})이 포함됩니다."
            if (attrs.get(rc) or {}).get("pre_analysis"):
                s += f" 다만 이 방식으로 맡길 때는 경우에 따라 사전 분석이 필요할 수 있습니다."
        else:
            s += f" 중간 가공({rc})은 허용 유형에 없습니다."
    return s


RX_DISPUTE = re.compile(r"모델 간 이견")
RX_MINORITY = re.compile(r"모델 간 이견 — .*? 은\(는\) '(\w+)' 로 판단")
MINORITY_KO = {"B-1": {"yes": "재활용 금지"}, "A-2": {"yes": "지정폐기물", "no": "지정폐기물 아님"},
               "D-1": {"no": "허용 유형 없음"}}
RX_PENDING = re.compile(r"다음 사실이 없어 판단을 보류함:\s*(.+?)\s*—")
NEG_ROLE = {"A-2", "A-3", "A-4", "B-1", "B-2"}     # '아니오' 답의 인용 = 확인했지만 해당하지 않은 기준


def clip(text: str, n: int = 150) -> str:
    """원문 인용·내용을 한 줄로 줄이고 n 자에서 자른다"""
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def first_sentence(text: str, n: int = 150) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"(?<=[가-힣\)\]」])다\.", t)
    return clip(t[: m.end()] if m and m.end() <= n else t, n)


_STORE_FAIL = False


def chunk_text(ref: str, store=None) -> str:
    """근거 키의 조문·별표 원문 전체 (정리된 글). 법령 저장소가 없으면 ''"""
    global _STORE_FAIL
    if _STORE_FAIL and store is None:
        return ""
    m = re.match(r"^([A-Z_]+):([^@#]+)", ref or "")
    if not m:
        return ""
    try:
        from law_agent.core.lawstore import top_key
        from law_agent.tools.law_tools import clean_law_text, get_store
        st = store or get_store()
        law, key = m.groups()
        c = st.get(law, key) or st.get(law, top_key(key))
    except Exception:                                    # noqa: BLE001 — 법령 데이터 없는 환경
        _STORE_FAIL = True
        return ""
    return clean_law_text(c.text or "") if c is not None else ""


def expand_quote(ref: str, quote: str, store=None) -> str:
    """인용이 '다음 각 호/목' 머리말로 끝나면 뒤 호·목을 붙인 글 (화면·요약용 — 판단 인용 자체는 그대로). 아니면 quote"""
    if not quote or "다음" not in quote:
        return quote
    from law_agent.tools.law_tools import expand_pointer
    return expand_pointer(quote, chunk_text(ref, store)) or quote


def source_text(ref: str, quote: str = "", n: int = 1200, store=None) -> str:
    """근거 키의 법 원문 (개정 표시 등 정리). 길면 인용 부분을 가운데 두고 n 자. 법령 저장소가 없으면 ''"""
    global _STORE_FAIL
    if _STORE_FAIL and store is None:
        return ""
    m = re.match(r"^([A-Z_]+):([^@#]+)", ref or "")
    if not m:
        return ""
    try:
        from law_agent.core.lawstore import top_key
        from law_agent.tools.law_tools import clean_law_text, get_store
        st = store or get_store()
        law, key = m.groups()
        c = st.get(law, key) or st.get(law, top_key(key))
    except Exception:                                    # noqa: BLE001 — 법령 데이터 없는 환경
        _STORE_FAIL = True
        return ""
    if c is None:
        return ""
    t = re.sub(r"[ \t]+", " ", clean_law_text(c.text or "")).strip()
    if len(t) <= n:
        return t
    q = re.sub(r"\s+", "", quote or "")[:20]
    i = re.sub(r"\s+", "", t).find(q) if q else -1
    if i > 0:                                            # 공백 뺀 위치 → 원문 위치 (대략)
        j, k = 0, 0
        while j < len(t) and k < i:
            k += 0 if t[j].isspace() else 1
            j += 1
        a = max(0, j - n // 3)
        return ("…" if a else "") + t[a:a + n] + ("…" if a + n < len(t) else "")
    return t[:n] + "…"


def clean_conditions(item: dict) -> tuple[list[str], bool, list[str]]:
    """판단의 조건 → (사용자에게 보일 조건, 모델 판단이 엇갈렸는지, 확인되면 확정될 사실)"""
    user, disputed, pending = [], False, []
    for c in item.get("conditions") or []:
        if not c:
            continue
        if RX_DISPUTE.search(c):
            disputed = True
            continue
        m = RX_PENDING.search(c)
        if m:
            pending += [x.strip() for x in m.group(1).split(",") if x.strip()]
            continue
        user.append(c)
    # safety_veto 플래그만으로는 '엇갈림'으로 보지 않는다 (2026.11.20 세척액: 2개 '금지 아님' + 1개 '성분 몰라 보류'
    #  → 반대 결론이 아니라 정보 부족. 반대 결론은 verifier 가 '모델 간 이견' 조건을 단다)
    return user, disputed, pending


def _majority(item: dict) -> str:
    votes = [v.get("answer") for v in item.get("votes") or [] if v.get("valid", True) and v.get("answer")]
    return max(set(votes), key=votes.count) if votes else ""


def plain_summary(b: dict) -> dict | None:
    """근거 b 의 쉬운 요약 (output.law_summary 에 미리 만든 것만 — 여기서 LLM 을 부르지 않음). 없으면 None"""
    try:
        from law_agent.output.law_summary import lookup, target
        t = target(b)
        return lookup(t) if t else None
    except Exception:                                    # noqa: BLE001  요약표 문제로 카드가 깨지지 않게
        return None


def summarize(item: dict, eff: dict | None = None, scope_r: list[str] | None = None) -> dict:
    """law_review 항목 하나 → {title, label, summary, detail, conditions, basis}"""
    qid = item.get("question_id") or ""
    base = qid.split(".")[0]
    st, ans, v = item.get("status"), item.get("answer") or "unknown", item.get("value") or {}
    f = _facts(eff)
    scope_r = scope_r if scope_r is not None else ["R-10"]
    conds, disputed, pending = clean_conditions(item)
    if disputed and ans == "conditional":
        maj = _majority(item)
        ans = maj if maj in ("yes", "no") else ans
    title = TITLE.get(base, item.get("question") or qid)
    if ".c" in qid:
        title += f" (성분 {qid.split('.c')[-1]})"
    detail = ""
    if st == "needs_info":
        miss = [FACT_KO.get(x, x) for x in item.get("missing_facts") or []]
        summary = "판단하려면 정보가 더 필요합니다" + (f": {', '.join(miss)}" if miss else ".")
    elif st == "needs_human" or ans == "unknown":
        summary = "법령 원문만으로 판단하지 못해 담당자 확인이 필요합니다."
    elif base == "D-1":
        summary = _d1(v, f, scope_r)
    elif base == "B-2":
        items = v.get("items") or []
        summary = (f"재활용이 제한되는 폐기물입니다. 허용되는 유형: {', '.join(items)}" if ans == "yes" and items
                   else "재활용 제한 대상이 아닙니다." if ans == "no" else "아래 조건에 따라 재활용이 제한됩니다.")
    elif base in COMPARE:
        res = v.get("result") or ("met" if ans == "yes" else "not_met" if ans == "no" else "")
        summary = COMPARE[base].get(res, "아래 조건에 따라 대상일 수 있습니다.")
        if v.get("criterion"):
            case = v.get("case_value")
            detail = f"기준: {v['criterion']}" + (f" / 이 사례: {case}{v.get('unit') or ''}" if case not in (None, "") else "")
    elif base in TEXT:
        role = STD_ROLE.get(base)
        summary = (f"{role['who']}가 지켜야 할 기준입니다. {role['when']} 확인하세요." if role and role["kind"] == "partner"
                   else f"{TEXT[base]} 배출자가 지킬 것({role['when']})이라 '해야 할 일'에 넣었습니다." if role else TEXT[base])
        detail = str(v.get("text") or "")
    elif base in YESNO:
        alt = v.get("items") or []
        summary = YESNO[base].get(ans, "").format(code=f["code"], cn=f["cn"],
                                                    alt=f" 맞는 번호 후보: {', '.join(alt)}" if alt else "")
    else:
        summary = ANSWER_LABEL.get(ans, ans)
        detail = str(v.get("text") or "")
    label = "조건부" if ans == "conditional" and st == "agreed" else STATUS_LABEL.get(st, st)
    if pending:
        conds.append("다음 정보가 확인되면 확정됩니다: " + ", ".join(pending))
        label = "조건부" if st == "agreed" else label
    standards = standard_lines(item) if base in TEXT and st == "agreed" and FLAG_ATTACHED in (item.get("flags") or []) else []
    if standards:
        label = STD_LABEL
    if disputed and st == "agreed":
        label = "담당자 확인 필요"
        summary = (summary + " " if summary else "") + "다만 자동 판단 결과가 엇갈려 담당자가 확인합니다."
    role = "확인한 기준" if ans == "no" and base in NEG_ROLE and st == "agreed" else "근거"
    basis = []
    for q in item.get("quotes") or []:
        ref = q.get("ref") or ""
        if ref and all(b["ref"] != ref for b in basis):
            qt = expand_quote(ref, q.get("text") or "")         # "다음 각 목의 방법으로" → 가·나목까지
            b = {"ref": ref, "where": ref_text(ref), "quote": qt,
                 "quote_short": clip(qt), "role": role,
                 "source": source_text(ref, q.get("text") or "")}
            b["plain"] = plain_summary(b)
            basis.append(b)
    return {"question_id": qid, "title": title, "label": label, "summary": summary, "detail": detail,
            "detail_short": first_sentence(detail) if detail else "", "conditions": conds, "basis": basis,
            "disputed": disputed and st == "agreed", "standards": standards,
            "who": (STD_ROLE.get(base) or {}).get("who", ""), "when": (STD_ROLE.get(base) or {}).get("when", "")}


def attach(law_review: list[dict], eff: dict | None = None, scope_r: list[str] | None = None) -> list[dict]:
    """law_review 항목마다 title·label·summary·detail·basis 를 더하고, 카드용 목록(law_summary)을 돌려준다."""
    cards = []
    for it in law_review:
        s = summarize(it, eff, scope_r)
        it.update({k: s[k] for k in ("title", "label", "summary", "detail", "basis")})
        if it.get("status") == "not_applicable":
            continue
        cards.append(s)
    return cards


def from_result(res: dict, scope_r: list[str] | None = None) -> list[dict]:
    """저장된 결과 파일(law_review 가 있는)에서 다시 만든다 — LLM 다시 안 부름."""
    code = ((res.get("preliminary") or {}).get("classification") or {}).get("normalized_waste_code") or ""
    if scope_r is None:
        try:
            from law_agent.settings import load_settings
            scope_r = (load_settings().get("scope") or {}).get("r_codes") or []
        except Exception:                                # noqa: BLE001
            scope_r = ["R-10"]
    return attach([dict(x) for x in res.get("law_review") or []], {"values": {"waste_code": code}}, scope_r)


def show(cards: list[dict]) -> str:
    out = []
    for c in cards:
        out.append(f"[{c['label']}] {c['title']}  ({c['question_id']})")
        out.append(f"  {c['summary']}")
        if c["detail"]:
            out.append(f"  내용: {c['detail'][:300]}")
        for x in c["conditions"]:
            out.append(f"  조건: {x}")
        for b in c["basis"][:2]:
            out.append(f"  근거: {b['where']}")
            if b["quote"]:
                out.append(f"        「{b['quote'][:150]}」")
        out.append("")
    return "\n".join(out)




# ── 사용자용 카드 3묶음 (2026.11.19): 결론 · 해야 할 일 · 확인할 것 (+ 자세히 보기 = law_summary) ──
# 해야 할 일 문장은 고정 문장이다 (LLM 답을 옮기지 않음). 근거 원문으로 확인한 내용만 적고, 확인 안 된 기한은 적지 않는다.
CARDS_REVIEWED = False          # 사람이 원문과 대조해 확인하면 True → 화면의 "(안내 문장 초안)" 표시가 빠짐

TODO = {
    "capacity": {"title": "수탁처리능력 확인서 받기", "when": "폐기물을 맡기기 전",
                 "what": "처리업체에서 수탁처리능력 확인서를 받습니다.",
                 "doc": "별지 제5호서식 (수탁처리능력 확인서)",
                 "basis": "폐기물관리법 제17조제1항제3호 · 시행규칙 제16조의13 제1호", "ref": "WMA_RULE:16의13/호1"},
    "contract": {"title": "위탁계약서 쓰기", "when": "폐기물을 맡기기 전",
                 "what": "처리업체와 서면으로 위탁계약을 맺습니다. 계약서에 폐기물 종류·수량, 계약 기간·위탁 비용, "
                         "폐기물의 성질·상태·취급 시 주의사항을 넣습니다.",
                 "doc": "위·수탁 계약서", "basis": "폐기물관리법 시행규칙 제16조의13 제3호", "ref": "WMA_RULE:16의13/호3"},
    "pre_analysis": {"title": "맡기기 전 분석 확인", "when": "폐기물을 맡기기 전",
                     "what": "성분에 따라 재활용 전에 분석이 필요할 수 있습니다. 처리업체와 분석이 필요한지 확인합니다.",
                     "doc": "", "basis": "폐기물관리법 시행규칙 별표 4의3", "use_src": True},
    "designated_check": {"title": "지정폐기물 해당 여부 확인", "when": "폐기물을 맡기기 전",
                         "what": "맡기기 전에 이 폐기물이 지정폐기물인지 분석·확인합니다.",
                         "doc": "", "basis": "폐기물관리법 시행규칙 제17조의2", "ref": "WMA_RULE:17의2"},
    "emitter_report": {"title": "사업장폐기물배출자 신고", "when": "",
                       "what": "관할 기관에 사업장폐기물배출자 신고를 합니다.",
                       "doc": "별지 제6호서식 (배출자 신고서)", "basis": "폐기물관리법 제17조제2항 · 시행규칙 제18조", "use_src": True},
    "treatment_plan": {"title": "지정폐기물 처리계획 확인받기", "when": "사업 개시일 또는 폐기물이 발생한 날부터 1개월 안에",
                       "what": "폐기물 발생지를 관할하는 시·도지사 또는 지방환경관서의 장에게 처리계획서를 내고 확인받습니다.",
                       "doc": "별지 제12호서식 (지정폐기물 처리계획서)", "basis": "폐기물관리법 제17조제5항 · 시행규칙 제18조의2제5항", "ref": "WMA_RULE:18의2/항5"},
    "allbaro": {"title": "올바로에 인계·인수 입력", "when": "",
                "what": "폐기물을 넘길 때 올바로(폐기물 전자정보처리프로그램)에 인계·인수 내용을 입력합니다.",
                "doc": "", "basis": "폐기물관리법 제18조 제3항", "ref": "WMA:18/항3"},
}
KEY_Q = ("A-2", "B-1", "D-1")
# 재활용 금지 판단을 보류했을 때, 인용된 금지 항목 → 무엇을 확인하고 어떤 자료를 내면 되는지 (2026.11.20)
BAN_HINTS = [
    ("폴리클로리네이티드비페닐", "PCB(폴리클로리네이티드비페닐)가 기준 농도 이상 들어 있지 않은지",
     "PCB 분석 결과, 또는 세척제·기름 성분자료(MSDS)"),
    ("PCB", "PCB(폴리클로리네이티드비페닐)가 기준 농도 이상 들어 있지 않은지",
     "PCB 분석 결과, 또는 세척제·기름 성분자료(MSDS)"),
    ("석면", "석면이 들어 있지 않은지", "석면 분석 결과 또는 자재 성분자료"),
    ("폐농약", "농약이 섞여 있지 않은지", "사용 원료·세척제 성분자료(MSDS)"),
    ("의료폐기물", "의료폐기물이 섞여 있지 않은지", "발생 공정 설명"),
    ("방사성", "방사성 물질이 섞여 있지 않은지", "발생 공정 설명·원료 자료"),
    ("유해물질", "유해물질이 기준 이상 들어 있지 않은지", "폐기물 분석 결과(용출시험 등)"),
]


def ban_checks(items: list[dict]) -> list[tuple[str, str, str]]:
    """B-1 인용에서 걸린 금지 항목 → [(확인할 것, 낼 자료, 근거 조문)]"""
    out, seen = [], set()
    for it in items:
        for q in it.get("quotes") or []:
            t = q.get("text") or ""
            for kw, what, doc in BAN_HINTS:
                if kw in t and what not in seen:
                    seen.add(what)
                    out.append((what, doc, ref_text(q.get("ref") or "")))
    return out
ASK_WHY = {"배출자 신고": "배출자 신고 대상인지 확인", "처리계획 확인": "처리계획 확인 대상인지 확인",
           "지정폐기물 여부": "지정폐기물인지 확인", "분류번호 확인": "분류번호 확인",
           "재활용 금지 여부": "재활용 금지 대상인지 확인", "올바로 입력": "올바로 입력 대상인지 확인"}       # 결론에 꼭 필요한 판단


def _by_base(review: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for it in review:
        out.setdefault((it.get("question_id") or "").split(".")[0], []).append(it)
    return out


def _ok(items: list[dict]) -> bool:
    return bool(items) and all(i.get("status") == "agreed" and i.get("answer") not in (None, "unknown") for i in items)


def _ans(items: list[dict], want: str) -> bool:
    return any(i.get("status") == "agreed" and i.get("answer") == want for i in items)


def _met(items: list[dict]) -> bool:
    return any(i.get("status") == "agreed" and ((i.get("value") or {}).get("result") == "met" or i.get("answer") == "yes")
               for i in items)


def _r_allowed(items: list[dict], rc: str) -> tuple[bool, bool]:
    """(허용, 사전 분석 필요) — 성분별 질문이면 모든 성분에 허용돼야 허용"""
    if not items:
        return False, False
    allowed = all(rc in ((i.get("value") or {}).get("items") or []) for i in items)
    pre = any(((i.get("value") or {}).get("attrs") or {}).get(rc, {}).get("pre_analysis") for i in items)
    return allowed, pre


def _quote_basis(items: list[dict]) -> list[dict]:
    out = []
    for i in items:
        for q in i.get("quotes") or []:
            if q.get("ref") and all(o["ref"] != q["ref"] for o in out):
                out.append({"ref": q["ref"], "where": ref_text(q["ref"]), "quote": q.get("text") or "",
                            "quote_short": clip(q.get("text") or ""), "role": "근거"})
    return out


def _todo(key: str, src: list[dict]) -> dict:
    """할 일 카드. 근거 원문은 그 할 일의 조문(ref)을 법령 저장소에서 통째로 — 다른 판단의 인용 조각을 쓰지 않는다.
    ref 가 없는 것(use_src)만 그 판단의 인용을 쓴다."""
    t = dict(TODO[key])
    quotes = []
    if t.get("ref"):
        txt = source_text(t["ref"], n=900)
        if txt:
            b = {"ref": t["ref"], "where": ref_text(t["ref"]), "quote": txt, "quote_short": clip(txt, 150), "role": "근거",
                 "whole": not txt.endswith("…")}                 # 할 일 근거는 그 호·항 전체 (잘렸으면 …)
            b["plain"] = plain_summary(b)
            quotes = [b]
    elif t.get("use_src"):
        quotes = _quote_basis(src)
    t.update({"key": key, "basis_quotes": quotes})
    return t


def cards(review: list[dict], eff: dict | None = None, scope_r: list[str] | None = None,
          partners: int | None = None) -> dict:
    """law_review → {conclusion, todos, checks, details, reviewed}"""
    scope_r = scope_r if scope_r is not None else ["R-10"]
    rc = scope_r[0] if scope_r else ""
    q = _by_base(review)
    a2, b1, d1 = q.get("A-2", []), q.get("B-1", []), q.get("D-1", [])
    allowed, pre = _r_allowed(d1, rc) if rc else (bool(_ans(d1, "yes")), False)
    designated = _ans(a2, "yes")
    route = f"중간 가공({rc}) 재활용업체" if rc else "재활용업체"

    disputed = {k for k in ("A-1",) + KEY_Q if any(clean_conditions(i)[1] for i in q.get(k, []))}
    # ① 결론
    notes = []
    if disputed & set(KEY_Q):
        names = [TITLE.get(k, k) for k in KEY_Q if k in disputed]
        status, text = "확인 필요", f"{'·'.join(names)} 판단이 엇갈려 아직 {route}에 맡길 수 있는지 결론을 내지 않았습니다."
        for k in KEY_Q:
            if k not in disputed:
                continue
            for it in q.get(k, []):
                minority = next((m.group(1) for c in it.get("conditions") or []
                                 for m in [RX_MINORITY.search(c or "")] if m), None)
                if minority:
                    notes.append(f"{TITLE.get(k, k)}: AI 판단 하나가 '{MINORITY_KO.get(k, {}).get(minority, ANSWER_LABEL.get(minority, minority))}'(으)로 봤습니다.")
                notes += [f"{TITLE.get(k, k)}: {x}" for x in clean_conditions(it)[0]]
                if it.get("missing_facts"):
                    notes.append(f"{TITLE.get(k, k)}: 이 정보가 있으면 판단할 수 있습니다 — "
                                 + ", ".join(FACT_KO.get(x, x) for x in it["missing_facts"]))
        notes.append("담당자가 법령 원문을 확인한 뒤 결론을 알려 드립니다. 아래 업체 목록은 참고용입니다.")
    elif _ans(b1, "yes"):
        status, text = "불가", "재활용이 금지된 폐기물이라 재활용업체에 맡길 수 없습니다."
    elif _ok(d1) and not allowed:
        status, text = "불가", f"이 폐기물은 {route}에 맡길 수 있는 유형이 아닙니다."
    elif all(_ok(q.get(k, [])) for k in KEY_Q) and allowed:
        status, text = ("조건부 가능" if (pre or designated or _ans(b1, "conditional")) else "가능"), \
            f"이 폐기물은 {route}에 맡길 수 있습니다."
        if designated:
            notes.append("지정폐기물이라 지정폐기물을 처리할 수 있는 허가 업체에만 맡길 수 있습니다.")
        if pre:
            notes.append("성분에 따라 맡기기 전에 분석이 필요할 수 있습니다.")
        for i in b1:
            if not _ans([i], "conditional"):
                continue
            user, _, pending = clean_conditions(i)
            notes += user
            if pending:
                votes = [v for v in i.get("votes") or [] if v.get("valid", True)]
                no = sum(v.get("answer") == "no" for v in votes)
                bans = ban_checks([i])
                what = " · ".join(w for w, _, _ in bans) or ", ".join(pending)
                notes.append(f"재활용 금지 여부: {what} 확인되면 '금지 아님'으로 확정됩니다"
                             + (f" (AI 판단 {len(votes)}개 중 {no}개는 '금지 아님', 나머지는 정보가 없어 보류)" if votes else ""))
        if "A-1" in disputed:
            status = "조건부 가능"
            notes.insert(0, f"분류번호({_facts(eff)['code']})는 담당자 확인 후 확정됩니다. 번호가 바뀌면 결과가 달라질 수 있습니다.")
        if partners:
            notes.append(f"조건에 맞는 추천 업체 {partners}곳을 찾았습니다.")
    else:
        status, text = "확인 필요", f"아래 '확인할 것'을 먼저 처리하면 {route}에 맡길 수 있는지 알 수 있습니다."
    conclusion = {"status": status, "text": text, "notes": notes,
                  "basis_quotes": _quote_basis(b1 + d1 + a2)}

    # ② 해야 할 일 (맡길 수 있거나 확인 중일 때만)
    todos = []
    if status != "불가":
        if pre:
            todos.append(_todo("pre_analysis", d1))
        if _ans(q.get("A-3", []), "yes") or _ans(q.get("A-3", []), "conditional"):
            todos.append(_todo("designated_check", q.get("A-3", [])))
        todos.append(_todo("capacity", q.get("G-1", [])))
        todos.append(_todo("contract", q.get("G-1", [])))
        if _met(q.get("H-1", [])):
            todos.append(_todo("emitter_report", q.get("H-1", [])))
        if _met(q.get("H-2", [])):
            todos.append(_todo("treatment_plan", q.get("H-2", []) + q.get("H-3", [])))
        if _ans(q.get("G-2", []), "yes") or _ans(q.get("G-2", []), "conditional"):
            todos.append(_todo("allbaro", q.get("G-2", [])))

    # ③ 확인할 것 — 무엇을 판단하려고 묻는지 같이 적는다 (예: "배출자 신고 대상인지 보려면: 1일 평균 배출량")
    checks, asked = [], {}
    for it in review:
        base = (it.get("question_id") or "").split(".")[0]
        title = TITLE.get(base, base)
        if it.get("status") == "needs_info":
            facts = [FACT_KO.get(x, x) for x in it.get("missing_facts") or []] or [title]
            asked.setdefault(title, [])
            asked[title] += [f for f in facts if f not in asked[title]]
        elif it.get("status") == "agreed" and clean_conditions(it)[1]:
            checks.append({"kind": "담당자 확인", "title": title,
                           "text": f"{title}: 자동 판단 결과가 엇갈려 담당자가 확인합니다."})
        elif it.get("status") == "needs_human":
            checks.append({"kind": "담당자 확인", "title": title,
                           "text": f"{title}: 법령만으로 자동 판단하기 어려워 담당자가 확인합니다."})
    checks = [{"kind": "알려 주세요", "title": t, "facts": f, "text": f"{ASK_WHY.get(t, t + ' 판단')}에 필요: {', '.join(f)}"}
              for t, f in asked.items()] + checks
    for it in b1:
        if _ans([it], "conditional") and clean_conditions(it)[2]:
            for what, doc, where in ban_checks([it]) or [("재활용 금지 항목에 해당하지 않는지",
                                                           "성분자료(MSDS) 또는 폐기물 분석 결과", "")]:
                checks.insert(0, {"kind": "자료를 내 주세요", "title": "재활용 금지 여부",
                                  "text": f"{what} 확인 — 낼 자료: {doc}" + (f" (근거: {where})" if where else "")})

    details = [summarize(it, eff, scope_r) for it in review if it.get("status") != "not_applicable"]
    # ④ 맡길 업체가 지켜야 할 기준 — 업체 고를 때 확인할 것 (배출자 기준 G-1·H-3 은 '해야 할 일'에 있음)
    partner_std = [{"question_id": d["question_id"], "title": d["title"], "who": d["who"], "when": d["when"],
                    "lines": d["standards"]}
                   for d in sorted(details, key=lambda d: ORDER.index(d["question_id"].split(".")[0])
                                   if d["question_id"].split(".")[0] in ORDER else 99)
                   if d.get("standards") and (STD_ROLE.get(d["question_id"].split(".")[0]) or {}).get("kind") == "partner"]
    if status == "불가":
        partner_std = []
    return {"conclusion": conclusion, "todos": todos, "checks": checks, "details": details,
            "partner_standards": partner_std, "reviewed": CARDS_REVIEWED}


def partner_count(routes: list[dict]) -> int | None:
    """첫 경로의 처리 업체 후보 수 (운반 제외)"""
    for r in routes or []:
        for s in ((r.get("partners") or {}).get("steps") or []):
            if s.get("role") != "transporter" and s.get("candidates"):
                return len(s["candidates"])
    return None


ORDER = ["A-1", "A-2", "A-3", "A-4", "B-1", "B-2", "D-1", "D-3", "D-4", "D-5", "G-1", "G-2", "G-3", "H-1", "H-2", "H-3"]


def show_cards(c: dict) -> str:
    """결론 → 판단 근거(메인) → 해야 할 일 → 확인할 것"""
    head = "" if c.get("reviewed") else "  (안내 문장 초안 · 원문이 우선)"
    k = c["conclusion"]
    out = [f"■ 결론: [{k['status']}] {k['text']}{head}"] + [f"   · {n}" for n in k["notes"]]

    out.append("\n■ 판단 근거")
    det = sorted(c.get("details") or [], key=lambda d: (ORDER.index(d["question_id"].split(".")[0])
                                                         if d["question_id"].split(".")[0] in ORDER else 99, d["question_id"]))
    for i, d in enumerate(det, 1):
        out.append(f" {i}. {d['title']} — {d['label']}")
        out.append(f"    {d['summary']}")
        if d.get("detail_short"):
            out.append(f"    내용: {d['detail_short']}" + ("  (전체는 cards.json)" if len(d.get("detail") or "") > len(d["detail_short"]) else ""))
        for x in d.get("conditions") or []:
            out.append(f"    조건: {clip(x, 200)}")
        for b in (d.get("basis") or [])[:3]:
            out.append(f"    {b.get('role', '근거')}: {b['where']}")
            if b.get("plain"):
                out.append(f"          요약: {b['plain']['summary']}" + ("" if b["plain"]["reviewed"] else "  (AI 요약 · 검수 전)"))
            if b.get("quote_short"):
                out.append(f"          「{b['quote_short']}」")
    if not det:
        out.append("   (없음)")

    out.append("\n■ 해야 할 일")
    for i, t in enumerate(c["todos"], 1):
        out.append(f" {i}. {t['title']}" + (f"  — {t['when']}" if t["when"] else ""))
        out.append(f"    {t['what']}")
        if t["doc"]:
            out.append(f"    서류: {t['doc']}")
        out.append(f"    근거: {t['basis']}")
    if not c["todos"]:
        out.append("   (없음)")
    if c.get("partner_standards"):
        out.append("\n■ 맡길 업체가 지켜야 할 기준")
        for x in c["partner_standards"]:
            out.append(f" · {x['title']} — {x['who']} ({x['when']})")
            for ln in x["lines"]:
                out.append(f"    - {ln['text']}  [{ln['where']}]" + ("" if ln["plain"] and ln["reviewed"] else
                                                                      "  (AI 요약 · 검수 전)" if ln["plain"] else ""))
    out.append("\n■ 확인할 것")
    out += [f" · [{x['kind']}] {x['text']}" for x in c["checks"]] or ["   (없음)"]
    return "\n".join(out)


def cards_from_result(res: dict, scope_r: list[str] | None = None) -> dict:
    code = ((res.get("preliminary") or {}).get("classification") or {}).get("normalized_waste_code") or ""
    if scope_r is None:
        try:
            from law_agent.settings import load_settings
            scope_r = (load_settings().get("scope") or {}).get("r_codes") or []
        except Exception:                                # noqa: BLE001
            scope_r = ["R-10"]
    return cards(res.get("law_review") or [], {"values": {"waste_code": code}}, scope_r,
                 partner_count(res.get("routes") or []))


def save_cards(result_path: str) -> list[str]:
    """저장된 결과 옆에 {사례}_v{v}_cards.json · _cards.txt 를 쓴다 (LLM 다시 안 부름)"""
    import json
    from pathlib import Path
    p = Path(result_path)
    c = cards_from_result(json.loads(p.read_text(encoding="utf-8")))
    stem = p.name[: -len("_result.json")] if p.name.endswith("_result.json") else p.stem
    from law_agent.output.cards_html import render
    js, tx, ht = (p.with_name(stem + x) for x in ("_cards.json", "_cards.txt", "_cards.html"))
    js.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    tx.write_text(show_cards(c) + "\n", encoding="utf-8")
    ht.write_text(render(c, stem), encoding="utf-8")
    return [str(js), str(tx), str(ht)]


if __name__ == "__main__":      # python3 -m law_agent.output.plain <결과.json> [--detail] [--save]
    import json
    import sys
    for p in [a for a in sys.argv[1:] if not a.startswith("--")]:
        r = json.load(open(p, encoding="utf-8"))
        print(f"=== {r.get('case_id')} ===")
        print(show_cards(cards_from_result(r)))
        if "--save" in sys.argv:
            print("저장: " + " · ".join(save_cards(p)))
        if "--detail" in sys.argv:
            print()
            print(show(from_result(r)))
