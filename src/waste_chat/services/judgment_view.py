"""법령 판단 결과 → 사용자가 한눈에 읽는 화면 요약. 정해진 문장 틀에 판단 값만 끼워 넣는다(AI를 쓰지 않음).

순서: 결론(신호등) → 지금 할 일(모자란 사실, 바로 답하기) → 맡길 곳 → 준비할 서류 → 근거(접기).
같은 판단이면 늘 같은 문장이 나오고, 판단에 없는 말은 넣지 않는다. 원문·모델 의견은 근거 자리에 그대로 둔다.
"""
import re

from . import engine, tables

# 엔진이 말하는 '필요한 사실' → 우리 입력 칸. kind: text/area/number/pick
FACT_OF = {
    "발생 공정": "process", "성분": "components", "폐기물 구성 성분": "components", "구성 폐기물": "components",
    "폐기물 종류": "code", "월 평균 배출량": "monthly_ton", "정확한 폐기물 분류번호": "code", "분류번호": "code",
    "다른 폐기물과의 혼합 여부": "mixture", "혼합 여부": "mixture", "분리 가능 여부": "separable",
    "혼합 폐기물의 분리 가능 여부": "separable", "사업장폐기물 해당 여부(배출 사업장의 종류)": "facility_type",
    "성상": "form", "형태": "form", "오염": "contamination", "유해물질": "hazard",
}
FIELD_OF = {"designated_analysis": "analysis", "analysis": "analysis", "hazardous_suspect": "hazard", "emitter_facility_type": "facility_type",
            "quantity": "monthly_ton", "classification": "code", "waste_code": "code", "components": "components",
            "foreign_matter": "separable", "process_description": "process", "material_form": "form", "region": "address"}
ASK = {  # 칸마다 쉬운 질문과 답 방식
    "process": ("이 폐기물이 어떤 작업에서, 어떤 원료·약품을 써서 나오는지 조금 더 자세히 적어 주세요.", "area", []),
    "components": ("무엇이 몇 % 섞여 있는지 적어 주세요. 모르면 ‘모름’을 누르세요. 예: PP 70%, ABS 30%", "text", []),
    "name": ("현장에서 부르는 이름과 원래 무엇이었는지(제품·부품)를 적어 주세요.", "text", []),
    "monthly_ton": ("한 달 평균 몇 톤 나오나요? 숫자만 적어 주세요.", "number", []),
    "code": ("분류번호(별표 4)를 알고 있나요? 확실하지 않으면 ‘분류 확인’ 절차로 기관에 확인합니다.", "text", []),
    "mixture": ("다른 폐기물과 섞여 있나요?", "pick", ["단일", "혼합"]),
    "separable": ("섞여 있다면 현장에서 나눠 담을 수 있나요?", "pick", ["가능", "불가능"]),
    "facility_type": ("이 폐기물이 대기·수질 배출시설(허가·신고 대상)에서 나오나요?", "pick", ["배출시설계", "비배출시설계"]),
    "form": ("지금 어떤 상태인가요? 예: 마른 고체 조각, 액체, 진흙", "text", []),
    "hazard": ("유해물질이 들어 있나요? 성분 분석 결과서가 있으면 그 결과로 답해 주세요.", "pick", ["아니오", "예"]),
    "analysis": ("분석기관의 성분 분석 결과서가 있나요? ‘유해물질 없음’이라는 진술만으로는 지정폐기물이 아니라고 확정할 수 없어, "
                 "분석 결과가 있어야 이 업체들에 맡길 수 있습니다.", "pick", ["있음·지정 아님", "있음·지정 해당", "없음"]),
    "contamination": ("기름·약품 등이 묻어 있나요?", "pick", ["깨끗", "오염"]),
    "address": ("이 폐기물이 나오는 사업장 주소(시·군·구 포함)를 적어 주세요.", "text", []),
}
WHY = {  # 판단 질문 → 쉬운 말
    "A-1": "분류번호 확인", "A-2": "지정폐기물 여부", "A-3": "폐기물 해당 여부", "A-4": "섞인 폐기물의 분류",
    "B-1": "재활용 금지 여부", "B-2": "재활용 제한 여부", "C-1": "순환자원 해당 여부", "D-1": "허용되는 재활용 방법",
    "D-2": "재활용 사전 분석", "D-3": "재활용 기준", "D-4": "재활용 업체 준수사항", "D-5": "맡길 수 있는 허가",
    "E-1": "처분 방법", "F-1": "운반 기준", "G-1": "위탁 절차", "G-2": "올바로 입력 대상", "G-3": "운반 기준",
    "H-1": "배출자 신고 대상", "H-2": "처리계획 확인 대상", "H-3": "처리계획 제출 서류",
}
KIND_PLAIN = {"R-": "재활용", "D-": "처분"}
ALWAYS_ASKED = {"facility_type", "analysis"}  # 엔진(by_transport)이 사실 입력과 관계없이 내는 질문


