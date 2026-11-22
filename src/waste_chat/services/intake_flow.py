"""기초 입력서로 시작하기 — 입력서(docx) → 빈칸 보충 질문 → 업체 찾기 + 법령 판단.

말로 찾기는 문장을 해석하는 방식에 따라 조건이 조금씩 달라질 수 있다. 입력서로 시작하면 정해진 칸의 값만 쓰고,
빈칸은 정해진 질문으로만 채우며(AI를 쓰지 않음), 그 값으로 업체 조건(품목·물량·상태)을 만든다.
보충 질문은 파트 1(phase2_rules)의 질문을 waste-chat 판단 사실(engine.FACT_KEYS)에 맞춰 옮긴 것이다.
"""
import re

from . import directory, engine, tables

UNKNOWN = "모름"

# (사실 키, 질문, 종류, 선택지, 왜 묻는지). 순서 = 묻는 순서. 선택형은 '모름'을 함께 보여 준다.
QUESTIONS = [
    ("name", "이 폐기물을 현장에서 뭐라고 부르나요? 다른 폐기물과 구분할 수 있게 적어 주세요.", "text", None,
     "업체 품목과 분류번호 후보를 찾는 기준입니다."),
    ("process", "이 폐기물은 어떤 작업에서 나오나요? 사용하는 원료·약품을 알면 함께 적어 주세요.", "area", None,
     "같은 물건이라도 발생 공정에 따라 분류번호와 지정폐기물 여부가 달라집니다."),
    ("form", "지금 어떤 상태인가요? 예: 마른 고체 조각, 액체, 진흙 같은 상태", "text", None,
     "고상·액상·반고상에 따라 받을 수 있는 업체와 운반 방법이 달라집니다."),
    ("components", "재질 이름이나 구성 비율을 알고 있나요? 예: PP 90%, 종이 라벨 10%. 모르면 추정하지 말고 ‘모름’을 고르세요.", "text", None,
     "섞인 재질은 성분마다 재활용 가능 여부를 따로 봅니다."),
    ("contamination", "기름·약품·음식물 등이 묻어 있나요?", "pick", ["깨끗", "오염"],
     "오염되면 재활용 유형이 좁아지거나 분석이 필요할 수 있습니다."),
    ("contamination_detail", "무엇이 얼마나 묻어 있나요? 예: 절삭유가 표면에 묻음", "text", None,
     "오염 물질에 따라 지정폐기물 의심 여부가 달라집니다."),
    ("hazard", "유해물질이 들어 있다고 알고 있나요? ‘아니오’도 분석으로 확인한 것이 아니라 현재 아는 내용입니다.", "pick", ["아니오", "예"],
     "지정폐기물이면 맡길 수 있는 업체와 서류가 완전히 달라집니다."),
    ("monthly_ton", "한 달에 대략 몇 톤 나오나요? 숫자만 적어 주세요. 예: 5 또는 0.3", "number", None,
     "업체의 처리 용량과 운반 횟수·탄소를 계산할 때 씁니다."),
    ("address", "이 폐기물이 나오는(보관된) 사업장 주소를 알려 주세요.", "text", None,
     "가까운 업체를 찾고 영업 구역을 확인할 때 씁니다."),
    ("facility_type", "이 폐기물이 나오는 시설이 배출시설(대기·수질 배출시설 허가·신고 대상)인가요?", "pick", ["배출시설계", "비배출시설계"],
     "운반업체마다 맡을 수 있는 폐기물(배출시설계/비배출시설계)이 다릅니다."),
]
LABEL = {k: engine.FACT_LABELS.get(k, k) for k, *_ in QUESTIONS}
WANT_DEFAULT = ["recycler", "hauler"]


def questions(facts):
    """비어 있는 칸의 보충 질문. 사용자가 '모름'으로 답한 칸은 다시 묻지 않는다."""
    out = []
    for key, text, kind, opts, why in QUESTIONS:
        if (facts.get(key) or "").strip():
            continue
        if key == "contamination_detail" and facts.get("contamination") != "오염":
            continue
        out.append({"key": key, "label": LABEL[key], "text": text, "kind": kind, "options": opts or [], "why": why})
    return out


def wastes_for(facts):
    """입력서 값으로 업체 검색 품목(directory.WASTES 묶음)을 정한다. 분류번호가 있으면 그 법정 명칭도 본다."""
    row = tables.code(facts.get("code"))
    text = facts.get("name", "") + " " + (row.get("명칭", "") if row else "")
    found = directory.find_wastes(text)  # 이름·분류번호가 먼저. 성분의 '종이 라벨' 같은 부재료로 품목이 늘지 않게
    if not found:
        found = directory.find_wastes(" ".join(facts.get(k, "") for k in ("components", "process")))
    if not found and row:  # 법정 명칭이 묶음 이름과 다른 경우: 표의 폐기물 종류 이름으로 다시 본다
        for group, kinds in tables.KINDS.items():
            if any(k.lstrip("=") in row.get("명칭", "") for k in kinds):
                found.append(group)
    return found[:4]


