"""법령에서 만든 표(분류번호·재활용 유형)와 공공자료 업체 목록. engine/tables 의 CSV를 읽기만 한다.

- 분류번호: 폐기물관리법 시행규칙 별표 4 (waste_codes_4.csv)
- 재활용 유형: 별표 4의3 (recycling_types_4_3.csv), R코드 설명: 별표 4의2
- 업체: 환경부·한국환경공단 공개 실적·업체현황을 정리한 표(partners_*.csv). 앱 가입 업체가 아니다.
"""
import csv
import math
import re
import threading
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from .paths import TABLES  # noqa: E402  파트 2 엔진과 같은 표
_LOCK = threading.Lock()
_T = {}

# 화면의 폐기물 묶음(directory.WASTES) → 공공자료 표의 폐기물 종류 이름
KINDS = {
    "폐합성수지": ("폐합성고분자화합물", "폐합성수지"),
    "폐목재": ("폐목재",),
    "폐금속": ("폐금속", "고철"),
    "폐지": ("폐지",),
    "폐유리": ("폐유리",),
    "폐유": ("=폐유", "폐식용유", "폐윤활유"),
    "폐유기용제": ("폐유기용제",),
    "폐산·폐알칼리": ("폐산", "폐알칼리"),
    "폐섬유": ("폐섬유", "폐의류"),
    "폐고무·폐타이어": ("폐타이어", "폐고무", "폐합성고무"),
    "유기성오니": ("유기성오니",),
    "식물성잔재물": ("동·식물성잔재물", "식물성", "왕겨"),
    "건설폐기물": ("건설",),
    "폐배터리": ("폐전지", "폐배터리"),
    "폐전기전자제품": ("폐전기전자",),
    "소각재·분진": ("분진", "연소잔재물", "광재", "소각재"),
}
DESIGNATED_KINDS = {"폐유", "폐유기용제", "폐산·폐알칼리"}
SIDO = {"서울": "11", "부산": "21", "대구": "22", "인천": "23", "광주": "24", "대전": "25", "울산": "26", "세종": "29",
        "경기": "31", "강원": "32", "충북": "33", "충남": "34", "전북": "35", "전남": "36", "경북": "37", "경남": "38",
        "제주": "39"}


def _read(name):
    with (TABLES / name).open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _load():
    with _LOCK:
        if _T:
            return _T
        codes = {r["분류번호"]: r for r in _read("waste_codes_4.csv")}
        rtypes = defaultdict(list)
        for r in _read("recycling_types_4_3.csv"):
            rtypes[r.get("분류번호", "")].append(r)
        rdesc = {}
        try:
            for r in _read("rcode_detail_4_2.csv"):
                key = r.get("r_code") or r.get("R코드") or next(iter(r.values()))
                rdesc[key] = r
        except (OSError, StopIteration):
            pass
        centers = {}
        for r in _read("sigungu_centers_2025_2Q.csv"):
            centers.setdefault(r["sig_cd"][:2], []).append(
                (re.sub(r"\s", "", r["sig_nm"]), float(r["centroid_latitude"]), float(r["centroid_longitude"])))
        _T.update(codes=codes, rtypes=rtypes, rdesc=rdesc, centers=centers, partners=None)
        return _T


# ── 분류번호 ───────────────────────────────────────────────────────────
def code(value):
    return _load()["codes"].get((value or "").strip())


def check_code(value):
    """(정상 여부, 안내 문구). 별표 4에 있는 말단(세분류) 번호인지 본다."""
    value = (value or "").strip()
    if not re.fullmatch(r"\d{2}-\d{2}(-\d{2})?", value):
        return False, "분류번호는 별표 4의 00-00-00 형식으로 적습니다."
    row = code(value)
    if not row:
        return False, f"{value}는 시행규칙 별표 4의 분류번호 목록에 없습니다."
    if row.get("말단") == "N":
        kids = [c for c, r in _load()["codes"].items() if r.get("상위") == value][:6]
        return False, (f"{value}({row['명칭']})는 상위 분류입니다. 세분류 번호를 적어야 합니다"
                       + (f" (예: {', '.join(kids)})." if kids else "."))
    return True, f"{value} {row['명칭']} · {row['폐기물_구분']}"