def _rname(code):
    """R코드 → 별표 4의2 설명(짧게)."""
    row = tables._load()["rdesc"].get(code) or {}
    text = re.sub(r"\(.*?\)", "", row.get("설명", "")).strip()
    return text[:50]


def _clean_title(t):
    t = re.sub(r"\s*\((R|D)-[\d-]+\)", lambda m: "", t or "").strip()
    return t


def _verdict(r, route, todo):
    bad = r.get("code_mismatch")
    if bad:
        return {"tone": "bad", "title": "분류번호부터 다시 확인하세요",
                "why": f"입력한 {bad['code']}({bad['code_name']})는 ‘{bad['name']}’과 맞지 않는다고 판단했습니다. 이 번호로 낸 경로·업체·서류는 쓸 수 없습니다."}
    if not route:
        reason = "; ".join(str(x.get("사유") or x.get("reason") or x)[:120] for x in (r.get("none") or [])[:2]) if r.get("none") else ""
        return {"tone": "bad", "title": "지금 정보로는 맡길 곳을 정하지 못했습니다",
                "why": (reason + " " if reason else "") + ("아래 질문에 답하면 다시 판단합니다." if todo else "관할 기관 확인이 필요합니다.")}
    legal = route.get("legal") or ""
    kind = route.get("kind") or _clean_title(route.get("title"))
    rc = next(iter(re.findall(r"R-\d+(?:-\d+)?", route.get("title") or "")), "")
    how = f"‘{kind}’" + (f"(별표 4의2 {rc}: {_rname(rc)})" if rc and _rname(rc) else "")
    if re.search(r"불가|금지", legal):
        return {"tone": "bad", "title": "이 경로로는 맡길 수 없습니다", "why": f"{how} 경로가 법령상 {legal}으로 판단됐습니다."}
    if "조건" in legal:
        n = len([c for c in route.get("conditions", []) if not _internal(c)])
        return {"tone": "warn", "title": f"조건을 지키면 맡길 수 있어요" + (f" (조건 {n}개)" if n else ""),
                "why": f"{how}으로 맡기는 경로입니다. 조건은 아래 ‘근거 보기’에 있습니다."}
    held = list(dict.fromkeys(WHY.get((c["id"] or "").split(".")[0], "기타 판단") for c in r.get("checks") or [] if c.get("status") != "모델 합의"))
    if todo:
        return {"tone": "warn", "title": f"아래 {len(todo)}가지를 확인하면 확정됩니다",
                "why": f"{how}으로 맡기는 경로가 가장 유력합니다. 확인 전까지는 잠정 결과입니다."}
    if "확인" in legal:
        return {"tone": "warn", "title": "유력한 경로를 찾았습니다" + (f" · 담당자 확인 {len(held)}가지가 남았습니다" if held else ""),
                "why": f"{how}으로 맡기는 경로입니다. " + (f"‘{'’, ‘'.join(held[:4])}’" + (f" 외 {len(held) - 4}가지" if len(held) > 4 else "") + " 판단은 AI 모델 의견이 갈리거나 근거가 모자라 담당자·관할 기관 확인이 필요합니다(근거 보기에서 내용 확인)."
                                                        if held else "일부 판단이 조건부입니다. 근거 보기에서 조건을 확인하세요.")}
    return {"tone": "ok", "title": "맡길 수 있어요", "why": f"{how}으로 맡기는 경로가 법령 기준에 맞습니다."}


