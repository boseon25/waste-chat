"""업체 목록에서 거래 상대를 찾는다. 폐기물 용어 사전, 문장 해석(키 없이도 동작), 거리·품목 점수."""
import math
import re

from . import store

ROLES = {"emitter": "배출업체", "hauler": "수집·운반업체", "recycler": "재활용·가공업체", "disposer": "처분업체"}
DEAL_ROLES = {"emitter": "배출", "hauler": "운반", "processor": "처리"}

# 표준 품목명 → 문장에서 찾을 표현. 긴 표현을 먼저 대조한다.
WASTES = {
    "폐합성수지": ["폐합성수지", "합성수지", "폐플라스틱", "플라스틱", "폐비닐", "비닐", "필름", "사출 스크랩", "스크랩 수지",
              "pe", "pp", "pet", "ps", "abs", "pvc", "스티로폼", "폐스티로폼", "펠릿"],
    "폐목재": ["폐목재", "목재", "팔레트", "파렛트", "톱밥", "우드칩", "합판", "mdf"],
    "폐금속": ["폐금속", "고철", "철스크랩", "스크랩 철", "알루미늄", "구리", "비철", "스테인리스", "금속 스크랩", "절삭칩"],
    "폐지": ["폐지", "종이", "골판지", "박스", "폐박스", "지관"],
    "폐유리": ["폐유리", "유리", "유리병", "판유리"],
    "폐유": ["폐유", "폐윤활유", "윤활유", "절삭유", "폐절삭유", "유압유", "폐식용유"],
    "폐유기용제": ["폐유기용제", "유기용제", "용제", "신나", "시너", "세척액", "폐세척액", "ipa", "아세톤", "톨루엔"],
    "폐산·폐알칼리": ["폐산", "폐알칼리", "산세", "에칭액", "도금액", "폐황산", "폐염산", "가성소다"],
    "폐섬유": ["폐섬유", "섬유", "원단", "자투리 천", "폐의류", "부직포"],
    "폐고무·폐타이어": ["폐고무", "고무", "폐타이어", "타이어"],
    "유기성오니": ["오니", "슬러지", "폐수처리오니", "유기성오니", "공정오니"],
    "식물성잔재물": ["식물성잔재물", "커피박", "커피 찌꺼기", "왕겨", "쌀겨", "과일 껍질", "비지", "주정박"],
    "건설폐기물": ["건설폐기물", "폐콘크리트", "콘크리트", "폐아스콘", "아스콘", "혼합건설폐기물", "폐벽돌", "순환골재"],
    "폐배터리": ["폐배터리", "배터리", "이차전지", "2차전지", "리튬", "폐전지", "블랙매스"],
    "폐전기전자제품": ["폐전기전자", "폐가전", "pcb", "기판", "폐전선", "전선"],
    "소각재·분진": ["소각재", "분진", "비산재", "바닥재", "연소재", "슬래그", "광재"],
}
WANT_WORDS = [
    ("hauler", ["운반", "수거", "운송", "차량", "수집"]),
    ("disposer", ["소각", "매립", "처분", "폐기 처리"]),
    ("recycler", ["재활용", "가공", "원료", "재생", "리사이클", "재자원", "연료화", "선별", "파쇄"]),
    ("emitter", ["배출업체", "공급처", "공급받", "구하고", "구합니다", "구해요", "매입", "받고 싶", "수급", "원료를 찾"]),
]
QUANTITY = re.compile(r"(?:월|연|년|주|하루|일|매월|매주|매달)?\s*(?:약|대략|평균)?\s*\d[\d,.]*\s*(?:톤|t\b|kg|킬로|㎏|l\b|리터|㎥|m3|루베|개|대)"
                      r"(?:\s*(?:/|씩|정도|가량|이상|내외)?\s*(?:월|년|연|주|일|회))?", re.I)
QUALITY_WORDS = ["오염", "이물", "혼합", "단일", "깨끗", "세척", "분리", "선별", "수분", "색상", "투명", "유색", "라벨", "습기",
                 "순도", "등급", "압축", "베일", "분쇄", "건조"]