def find_codes(text, limit=8):
    """명칭에 들어간 말로 말단 분류번호 후보를 찾는다.
    - 낱말이 명칭 안에서 다른 낱말의 앞부분이면 세지 않는다('폐유'가 '폐유리'에 걸리지 않게)
    - 낱말이 상위 분류의 이름과 같으면 그 아래 번호를 올린다('폐유' → 06 폐유 아래)
    - 세 글자 이상 낱말은 두 글자 조각으로 설명(괄호 안)까지 본다('폐유압유' → '유압작동유')"""
    from .directory import WASTES  # 현장 말(PE 필름·고철 등)을 묶음 이름으로 바꿔 함께 찾는다
    low = (text or "").lower()
    words = re.findall(r"[가-힣A-Za-z]{2,}", text or "")
    for name, syns in WASTES.items():
        if any(s in low for s in syns):
            words += [name, *[k.lstrip("=") for k in KINDS.get(name, ()) if k.lstrip("=") == name or k.lstrip("=")[1:3] in low]]
    words = list(dict.fromkeys(words))
    codes = _load()["codes"]
    stems = set(words) | {w + "류" for w in words}  # '폐지' → 상위 분류 '폐지류'
    parents = {c for c, r in codes.items() if r.get("말단") == "N" and re.sub(r"\(.*", "", r["명칭"]).strip() in stems}

    def hit(w, name):
        for m in re.finditer(re.escape(w), name):
            after = name[m.end():m.end() + 1]
            if len(w) >= 3 or not re.match(r"[가-힣]", after):
                return True
        return False

    found = []
    for c, r in codes.items():
        if r.get("말단") != "Y" or r.get("폐기물_구분") == "생활폐기물":  # 사업장 거래이므로 생활폐기물 번호는 뺀다
            continue
        name = r["명칭"]
        head = re.sub(r"\(.*", "", name)
        score = sum(len(w) for w in words if hit(w, head) or head in w)
        score += sum(50 for w in words if head == w)  # 이름이 그대로 같으면 먼저
        score += sum(3 for w in words if len(w) >= 3 for g in {w.lstrip("폐")[i:i + 2] for i in range(len(w.lstrip("폐")) - 1)}
                     if len(g) == 2 and g in name)
        if any(c.startswith(p + "-") for p in parents):
            score += 20
        if score >= 3:
            found.append((score, c, r))
    found.sort(key=lambda x: (-x[0], x[2].get("지정폐기물") == "Y", x[1]))  # 같은 점수면 일반폐기물 번호 먼저
    return [{"code": c, "name": r["명칭"], "group": r["폐기물_구분"], "designated": r.get("지정폐기물") == "Y"}
            for _, c, r in found[:limit]]


def recycling_types(code_value):
    """별표 4의3에서 이 분류번호에 허용된 재활용 유형."""
    rows = _load()["rtypes"].get((code_value or "").strip(), [])
    out = []
    for r in rows:
        out.append({k: v for k, v in r.items() if v})
    return out


# ── 공공자료 업체 ──────────────────────────────────────────────────────
def _place(sido, sigungu):
    cands = _load()["centers"].get(SIDO.get(sido, ""), [])
    name = re.sub(r"\s", "", sigungu or "")
    for n, lat, lng in cands:
        if name and (n == name or n.startswith(name) or name.startswith(n)):
            return lat, lng
    if cands:  # 시·군·구를 못 찾으면 시·도 안 첫 지점 대신 평균
        return sum(c[1] for c in cands) / len(cands), sum(c[2] for c in cands) / len(cands)
    return None, None