def _internal(text):
    """모델 내부 기록(‘…은(는) 다음 사실이 없어 판단을 보류함’)은 사용자 조건이 아니다."""
    return bool(re.search(r"판단을 보류함|확정되지 않음 \(A-|이 번호 기준의 잠정 결과|openai/|anthropic/|google/|claude-|gpt-|gemini", text or ""))


def _todo(r, facts):
    """모자란 사실을 입력 칸 기준으로 합친다. 같은 칸을 여러 판단이 원하면 한 번만 묻고 '어디에 쓰이는지'를 모은다."""
    items = {}

    def add(key, why, guide=None, raw="", asked=True):
        if key not in ASK:
            return
        if not asked and (facts.get(key) or "").strip() and key not in items:
            return  # 이미 입력한 사실은 판단 내부의 '모자란 사실' 목록만으로 다시 묻지 않는다
        it = items.setdefault(key, {"key": key, "label": engine.FACT_LABELS.get(key, key), "ask": ASK[key][0],
                                    "kind": ASK[key][1], "options": ASK[key][2], "why": [], "guide": guide,
                                    "current": facts.get(key, ""), "raw": raw})
        if why and why not in it["why"]:
            it["why"].append(why)
        if guide and not it["guide"]:
            it["guide"] = guide

    for q in r.get("questions") or []:
        field = q.get("field") or ""
        qid, _, fact = field.partition(".")
        why = WHY.get(qid, "")
        key = FACT_OF.get(fact) if fact else FIELD_OF.get(field.split(".")[0])
        if not key and fact:  # 처음 보는 사실 이름 → 글자로 짐작
            key = next((v for k, v in FACT_OF.items() if k in fact), None)
        if key in ALWAYS_ASKED and (facts.get(key) or "").strip():
            continue  # 엔진이 입력과 관계없이 늘 묻는 질문 — 이미 답했으면 다시 묻지 않는다
        if key:
            add(key, why or ("지정폐기물 여부" if key in ("hazard", "analysis") else "운반 범위" if key == "facility_type" else ""),
                q.get("guide"), q.get("text", ""))
    for c in r.get("checks") or []:  # 보류된 판단의 모자란 사실도
        for m in c.get("missing") or []:
            key = FACT_OF.get(m) or next((v for k, v in FACT_OF.items() if k in m), None)
            if key:
                add(key, WHY.get(c["id"], ""), asked=False)
    order = ["code", "analysis", "hazard", "mixture", "separable", "components", "process", "monthly_ton", "facility_type", "form", "name", "address"]
    return sorted(items.values(), key=lambda x: order.index(x["key"]) if x["key"] in order else 99)


def _partner(p):
    why = []
    for w in p.get("why") or []:
        w = re.sub(r"^(\d{2}-\d{2})(-\d{2})? 위탁 재활용 허가", lambda m: "이 분류번호 재활용 허가", w)
        w = re.sub(r"처리방법 \d+ ", "", w)
        why.append(w)
    rcodes = [rc for rc in (p.get("rcodes") or [])][:3]
    return {"rank": p.get("rank"), "key": p.get("key"), "name": re.sub(r"_[^_]*$|\s+$", "", p.get("name") or ""), "role": p.get("role"), "region": p.get("region"),
            "phone": p.get("phone"), "km": p.get("km"), "carbon": p.get("carbon"),
            "license": p.get("license") or "", "does": rcodes, "why": why,
            "evidence": p.get("evidence") or [], "cautions": p.get("cautions") or [], "ask": p.get("ask") or [],
            "road_km": p.get("road_km"), "distance_basis": p.get("distance_basis") or ""}


def _next(n):
    role, memo = n.get("역할", ""), n.get("메모") or ""
    if "협력 확정" in memo:
        return f"{role}: 처리를 맡을 업체가 정해진 뒤 그 업체와 맞춰 고릅니다."
    return f"{role}: {memo}" if memo else f"{role}: 다음 단계에서 고릅니다."