def item_view(item, profile):
    """입력서의 폐기물 한 건 → 화면에 보여 줄 사실·근거·보충 질문·분류번호 후보."""
    facts = {k: v for k, v in item["facts"].items() if k in engine.FACT_KEYS and v}
    if not facts.get("address") and profile.get("address"):
        facts["address"] = profile["address"]
    if profile.get("industry"):
        facts.setdefault("industry", profile["industry"])
    return {"index": item["index"], "facts": facts, "sources": item.get("sources", []), "mixture": item.get("mixture", ""),
            "history": item.get("history", ""), "code_note": item.get("code_note", ""), **extras(facts)}


def _major(components):
    """구성 성분 중 10% 이상(또는 비율 없는 것)만. '종이 라벨 2%' 같은 부재료로 다른 분류가 앞서지 않게."""
    keep = []
    for part in re.split(r"[,/+]|및", components or ""):
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", part)
        if not m or float(m.group(1)) >= 10:
            keep.append(re.sub(r"\d+(?:\.\d+)?\s*%", "", part))
    return " ".join(keep)


def extras(facts):
    codes = tables.find_codes(f"{facts.get('name', '')} {facts.get('process', '')}", 6) or tables.find_codes(f"{facts.get('name', '')} {_major(facts.get('components', ''))}", 6)
    check = tables.check_code(facts["code"])[1] if facts.get("code") else ""
    return {"questions": questions(facts), "codes": codes, "code_check": check, "wastes": wastes_for(facts),
            "labels": engine.FACT_LABELS}


def need_from(facts, wastes, want):
    """업체 찾기 조건. 입력서 값만 쓴다 (문장 해석 없음)."""
    need = directory.empty_need()
    need["wastes"] = [w for w in dict.fromkeys(wastes) if w in directory.WASTES][:6]
    need["want"] = [w for w in dict.fromkeys(want) if w in ("recycler", "hauler", "disposer")] or list(WANT_DEFAULT)
    if facts.get("monthly_ton"):
        need["quantity"] = f"월 {facts['monthly_ton']}톤"
    state = [facts.get("form", "")]
    if facts.get("contamination") == "오염":
        state.append("오염: " + (facts.get("contamination_detail") or "있음"))
    elif facts.get("contamination") == "깨끗":
        state.append("오염 없음")
    need["quality"] = directory.clean(" · ".join(s for s in state if s and s != UNKNOWN), 160)
    need["detail"] = directory.clean(facts.get("name", ""), 240)
    return need


def summary(facts, need, results, public, running, no_code):
    """거래 찾기 대화의 첫 답. 정해진 틀만 쓴다."""
    lines = [f"기초 입력서의 ‘{facts.get('name', '폐기물')}’ 내용으로 찾았습니다."]
    conds = [f"{s['label']} {s['value']}" for s in directory.need_summary(need)]
    if conds:
        lines.append("조건: " + " / ".join(conds) + ".")
    lines.append(f"가입 업체 {len(results)}곳, 공공자료 업체 {len(public)}곳을 지도에 표시했습니다.")
    if running:
        lines.append("법령 판단을 시작했습니다. 30~90초 뒤 ‘법령 판단’ 탭에 분류·처리 경로·필요 서류와 법령 기준으로 고른 업체 순위가 나옵니다.")
    elif no_code:
        lines.append("분류번호가 정해지지 않아 법령 판단은 아직 하지 않았습니다. ‘법령 판단’ 탭에서 후보를 고르거나, 확실하지 않으면 분류 확인 절차로 기관에 확인하세요.")
    unknown = [LABEL[k] for k, *_ in QUESTIONS if facts.get(k) == UNKNOWN]
    if unknown:
        lines.append("‘모름’으로 둔 칸(" + ", ".join(unknown) + ")은 판단 결과의 ‘확인할 것’에서 다시 안내합니다.")
    return " ".join(lines)


def clean_want(raw):
    return [w for w in (raw if isinstance(raw, list) else []) if w in ("recycler", "hauler", "disposer")]


def clean_wastes(raw):
    return [w for w in (raw if isinstance(raw, list) else []) if w in directory.WASTES]


def strip_unknown(facts):
    """'모름'은 판단 엔진에 빈칸으로 넘긴다 (엔진이 모르는 사실로 다루고 확인 질문을 만든다)."""
    return {k: v for k, v in facts.items() if v and v != UNKNOWN and not re.fullmatch(r"\s*", v)}


def site_point(address):
    """입력서의 배출 현장 주소 → 지도·거리 기준점(시·군·구 중심점). 못 찾으면 None."""
    reg = engine._region(address or "")
    if not reg or not reg.get("sido"):
        return None
    short = next((k for k, v in tables.SIDO.items() if reg["sido"].startswith(k) or
                  {"충청북도": "충북", "충청남도": "충남", "전라남도": "전남", "전북특별자치도": "전북", "경상북도": "경북",
                   "경상남도": "경남", "강원특별자치도": "강원", "제주특별자치도": "제주"}.get(reg["sido"]) == k), None)
    lat, lng = tables._place(short, (reg.get("sigungu") or "").split()[0])
    if lat is None:
        return None
    return {"lat": lat, "lng": lng, "label": " ".join(x for x in (reg["sido"], reg.get("sigungu")) if x)}


def at_site(me, need):
    """거리 계산용 '나': 입력서로 시작한 기록이면 업체 주소 대신 배출 현장 위치를 쓴다."""
    o = (need.get("_intake") or {}).get("origin")
    if not o:
        return me
    return {**me, "profile": {**me["profile"], "lat": o["lat"], "lng": o["lng"]}}