def _partners():
    t = _load()
    if t["partners"] is not None:
        return t["partners"]
    out = {}

    def add(kind, r, extra):
        key = r["업체_키"] + "|" + kind
        lat, lng = _place(r.get("시도"), r.get("시군구"))
        row = out.setdefault(key, {
            "id": "pub:" + kind + ":" + r["업체_키"], "name": r["업체명"], "role": kind, "address": r.get("주소", ""),
            "sido": r.get("시도", ""), "sigungu": r.get("시군구", ""), "phone": r.get("전화번호", ""),
            "lat": lat, "lng": lng, "kinds": set(), "methods": set(), "rcodes": set(), "products": set(),
            "groups": set(), "year": r.get("기준연도", ""), "source": (r.get("출처") or "").split("#")[0], **extra})
        return row

    for r in _read("partners_recycling.csv"):
        if r.get("위탁가능") == "N":
            continue
        row = add("recycler", r, {"license": r.get("등록형태", "")})
        row["kinds"].add(r.get("폐기물종류", ""))
        if r.get("처리방법"):
            row["methods"].add(r["처리방법"])
        if r.get("r_code"):
            row["rcodes"].add(r["r_code"])
        if r.get("재생제품"):
            row["products"].add(r["재생제품"])
        row["groups"].add(r.get("폐기물_구분", ""))
    for r in _read("partners_transport.csv"):
        if r.get("영업중") == "N":
            continue
        row = add("hauler", r, {"license": "수집·운반업", "vehicles": r.get("보유차량", ""), "area": r.get("영업구역", "")})
        row["groups"].add(r.get("대상군", ""))
        row["kinds"].add(r.get("영업대상_원문", ""))
    for r in _read("partners_disposal.csv"):
        if r.get("영업중") == "N":
            continue
        row = add("disposer", r, {"license": r.get("업종", "")})
        row["groups"].add(r.get("대상군", ""))
        row["kinds"].add(r.get("영업대상_원문", ""))
        if r.get("소각방식"):
            row["methods"].add(r["소각방식"])
    t["partners"] = list(out.values())
    return t["partners"]


def _km(a, b):
    if None in (a[0], a[1], b[0], b[1]):
        return None
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _kind_hit(row, wastes):
    if not wastes:
        return True
    text = " ".join(row["kinds"])
    for w in wastes:
        for k in KINDS.get(w, (w,)):
            if k.startswith("="):
                if k[1:] in row["kinds"]:
                    return True
            elif k in text:
                return True
    return False


def search_partners(wastes, roles, origin=None, limit=8, exclude_names=()):
    """공공자료 업체 후보. 품목(재활용업체는 폐기물 종류, 운반업체는 대상군)과 역할·거리로 고른다."""
    roles = [r for r in roles if r in ("recycler", "hauler", "disposer")] or ["recycler"]
    designated = any(w in DESIGNATED_KINDS for w in wastes or [])
    construction = "건설폐기물" in (wastes or [])
    skip = {re.sub(r"\(주\)|㈜|주식회사|\s", "", n) for n in exclude_names}
    found = []
    for row in _partners():
        if row["role"] not in roles or re.sub(r"\(주\)|㈜|주식회사|\s", "", row["name"]) in skip:
            continue
        if row["role"] == "recycler" and not _kind_hit(row, wastes):
            continue
        if row["role"] == "hauler":
            groups = " ".join(row["groups"])
            if designated and "지정" not in groups:
                continue
            if construction and "건설" not in groups:
                continue
            if not designated and not construction and "생활·사업장일반" not in groups:
                continue
        if row["role"] == "disposer":
            groups = " ".join(row["groups"])
            if construction and "건설" not in groups:
                continue
            if designated and "지정" not in groups:
                continue
            if not construction and not designated and "생활·사업장일반" not in groups:
                continue
        km = _km((row["lat"], row["lng"]), origin) if origin else None
        found.append((km if km is not None else 9999, row))
    found.sort(key=lambda x: x[0])
    out = []
    per_role = defaultdict(int)
    for km, row in found:
        if per_role[row["role"]] >= limit:
            continue
        per_role[row["role"]] += 1
        out.append({**{k: (sorted(v)[:6] if isinstance(v, set) else v) for k, v in row.items()},
                    "distance_km": round(km, 1) if km < 9999 else None})
    return out