# 서류 이름 → 누가 쓰는가(거래방 서류 목록 deal_docs와 같은 기준). (표시, 설명)
WHO = [
    (r"위탁\s*계약", ("함께 작성", "배출업체와 운반·처리업체가 함께 정하고 서명해요")),
    (r"수탁\s*처리\s*능력|수탁\s*확인", ("함께 작성", "배출업체가 폐기물 정보를 적고, 처리·운반업체가 허가·능력을 채워 서명해요")),
    (r"올바로|전자\s*인계", ("각자 입력", "배출·운반·처리업체가 각자 자기 단계를 같은 인계번호로 입력해요")),
    (r"배출자\s*신고", ("우리 업체만", "배출업체가 관할 시·군·구에 내요")),
    (r"처리\s*계획", ("우리 업체가 제출", "배출업체가 내고, 처리업체의 수탁확인서를 붙여요")),
    (r"분석\s*결과", ("분석기관 발급", "폐기물분석전문기관이 발급한 결과서를 받아요")),
    (r"유해성", ("우리 업체가 제공", "배출업체가 작성해 첫 인계 전에 수탁업체에 줘요")),
]


def _who(title):
    return next((w for pat, w in WHO if re.search(pat, title or "")), ("", ""))


def _docs(r):
    out = []
    for d in r.get("docs") or []:
        why = d.get("why") or ""
        why = re.sub(r"\s*\(관문\s*\d+\)", "", why)
        why = re.sub(r"\s*/\s*사례:\s*None", "", why).replace("None", "").strip(" /")
        why = re.sub(r"^지정\s+(\d{2}-\d{2}-\d{2})", r"분류번호 \1", why)
        why = re.sub(r"/\s*사례:\s*([\d.]+)", r"/ 이번 폐기물: 1일 평균 약 \1 kg", why)
        sure = (d.get("status") or "").startswith("AI 판단")
        who, who_note = _who(d["title"])
        out.append({"title": d["title"], "why": why, "basis": d.get("basis") or "", "sure": sure, "who": who, "who_note": who_note,
                    "status": "필요" if sure else "필요할 수 있음(확인 전)"})
    return out


def build(r, facts):
    if not r:
        return None
    route = (r.get("routes") or [None])[0]
    todo = _todo(r, facts or {})
    checks = r.get("checks") or []
    agreed = sum(1 for c in checks if c.get("status") == "모델 합의")
    status = r.get("status")
    code = r.get("code") or ""
    row = tables.code(code) or {}
    a2 = next((c for c in checks if c["id"] == "A-2"), None)
    designated = ("지정폐기물로 판단" if a2 and a2["answer"] == "예" else "일반폐기물로 판단" if a2 and a2["answer"] == "아니오"
                  else "지정폐기물 여부는 분석으로 확인 필요" if a2 else ("지정폐기물" if status == "designated" else "일반폐기물"))
    routes, seen = [], set()
    for rt in r.get("routes") or []:
        sig = (rt.get("title"), tuple(p.get("name") for p in rt.get("partners") or []))
        if sig in seen:
            continue
        seen.add(sig)
        routes.append({"title": rt.get("kind") or _clean_title(rt.get("title")), "legal": rt.get("legal"),
                       "partners": [] if r.get("code_mismatch") else [_partner(p) for p in rt.get("partners") or []],
                       "partner_total": rt.get("partner_total"), "hidden": len(rt.get("partners") or []) if r.get("code_mismatch") else 0,
                       "conditions": [c for c in rt.get("conditions") or [] if not _internal(c)], "laws": rt.get("laws") or [],
                       "next": [_next(n) for n in rt.get("next_roles") or []
                                if n.get("역할") and n["역할"] not in {p.get("role") for p in rt.get("partners") or []}]})
    ft = (facts or {}).get("facility_type")
    if ft in ("배출시설계", "비배출시설계"):
        for rt in routes:
            rt["next"] = [n + f" 운반업체는 ‘{ft}’ 폐기물을 받는 곳으로 고르세요." if n.startswith("운반") else n for n in rt["next"]]
    return {"verdict": _verdict(r, route, todo),
            "basis_line": " · ".join(x for x in [f"분류번호 {code} {re.sub(r'[(].*', '', row.get('명칭', ''))}".strip() if code else "",
                                                 designated] if x),
            "trust": f"판단 {len(checks)}개 중 {agreed}개는 여러 AI 모델이 법령 원문을 인용해 합의했고, {len(checks) - agreed}개는 정보 부족·의견 차이로 보류됐습니다.",
            "todo": todo, "routes": routes, "docs": _docs(r), "mismatch": r.get("code_mismatch"), "code": code,
            "cards": r.get("cards"), "steps": r.get("steps") or []}