def clean(text, limit=300):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def find_wastes(text):
    low = text.lower()
    found = []
    for name, words in WASTES.items():
        for word in sorted(words, key=len, reverse=True):
            if re.fullmatch(r"[a-z]+", word):
                hit = re.search(r"(?<![a-z])" + word + r"(?![a-z])", low)
            else:
                hit = word in low
            if hit:
                found.append(name)
                break
    return found


def empty_need():
    return {"wastes": [], "want": [], "quantity": "", "quality": "", "detail": ""}


def read_need(message, previous=None, my_roles=()):
    """규칙 기반 해석. 이전 조건에 이번 문장에서 찾은 내용을 덧붙인다."""
    need = {**empty_need(), **(previous or {})}
    need["wastes"] = list(dict.fromkeys([*need["wastes"], *find_wastes(message)]))
    wanted = [role for role, words in WANT_WORDS if any(w in message for w in words)]
    if wanted:
        # 처리 방식 표현이 있으면 '원료' 같은 일반 표현보다 그 방식을 우선한다.
        need["want"] = list(dict.fromkeys(wanted))
    if not need["want"]:
        need["want"] = ["emitter"] if my_roles and "emitter" not in my_roles else ["recycler"]
    amount = QUANTITY.search(message)
    if amount:
        need["quantity"] = clean(amount.group(0), 60)
    quality = [s for s in re.split(r"[.,\n·]|(?<=다)\s|(?<=요)\s|(?<=고)\s", message) if any(w in s for w in QUALITY_WORDS)]
    if quality:
        need["quality"] = clean(" / ".join(q.strip() for q in quality), 160)
    if not need["detail"]:
        need["detail"] = clean(message, 240)
    return need


def clean_need(raw):
    """모델이나 브라우저가 보낸 조건에서 허용된 값만 남긴다."""
    need = empty_need()
    if not isinstance(raw, dict):
        return need
    if isinstance(raw.get("wastes"), list):
        need["wastes"] = [w for w in dict.fromkeys(raw["wastes"]) if w in WASTES][:6]
    if isinstance(raw.get("want"), list):
        need["want"] = [w for w in dict.fromkeys(raw["want"]) if w in ROLES][:4]
    for name, limit in (("quantity", 60), ("quality", 160), ("detail", 240)):
        if isinstance(raw.get(name), str):
            need[name] = clean(raw[name], limit)
    return need


def need_title(need):
    waste = " · ".join(need.get("wastes") or []) or "폐기물"
    return waste + (" " + need["quantity"] if need.get("quantity") else "")


def need_summary(need):
    parts = [("품목", " · ".join(need.get("wastes") or [])), ("물량", need.get("quantity")), ("품질·상태", need.get("quality")),
             ("찾는 상대", " · ".join(ROLES[w] for w in need.get("want") or []))]
    return [{"label": label, "value": value} for label, value in parts if value]


def distance_km(a, b):
    try:
        lat1, lng1, lat2, lng2 = map(math.radians, (float(a["lat"]), float(a["lng"]), float(b["lat"]), float(b["lng"])))
    except (KeyError, TypeError, ValueError):
        return None
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)), 1)


def handled_wastes(profile):
    """수탁 업체는 영업대상 품목, 배출업체는 발생 품목."""
    return list(dict.fromkeys([*(profile.get("permit_wastes") or []), *(profile.get("wastes") or [])]))


def public(company, partner=False, origin=None):
    p = company["profile"]
    view = {"id": company["id"], "name": p.get("name", ""), "roles": p.get("roles", []), "address": p.get("address", ""),
            "lat": p.get("lat"), "lng": p.get("lng"), "industry": p.get("industry", ""), "wastes": handled_wastes(p),
            "methods": p.get("methods", ""), "capacity": p.get("capacity", ""), "vehicles": p.get("vehicles", ""),
            "permit_no": p.get("permit_no", ""), "intro": p.get("intro", ""), "demo": company["demo"]}
    if origin:
        view["distance_km"] = distance_km(origin, p)
    if partner:
        for name in ("ceo", "reg_no", "phone", "manager", "manager_phone", "permit_authority", "process"):
            view[name] = p.get(name, "")
    return view