# ── 가입 업체 ↔ 공공자료 업체 연결 ────────────────────────────────────────
# 공공자료에는 사업자등록번호·허가번호가 없다. 업체명(정규화)·시군구·전화번호로 후보를 찾고, 업체가 직접 '우리 업체'를 고른다.
def norm_name(name):
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(합\)|[\s_·.,()]", "", unicodedata.normalize("NFKC", name or "")).lower()


def _digits(s):
    return re.sub(r"\D", "", s or "")


def public_index():
    """업체_키 → {key, name, sido, sigungu, address, phone, roles, licenses, kinds}"""
    t = _load()
    if t.get("pindex") is not None:
        return t["pindex"]
    out = {}
    for row in _partners():
        key = row["id"].split(":", 2)[2]
        x = out.setdefault(key, {"key": key, "name": row["name"], "sido": row["sido"], "sigungu": row["sigungu"],
                                 "address": row["address"], "phone": row["phone"], "roles": set(), "licenses": set(), "kinds": set()})
        x["roles"].add(row["role"])
        if row.get("license"):
            x["licenses"].add(row["license"])
        x["kinds"].update(k for k in row["kinds"] if k)
        x["phone"] = x["phone"] or row["phone"]
    t["pindex"] = out
    return out


def public_view(x):
    return {"key": x["key"], "name": re.sub(r"_+$", "", x["name"]).strip(), "region": f"{x['sido']} {x['sigungu']}".strip(), "address": x["address"],
            "phone": x["phone"], "roles": sorted(x["roles"]), "licenses": sorted(x["licenses"])[:4], "kinds": sorted(x["kinds"])[:6]}


def find_public(name, address="", phone="", limit=5):
    """가입 정보로 공공자료 업체 후보. 이름이 같거나 포함되는 곳 중 시군구·전화번호가 맞을수록 앞에."""
    n = norm_name(name)
    if len(n) < 2:
        return []
    addr = re.sub(r"\s", "", address or "")
    ph = _digits(phone)
    found = []
    for x in public_index().values():
        xn = norm_name(x["name"])
        if not xn:
            continue
        score = 10 if xn == n else 5 if (n in xn or xn in n) and min(len(n), len(xn)) >= 2 else 0
        if not score:
            continue
        sgg = re.sub(r"\s", "", x["sigungu"] or "")
        if sgg and sgg in addr:
            score += 5
        if ph and len(ph) >= 7 and ph[-7:] == _digits(x["phone"])[-7:]:
            score += 5
        found.append((score, x))
    found.sort(key=lambda s: -s[0])
    return [{**public_view(x), "score": sc} for sc, x in found[:limit]]


def _rec_rows():
    t = _load()
    if t.get("rec_by_key") is None:
        idx = defaultdict(list)
        for r in _read("partners_recycling.csv"):
            if r.get("위탁가능") != "N":
                idx[r["업체_키"]].append(r)
        t["rec_by_key"] = idx
    return t["rec_by_key"]


def public_passes(key, code, rcodes):
    """연결된 공공자료 업체가 이 분류번호(중분류)·재활용 유형으로 위탁 가능한 재활용 허가를 가졌는가. 근거 행 설명을 돌려준다."""
    mid = (code or "")[:5]
    for r in _rec_rows().get(key, []):
        if r.get("분류번호_중분류") == mid and (not rcodes or r.get("r_code") in rcodes):
            return f"{mid} {r.get('등록형태', '')} · {r.get('r_code', '')} {r.get('처리방법', '')}".strip()
    return ""