def match(need, me, exclude=(), limit=12):
    """조건에 맞는 업체를 점수순으로 돌려준다. 허가 범위의 적법성은 판단하지 않는다."""
    wanted = set(need.get("want") or [])
    wastes = set(need.get("wastes") or [])
    found = []
    for other in store.companies():
        if other["id"] == me["id"] or other["id"] in exclude:
            continue
        p = other["profile"]
        roles = set(p.get("roles") or [])
        if wanted and not roles & wanted:
            continue
        handles = set(handled_wastes(p))
        overlap = wastes & handles
        if wastes and not overlap:
            continue
        view = public(other, origin=me["profile"])
        km = view.get("distance_km")
        view["score"] = 10 * len(overlap) + 3 * len(roles & wanted) - (km or 150) / 40
        view["match_role"] = next((r for r in ("recycler", "disposer", "hauler", "emitter") if r in roles & wanted),
                                  next(iter(roles), ""))
        reason = []
        if overlap:
            reason.append(" · ".join(sorted(overlap)) + " 취급")
        if p.get("methods"):
            reason.append(p["methods"])
        view["why"] = ", ".join(reason)
        found.append(view)
    found.sort(key=lambda v: -v["score"])
    return found[:limit]


def deal_role(company, hint=None):
    """거래방 안에서 맡는 역할. 초대할 때 지정한 역할이 있으면 그것을 쓴다."""
    if hint in DEAL_ROLES:
        return hint
    roles = company["profile"].get("roles") or []
    if "emitter" in roles:
        return "emitter"
    if "recycler" in roles or "disposer" in roles:
        return "processor"
    return "hauler" if "hauler" in roles else "emitter"


def role_for_match(match_role):
    return {"hauler": "hauler", "recycler": "processor", "disposer": "processor", "emitter": "emitter"}.get(match_role)


# ── 예시 업체 ─────────────────────────────────────────────────────────
DEMO_PASSWORD = "demo1234"
_DEMO = [
    ("daon", "다온산업", ["emitter"], "경기 시흥시 공단1대로 (예시 주소)", 37.345, 126.738, "플라스틱 사출 성형",
     dict(wastes=["폐합성수지"], process="사출 성형 후 스프루·런너, 불량품, PE 포장필름")),
    ("hanbit", "한빛리사이클", ["recycler"], "경기 화성시 팔탄면 (예시 주소)", 37.158, 126.905, "폐기물 종합재활용업",
     dict(permit_wastes=["폐합성수지"], methods="분쇄·세척·압출로 재생 펠릿 제조 (PE·PP)", capacity="40톤/일", vehicles="5톤 집게차 2대")),
    ("daesung", "대성플라텍", ["recycler"], "인천 서구 가좌동 (예시 주소)", 37.492, 126.675, "폐기물 중간재활용업",
     dict(permit_wastes=["폐합성수지"], methods="PET·PS 분쇄·세척 플레이크 제조", capacity="25톤/일")),
    ("cheongsol", "청솔에너지", ["recycler"], "충남 당진시 송악읍 (예시 주소)", 36.905, 126.69, "폐기물 종합재활용업",
     dict(permit_wastes=["폐합성수지", "폐목재", "폐섬유"], methods="고형연료제품(SRF) 제조", capacity="120톤/일")),
    ("greenwood", "그린우드자원", ["recycler"], "경기 평택시 포승읍 (예시 주소)", 36.985, 126.87, "폐기물 중간재활용업",
     dict(permit_wastes=["폐목재"], methods="파쇄·선별 후 우드칩 제조", capacity="60톤/일")),
    ("donghae", "동해메탈", ["recycler"], "경북 포항시 남구 (예시 주소)", 35.985, 129.39, "폐기물 종합재활용업",
     dict(permit_wastes=["폐금속", "폐전기전자제품"], methods="선별·압축·절단 후 제강 원료 공급", capacity="200톤/일")),
    ("mirae", "미래용제재생", ["recycler"], "경기 시흥시 정왕동 (예시 주소)", 37.338, 126.72, "지정폐기물 종합재활용업",
     dict(permit_wastes=["폐유기용제", "폐유"], methods="증류 정제로 재생 용제·재생유 제조", capacity="30톤/일")),
    ("namdo", "남도제지자원", ["recycler"], "전북 군산시 소룡동 (예시 주소)", 35.975, 126.64, "폐기물 중간재활용업",
     dict(permit_wastes=["폐지"], methods="선별·압축 후 제지 원료 공급", capacity="80톤/일")),
    ("jungbu", "중부순환골재", ["recycler"], "충북 청주시 흥덕구 (예시 주소)", 36.64, 127.39, "건설폐기물 중간처리업",
     dict(permit_wastes=["건설폐기물"], methods="파쇄·선별로 순환골재 생산", capacity="1,500톤/일")),
    ("seohae", "서해유리자원", ["recycler"], "인천 남동구 고잔동 (예시 주소)", 37.395, 126.705, "폐기물 중간재활용업",
     dict(permit_wastes=["폐유리"], methods="색상 선별·파쇄 후 유리 원료(컬릿) 공급", capacity="50톤/일")),
    ("daegyeong", "대경배터리리사이클", ["recycler"], "대구 달성군 구지면 (예시 주소)", 35.66, 128.42, "폐기물 종합재활용업",
     dict(permit_wastes=["폐배터리"], methods="방전·파쇄 후 블랙매스 회수", capacity="20톤/일")),
    ("hanul", "한울소각", ["disposer"], "울산 남구 용잠동 (예시 주소)", 35.495, 129.375, "폐기물 중간처분업(소각)",
     dict(permit_wastes=["폐합성수지", "폐유기용제", "폐유", "유기성오니", "폐섬유"], methods="고온 소각", capacity="96톤/일")),
    ("gyeonggi_srf", "경기에코소각", ["disposer"], "경기 안산시 단원구 (예시 주소)", 37.305, 126.79, "폐기물 중간처분업(소각)",
     dict(permit_wastes=["폐합성수지", "폐목재", "폐섬유", "유기성오니"], methods="일반 소각", capacity="72톤/일")),
    ("saegil", "새길환경운수", ["hauler"], "경기 안산시 상록구 (예시 주소)", 37.31, 126.86, "폐기물 수집·운반업",
     dict(permit_wastes=["폐합성수지", "폐목재", "폐금속", "폐지", "폐유리", "폐섬유"], methods="사업장 일반폐기물 수집·운반",
          vehicles="암롤 25톤 3대, 집게차 5톤 4대")),
    ("bukyung", "부경환경물류", ["hauler"], "부산 강서구 녹산동 (예시 주소)", 35.09, 128.86, "지정폐기물 수집·운반업",
     dict(permit_wastes=["폐유기용제", "폐유", "폐산·폐알칼리", "폐합성수지"], methods="지정폐기물 수집·운반 (탱크로리·밀폐형)",
          vehicles="탱크로리 16톤 2대, 윙바디 5톤 3대")),
    ("gwangju", "광주에코운송", ["hauler"], "광주 광산구 하남동 (예시 주소)", 35.19, 126.8, "폐기물 수집·운반업",
     dict(permit_wastes=["폐합성수지", "폐지", "폐금속", "건설폐기물"], methods="사업장·건설폐기물 수집·운반", vehicles="덤프 15톤 2대, 집게차 3대")),
]


def seed_demo():
    """처음 실행할 때 예시 업체를 넣는다. 모두 가상의 자료이며 화면에 ‘예시’로 표시된다."""
    if store.one("SELECT id FROM company WHERE demo=1 LIMIT 1"):
        return
    for n, (login, name, roles, address, lat, lng, industry, extra) in enumerate(_DEMO, 1):
        profile = {"name": name, "roles": roles, "address": address, "lat": lat, "lng": lng, "industry": industry,
                   "reg_no": f"000-00-{n:05d}", "ceo": "예시 대표", "phone": f"000-0000-{n:04d}",
                   "manager": "예시 담당자", "manager_phone": f"000-0000-{1000 + n}", "intro": "화면 시험용 예시 업체입니다.",
                   "permit_no": "" if roles == ["emitter"] else f"예시-제{2020 + n % 5}-{n:03d}호",
                   "permit_authority": "" if roles == ["emitter"] else "예시 관할청", **extra}
        store.create_company(login, DEMO_PASSWORD, profile, demo=True, company_id="demo_" + login)


def demo_accounts():
    return [{"login": c["login"], "name": c["profile"]["name"], "roles": c["profile"]["roles"]}
            for c in store.companies() if c["demo"]]
