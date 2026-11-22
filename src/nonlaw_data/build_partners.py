"""build_partners.py — 비법령 데이터(상대 기업) 정제

    python build_partners.py recycling "폐기물 재활용실적 및 업체현황(2024년).xlsx"
    python build_partners.py demand    "관문1 수요기업 후보.xlsx"
    python build_partners.py facilities data/nonlaw/raw/처리업체        # 처분업·운반업 엑셀이 든 폴더
    python build_partners.py standard  "전국폐기물처리업소표준데이터.xls"  # 좌표 + facility_master 생성
    python build_partners.py master                                      # 업체 통합만 다시

결과는 최신 data/staged/{시행일}/ 에 법령 표와 함께 들어간다 → build_tables.py diff / regress / promote 그대로 사용.

recycling (한국환경공단 「폐기물 재활용실적 및 업체현황」 부록 업체정보 6개 시트)
  → partners_recycling.csv   업체 × 폐기물코드(4자리) × 처리방법코드 한 줄씩
  → waste_code_crosswalk.csv 4자리 코드(5103·0101·9101) ↔ 분류번호(51-03-xx 등)
  - 처리방법 "(2003)원료 제조(재)(위탁)" → 처리방법코드 2003, 자가/위탁, R코드(R-3, 상위 수준)
  - 업체명 정규화: (주)·주식회사·㈜·(유)·유한회사 등 법인 표기와 공백 제거 → 업체_키
demand (관문 1 수요기업 후보, 팩토리온 기반 팀 정제본 '전체' 시트)
  → demand_designated.csv    품목·분류번호·용도 R코드를 active 지정 고시 표와 대조
"""
import os, csv, re, sys, json, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
# 경로: 코드는 src/<폴더>/, 데이터는 <프로젝트>/data/ (환경변수 WASTE_DATA_DIR 로 바꿀 수 있음)
DATA_ROOT = Path(os.environ.get("WASTE_DATA_DIR") or Path(__file__).resolve().parents[2] / "data")
LAW_DIR, TABLES = DATA_ROOT / "law", DATA_ROOT / "tables"   # 법령 원문·발췌 / 공용 판정표(staged·active·old)
STAGED, ACTIVE = TABLES / "staged", TABLES / "active"
RAW = DATA_ROOT / "nonlaw" / "raw"   # 원문 엑셀 보관 위치

SHEETS = {  # 시트 이름 → (허가/신고, 폐기물 구분)
    "부록 업체정보_허가_코드별_일반": ("허가", "사업장일반"),
    "부록 업체정보_허가_코드별_지정": ("허가", "지정"),
    "부록 업체정보_허가_코드별_생활": ("허가", "생활"),
    "부록 업체정보_신고_코드별 일반": ("신고", "사업장일반"),
    "부록 업체정보_신고_코드별 지정": ("신고", "지정"),
    "부록 업체정보_신고_코드별 생활": ("신고", "생활"),
}
RX_METHOD = re.compile(r"\((\d{4})\)\s*([^()]*?)\s*(?:\(재\)\s*)?\((자가|위탁)\)")  # R-11 은 "(재)" 없이 나옴
LEGAL = r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(합\)|합자회사|\(사\)|사단법인|\(재\)|재단법인|농업회사법인|영농조합법인|\(합명\)"


def norm_name(s):
    s = re.sub(LEGAL, "", s or "")
    return re.sub(r"[\s·ㆍ.,'\"]+", "", s)


def core_name(s):
    return re.sub(r"\([^)]*\)$", "", norm_name(s))  # 끝의 (지점명)·(처리) 등 제거


# 시도 표기 통일: 업체 표 전체를 짧은 이름("전북")으로 맞춘다
SIDO_SHORT = {
    "서울특별시": "서울", "서울시": "서울", "서울": "서울",
    "부산광역시": "부산", "부산시": "부산", "부산": "부산",
    "대구광역시": "대구", "대구시": "대구", "대구": "대구",
    "인천광역시": "인천", "인천시": "인천", "인천": "인천",
    "광주광역시": "광주", "광주": "광주",
    "대전광역시": "대전", "대전시": "대전", "대전": "대전",
    "울산광역시": "울산", "울산시": "울산", "울산": "울산",
    "세종특별자치시": "세종", "세종시": "세종", "세종": "세종",
    "경기도": "경기", "경기": "경기",
    "강원도": "강원", "강원특별자치도": "강원", "강원": "강원",
    "충청북도": "충북", "충북": "충북", "충청남도": "충남", "충남": "충남",
    "전라북도": "전북", "전북특별자치도": "전북", "전북": "전북",
    "전라남도": "전남", "전남": "전남",
    "경상북도": "경북", "경북": "경북", "경상남도": "경남", "경남": "경남",
    "제주특별자치도": "제주", "제주도": "제주", "제주": "제주",
}


GWANGJU_GU = {"동구", "서구", "남구", "북구", "광산구"}
MERGED_SIDO = {"전남광주통합특별시"}   # 2026 통합 행정구역 표기. 다른 업체 표는 전남·광주를 따로 쓰므로 시군구로 나눈다


def norm_sido(s, next_token=""):
    """시도 이름이면 짧은 표기, 아니면 "" (구·도로명 등은 시도가 아님)"""
    s = (s or "").strip()
    if s in MERGED_SIDO:
        return "광주" if next_token in GWANGJU_GU else "전남"
    return SIDO_SHORT.get(s, "")


def region(addr):
    """주소 → (시도, 시군구). 첫 단어가 시도 이름일 때만 시도로 잡는다.
    "덕진구 팔복로 167"처럼 시도가 빠진 주소는 시도 "" (부르는 쪽에서 다른 값으로 채움)."""
    t = (addr or "").split()
    if not t:
        return "", ""
    if not norm_sido(t[0]):   # 띄어쓰기 없이 붙은 주소 ("부산광역시해운대구반송로780") → 시도·시군구를 떼어 냄
        for full in sorted(SIDO_SHORT, key=len, reverse=True):
            if len(full) > 2 and t[0].startswith(full) and len(t[0]) > len(full):
                m = re.match(r"(.+?(?:시|군|구))(.*)", t[0][len(full):])
                t = [full] + ([m.group(1), m.group(2)] if m else [t[0][len(full):]]) + t[1:]
                break
    sido = norm_sido(t[0], t[1] if len(t) > 1 else "")
    rest = t[1:] if sido else t
    sgg = rest[0] if rest and re.search(r"(시|군|구)$", rest[0]) else ""
    if len(rest) > 1 and re.search(r"시$", rest[0]) and re.search(r"구$", rest[1]):
        sgg = f"{rest[0]} {rest[1]}"  # 수원시 영통구 등
    return sido, sgg


def out_dir():
    st = sorted(p for p in STAGED.iterdir() if p.is_dir()) if STAGED.exists() else []
    if not st:
        sys.exit("data/staged 가 없습니다. 먼저 build_tables.py build")
    return st[-1]


def ref(name):
    for d in (out_dir(), ACTIVE):
        p = d / name
        if p.exists():
            return list(csv.DictReader(p.open(encoding="utf-8-sig")))
    sys.exit(f"참조 표 없음: {name}")


def write_csv(path, rows):
    cols = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def code4_to_prefix(c):
    c = str(c).strip()
    if re.fullmatch(r"\d{4}", c):
        return f"{c[:2]}-{c[2:4]}"
    if re.fullmatch(r"\d{2}", c):  # 지정폐기물 일부(폐유 06 등)는 대분류 2자리로만 집계됨
        return c
    return None


def cmd_recycling(xlsx):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    codes = ref("waste_codes_4.csv")
    tmap = {r["code"]: r for r in ref("treatment_code_map.csv")}
    leaf = [r["분류번호"] for r in codes if r["말단"] == "Y"]
    names = {r["분류번호"]: r["명칭"] for r in codes}
    m = re.search(r"(20\d\d)", Path(xlsx).name)
    year = m.group(1) if m else ""

    rows, issues, seen_code4 = [], [], {}
    for sheet, (kind, gubun) in SHEETS.items():
        if sheet not in wb.sheetnames:
            issues.append(f"시트 없음: {sheet}")
            continue
        ws = wb[sheet]
        header = None
        for i, r in enumerate(ws.iter_rows(values_only=True), 1):
            r = ["" if x is None else str(x).strip() for x in r[:10]]
            if header is None:
                if r[:3] == ["폐기물종류", "폐기물코드", "업체명"]:
                    header = r
                continue
            if not r[2]:
                continue
            종류, code4, 업체, 등록, 대표, 주소, 전화, 종업원, 제품, 방법 = r
            seen_code4.setdefault(code4, 종류)
            sido, sgg = region(주소)
            base = {"업체_키": f"{core_name(업체)}|{sgg or sido}", "업체명": 업체, "업체명_정규화": norm_name(업체),
                    "허가신고": kind, "등록형태": 등록, "폐기물_구분": gubun, "폐기물코드": code4,
                    "분류번호_중분류": code4_to_prefix(code4) or "", "폐기물종류": 종류,
                    "주소": 주소, "시도": sido, "시군구": sgg, "전화번호": 전화, "종업원수": 종업원, "재생제품": 제품,
                    "기준연도": year, "출처": f"{Path(xlsx).name}#{sheet}#{i}"}
            ms = RX_METHOD.findall(방법)
            if not ms:
                rows.append({**base, "처리방법코드": "", "처리방법": 방법, "자가위탁": "", "r_code": "", "위탁가능": "N",
                             "상태": "처리방법 없음" if not 방법 else "처리방법 해석 실패"})
                if 방법:
                    issues.append(f"처리방법 해석 실패: {sheet}#{i} {방법[:40]}")
                continue
            for code, label, sw in ms:
                t = tmap.get(code)
                if t is None:
                    issues.append(f"별지 제5호에 없는 처리방법코드 {code}: {sheet}#{i}")
                rows.append({**base, "처리방법코드": code, "처리방법": label.strip(), "자가위탁": sw,
                             "r_code": (t or {}).get("r_code", ""), "위탁가능": "Y" if sw == "위탁" else "N",
                             "상태": ""})

    # 원자료 중복(같은 업체·주소·코드·방법이 두 줄) → 한 줄로 합치고 재생제품·출처를 이어 붙임
    merged, dup = {}, 0
    for r in rows:
        k = (r["업체_키"], r["폐기물_구분"], r["폐기물코드"], r["처리방법코드"], r["주소"])
        if k in merged:
            dup += 1
            m_ = merged[k]
            for col in ("재생제품", "출처"):
                if r[col] and r[col] not in m_[col]:
                    m_[col] = f"{m_[col]}; {r[col]}" if m_[col] else r[col]
        else:
            merged[k] = r
    rows = list(merged.values())
    if dup:
        issues.append(f"원자료 중복 행 {dup}개 합침 (같은 업체·주소·코드·처리방법)")

    # 4자리 코드 ↔ 분류번호
    cw = []
    for c4, 종류 in sorted(seen_code4.items()):
        pre = code4_to_prefix(c4)
        kids = [c for c in leaf if pre and (c == pre or c.startswith(pre + "-"))]
        if not kids:
            issues.append(f"폐기물코드 {c4}({종류}): 별표 4에 대응 분류번호 없음")
        cw.append({"폐기물코드": c4, "폐기물종류": 종류, "분류번호_중분류": pre or "",
                   "중분류_명칭": names.get(pre, ""), "말단_분류번호": ";".join(kids), "말단_수": len(kids)})

    od = out_dir()
    write_csv(od / "partners_recycling.csv", rows)
    write_csv(od / "waste_code_crosswalk.csv", cw)
    comp = {r["업체_키"] for r in rows}
    entrust = {r["업체_키"] for r in rows if r["위탁가능"] == "Y"}
    self_only = comp - entrust
    by = lambda k: {v: len({r["업체_키"] for r in rows if r[k] == v}) for v in sorted({r[k] for r in rows})}
    rep = [f"# 상대 기업 정제 보고서 — {Path(xlsx).name}", f"생성: {datetime.datetime.now():%Y-%m-%d %H:%M}", "",
           "| 항목 | 값 |", "|---|---|",
           f"| 행 (업체×코드×처리방법) | {len(rows):,} |",
           f"| 업체 (업체_키 기준) | {len(comp):,} |",
           f"| 위탁 받을 수 있는 업체 | {len(entrust):,} |",
           f"| 자가처리만 / 처리방법 없음 | {len(self_only):,} |",
           f"| 허가/신고 | {by('허가신고')} |",
           f"| 폐기물 구분 | {by('폐기물_구분')} |",
           f"| 폐기물코드(4자리) | {len(cw)} (대응 실패 {sum(1 for c in cw if not c['말단_수'])}) |",
           "", "참고: 원자료 업체현황(5) 2024년 가동업체 6,659곳 (같은 업체가 여러 코드·구분에 나오므로 업체_키 수와 정확히 같지 않음)",
           "", f"## 확인 필요 ({len(issues)})"]
    from collections import Counter
    grp = Counter(re.sub(r": 부록[^ ]*(?: [^ ]*)?#\d+.*$", "", x) for x in issues)
    rep += [f"- {k} ({v}건)" if v > 1 else f"- {k}" for k, v in grp.most_common()]
    (od / "partners_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep)); print(f"\n→ {od}")


def cmd_demand(xlsx):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb["전체"] if "전체" in wb.sheetnames else wb.worksheets[-1]
    it = ws.iter_rows(values_only=True)
    header = [str(x).strip() if x is not None else "" for x in next(it)]
    des = ref("designated_resources.csv")
    allow = {}
    for r in des:
        allow.setdefault(r["품목"], set()).update(r["허용_R코드"].split(";"))
        allow.setdefault(r["품목"] + "#codes", set()).add(r["분류번호"])
    rows, issues = [], []
    for i, r in enumerate(it, 2):
        d = dict(zip(header, ["" if x is None else str(x).strip() for x in r]))
        if not d.get("회사명"):
            continue
        item = d.get("품목", "")
        rc = re.findall(r"R-\d+-\d+", d.get("고시_용도", ""))
        cds = re.findall(r"\d{2}-\d{2}-\d{2}", d.get("분류번호", ""))
        st = []
        if item not in allow:  # 품목명 표기 차이(예: IC트레이 ↔ 아이씨트레이(IC-Tray)) → 분류번호가 하나의 품목에만 맞으면 그 품목으로
            cand = [k for k in allow if not k.endswith("#codes") and cds and all(c in allow[k + "#codes"] for c in cds)]
            if len(cand) == 1:
                st.append(f"품목명 표기 '{item}' → 고시 '{cand[0]}'")
                item = cand[0]
        if item not in allow:
            st.append("지정 고시에 없는 품목명")
        else:
            if any(x not in allow[item] for x in rc):
                st.append(f"고시 허용 용도 밖 R코드 {[x for x in rc if x not in allow[item]]}")
            if any(c not in allow[item + "#codes"] for c in cds):
                st.append("고시 적용범위 밖 분류번호")
        sido, sgg = d.get("시도", ""), d.get("시군구", "")
        rows.append({"품목": item, "분류번호": ";".join(cds), "용도_R코드": ";".join(rc), "확신도": d.get("확신도", ""),
                     "근거": d.get("근거", ""), "회사명": d.get("회사명", ""), "업체_키": f"{core_name(d.get('회사명',''))}|{sgg or sido}",
                     "공장주소": d.get("공장주소", ""), "시도": sido, "시군구": sgg, "단지명": d.get("단지명", ""),
                     "전화번호": d.get("전화번호", ""), "생산품": d.get("생산품", ""), "원자재_2021": d.get("원자재(2021)", ""),
                     "대표업종": d.get("대표업종", ""), "출처": d.get("출처", ""), "검증": "; ".join(st)})
        if st and not all(x.startswith("품목명 표기") for x in st):
            issues.append(f"{i}행 {d.get('회사명','')}: {'; '.join(st)}")
    od = out_dir()
    write_csv(od / "demand_designated.csv", rows)
    from collections import Counter
    c = Counter((r["품목"], r["확신도"]) for r in rows)
    rep = [f"# 관문 1 수요기업 정제 보고서 — {Path(xlsx).name}", "", f"행 {len(rows):,} / 검증 경고 {len(issues)}", "",
           "| 품목 | 높음 | 중간 | 낮음 |", "|---|---|---|---|"]
    for item in sorted({r["품목"] for r in rows}):
        rep.append(f"| {item} | {c[(item,'높음')]} | {c[(item,'중간')]} | {c[(item,'낮음')]} |")
    groups = Counter(re.sub(r"^\d+행 [^:]*: ", "", x) for x in issues)
    rep += ["", "## 검증 경고 (종류별)"] + [f"- {k}: {v}건" for k, v in groups.most_common()]
    (od / "demand_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep)); print(f"\n→ {od}")



# ───────────────────────── 처분업·운반업 (한국폐기물협회 「폐기물 처리업체 현황」 엑셀) ─────────────────────────
# 파일 제목(첫 두 줄)으로 종류를 알아낸다 → 파일 이름이 바뀌어도 동작
FILE_KINDS = [  # (제목에 들어 있는 말, 업종, 대상군)
    ("의료폐기물 수집", "수집운반", "의료"), ("지정폐기물 수집", "수집운반", "지정"),
    ("건설폐기물 수집", "수집운반", "건설"), ("생활, 사업장일반폐기물 수집", "수집운반", "생활·사업장일반"),
    ("의료폐기물 중간처분(소각)", "중간처분_소각", "의료"), ("사업장지정폐기물 중간처분(소각)", "중간처분_소각", "지정"),
    ("사업장지정폐기물 중간처분(소각제외", "중간처분_기타", "지정"), ("소각시설 현황", "중간처분_소각", "생활·사업장일반"),
    ("기타(소각 제외) 시설", "중간처분_기타", "생활·사업장일반"), ("건설폐기물 중간처리", "중간처리_건설", "건설"),
    ("지정폐기물 최종처분", "최종처분", "지정"), ("폐기물 최종처분업체", "최종처분", "생활·사업장일반·건설"),
]
# 지정폐기물 영업대상 문구 → 분류번호 앞자리 (키워드, 긴 것부터)
DESIG_KW = [("할로겐", "04-01"), ("폐유기용제", "04"), ("폐산", "02-01"), ("폐알칼리", "02-02"), ("수산화", "02-02"),
            ("PCB", "08"), ("폴리클로리네이티드", "08"), ("폐페인트", "05"), ("폐락", "05"), ("폐래커", "05"),
            ("폐합성", "01-01"), ("오니", "01-02"), ("폐농약", "01-03"), ("광재", "03-01"), ("분진", "03-02"),
            ("주물사", "03-03"), ("폐사", "03-03"), ("내화물", "03-04"), ("도자기", "03-04"), ("소각재", "03-05"), ("비산재", "03-05"), ("바닥재", "03-05"),
            ("안정화", "03-06"), ("고형화", "03-06"), ("폐촉매", "03-07"), ("흡착", "03-08"), ("흡수", "03-08"),
            ("이차전지", "03-10"), ("폐석면", "07"), ("폐유독", "09"), ("의료", "10"), ("수은", "11"), ("천연방사성", "12"),
            ("폐유", "06")]
GEN_CATS = ["생활", "음식물류", "사업장비(非)배출시설계", "사업장배출시설계", "건설"]


def _cells(r):
    return ["" if x is None else re.sub(r"\s+", " ", str(x)).strip() for x in r]


def parse_keco_sheet(rows):
    """제목 몇 줄 + 2~3줄짜리 머리글 + 합계·소계 줄이 섞인 표 → (칸이름 목록, 데이터 줄 목록)"""
    h = next(i for i, r in enumerate(rows) if "업체명" in r)
    name_i = rows[h].index("업체명")
    cols, parent = [], ""
    for v in rows[h]:  # 병합된 윗칸 이름을 오른쪽으로 채움 (영업대상 폐기물 | (빈칸) | (빈칸) …)
        parent = v or parent
        cols.append(v or parent)
    j = h + 1
    while j < len(rows):  # 머리글 아랫줄: 업체명 칸이 비고, 연번이 숫자·'개소'·합계가 아님
        r = rows[j]
        first = " ".join(r[:3])
        if r[name_i] or re.search(r"^\d+\b|개소|합계|소계", first.strip()):
            break
        for k, v in enumerate(r):
            if v and k < len(cols) and not cols[k].endswith(v):
                cols[k] = f"{cols[k]}/{v}" if cols[k] else v
        j += 1
    cols = [re.sub(r"\s+", "", c) for c in cols]  # 칸 이름 공백 제거 ("증발 농축" → "증발농축")
    data, sido_ff = [], ""
    for r in rows[j:]:
        rec = dict(zip(cols, r))
        sd = rec.get("시도") or rec.get("시·도") or ""
        if "개소" in (r[0] or "") or "개소" in (r[1] if len(r) > 1 else ""):
            if sd and sd != "전국":
                sido_ff = sd  # 소계 줄의 시도 → 아래 업체 줄에 채움
            continue
        if not rec.get("업체명"):
            continue
        rec["_시도"] = sd or sido_ff
        data.append(rec)
    return cols, data


def pick(rec, *names):
    for n in names:
        n = n.replace(" ", "")
        for k, v in rec.items():
            if k == n or k.startswith(n):
                if v:
                    return v
    return ""


def disposal_codes(kind, rec, cols):
    if kind == "중간처분_소각":
        m = pick(rec, "소각방식")
        codes = []
        if "고온" in m: codes.append("2102")
        if "일반" in m or not m: codes.append("2101")
        if "열분해" in m: codes.append("2103")
        if "용융" in m: codes.append("2104")
        return codes
    if kind == "최종처분":
        t = pick(rec, "매립지 종류", "매립지종류")
        return ["2202"] if "차단" in t else ["2201"]
    if kind == "중간처분_기타":
        m = {"증발농축": "2109", "파쇄": "2106", "반응응집침전": "2116", "고형화안정화": "2113;2114",
             "소멸화": "2115", "호기성": "2119", "기타(고시": "2119"}  # 칸 이름은 공백 제거된 상태
        out = []
        for k, v in rec.items():
            for kw, code in m.items():
                if kw in k and v not in ("", "0", "0.0"):
                    try:
                        if float(v) > 0: out += code.split(";")
                    except ValueError:
                        out += code.split(";")
        return sorted(set(out))
    return []


def cmd_facilities(folder):
    import openpyxl
    files = sorted(Path(folder).glob("*.xlsx"))
    disp, trans, issues, used = [], [], [], []
    for f in files:
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [_cells(r) for r in ws.iter_rows(values_only=True)]
        title = " ".join(rows[0][:3] + rows[1][:3]) if len(rows) > 1 else ""
        kind = next(((k, g) for kw, k, g in FILE_KINDS if kw in title), None)
        if not kind:
            issues.append(f"종류를 모르는 파일(건너뜀): {f.name} — 제목 '{title[:40]}'")
            continue
        kind, group = kind
        cols, data = parse_keco_sheet(rows)
        used.append(f"{f.name} → {kind} / {group}: {len(data)}곳")
        prev = {}
        for i, rec in enumerate(data):
            addr = pick(rec, "소재지")
            target = pick(rec, "영업대상폐기물", "영업대상건설폐기물")
            cats = [c for c in GEN_CATS if any(c in k and v for k, v in rec.items() if k.startswith("영업대상"))]
            dcodes = sorted({code for kw, code in DESIG_KW if kw in target}) if group in ("지정", "의료") else []
            if group == "지정" and not dcodes:
                dcodes = ["*"]  # "◦지정폐기물", "액상, 고상"처럼 종류 없이 적힌 경우 = 지정폐기물 전체(성상만 구분)
            # 같은 업체의 다음 공구·시설 줄은 시군구가 비어 있음 → 바로 윗줄 값 이어 받기
            if not pick(rec, "시군구", "시·군·구") and prev.get("업체명") == rec["업체명"]:
                rec["시군구"] = prev.get("시군구", "")
            prev = {"업체명": rec["업체명"], "시군구": pick(rec, "시군구", "시·군·구")}
            # 시도: 원자료 시트의 시도 구간(소계 줄) 값이 기준. 주소에는 시도가 빠진 경우가 많음
            # 시군구: 주소가 시도로 시작하면 주소에서 (재활용·표준데이터와 같은 방식 → 업체_키 일치)
            #         시도가 빠진 주소("덕진구 팔복로 …")면 원자료 시군구 칸 ("중구"처럼 어느 시인지 모르는 값 방지)
            a_sido, a_sgg = region(addr)
            sido = norm_sido(rec["_시도"]) or a_sido
            col_sgg = pick(rec, "시군구", "시·군·구")
            sgg = (a_sgg or col_sgg) if a_sido else (col_sgg or a_sgg)
            if not sido:
                issues.append(f"시도를 정하지 못함: {f.name}#{i} {rec['업체명']} / {addr}")
            if group == "의료":
                dcodes = ["10"]
            반납 = pick(rec, "반납")
            base = {"업체_키": f"{core_name(rec['업체명'])}|{sgg or sido}", "업체명": rec["업체명"],
                    "업종": kind, "대상군": group, "영업대상_원문": target[:500], "영업대상_구분": ";".join(cats),
                    "지정_분류번호_앞자리": ";".join(dcodes), "주소": addr, "시도": sido, "시군구": sgg,
                    "전화번호": pick(rec, "전화번호"), "허가일": pick(rec, "허가"), "반납신고일": 반납,
                    "영업중": "N" if 반납 else "Y", "기준연도": "2023", "출처": f"{f.name}#{i}"}
            if kind == "수집운반":
                trans.append({**base, "보유차량": pick(rec, "보유차량"), "영업구역": "", "2023_운반량_톤": pick(rec, "2023년 수집")})
            else:
                disp.append({**base, "처리방법코드": ";".join(disposal_codes(kind, rec, cols)),
                             "시설용량_톤일": pick(rec, "시설용량", "처리능력", "중간처분시설(소각제외) 처리능력(톤/일)/계"),
                             "소각방식": pick(rec, "소각방식"), "매립지종류": pick(rec, "매립지 종류", "매립지종류"),
                             "잔여매립가능량_m3": pick(rec, "잔여매립가능량"), "2023_처리량": pick(rec, "2023년 처리량", "2023년 매립량")})
    od = out_dir()
    write_csv(od / "partners_disposal.csv", disp)
    write_csv(od / "partners_transport.csv", trans)
    from collections import Counter
    c1 = Counter((r["업종"], r["대상군"]) for r in disp)
    c2 = Counter(r["대상군"] for r in trans)
    nocode = [r for r in disp if r["업종"] != "중간처리_건설" and not r["처리방법코드"]]
    nodes = [r for r in disp + trans if r["대상군"] == "지정" and not r["지정_분류번호_앞자리"]]
    rep = ["# 처분업·운반업 정제 보고서", f"생성: {datetime.datetime.now():%Y-%m-%d %H:%M}", "", "## 읽은 파일"] + [f"- {x}" for x in used]
    rep += ["", "## 처분업 (partners_disposal.csv)", "| 업종 | 대상군 | 업체 |", "|---|---|---|"]
    rep += [f"| {k[0]} | {k[1]} | {v} |" for k, v in sorted(c1.items())]
    rep += ["", "## 수집·운반업 (partners_transport.csv)", "| 대상군 | 업체 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(c2.items())]
    rep += ["", f"반납(영업 종료) 표시: 처분 {sum(r['영업중']=='N' for r in disp)} · 운반 {sum(r['영업중']=='N' for r in trans)}",
            f"처리방법코드 못 정한 처분업: {len(nocode)}", f"지정 영업대상 문구에서 분류번호를 못 찾은 업체: {len(nodes)}"]
    rep += ["", f"## 확인 필요 ({len(issues)})"] + [f"- {x}" for x in issues]
    (od / "facilities_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep)); print(f"\n→ {od}")


# ───────────────────────── 전국폐기물처리업소표준데이터 + 업체 통합 ─────────────────────────
def cmd_standard(path):
    import xlrd
    if str(path).endswith(".csv"):
        import io
        raw = Path(path).read_bytes()
        txt = raw.decode("utf-8-sig") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("cp949", errors="replace")
        allrows = [r for r in csv.reader(io.StringIO(txt))]
    else:
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        allrows = [[str(x).strip() for x in sh.row_values(i)] for i in range(sh.nrows)]
    h = next(i for i, r in enumerate(allrows) if "시설명" in r)
    hdr = allrows[h]
    std = []
    for i, r in enumerate(allrows[h + 1:], h + 2):
        d = dict(zip(hdr, r))
        if not d.get("시설명"):
            continue
        addr = d.get("소재지도로명주소") or d.get("소재지지번주소", "")
        sido, sgg = region(addr)
        std.append({"업체_키": f"{core_name(d['시설명'])}|{sgg or sido}", "시설명": d["시설명"], "주소": addr,
                    "시도": sido, "시군구": sgg, "위도": d.get("위도", ""), "경도": d.get("경도", ""),
                    "업종명": d.get("업종명", ""), "처리폐기물_분류번호": d.get("처리폐기물정보", "").replace("+", ";"),
                    "영업구역": d.get("영업구역", ""), "시설장비": d.get("시설·장비명", ""), "허가일자": d.get("허가일자", ""),
                    "관리기관": d.get("관리기관명", ""), "데이터기준일자": d.get("데이터기준일자", ""),
                    "출처": f"{Path(path).name}#{i}"})
    od = out_dir()
    write_csv(od / "facility_std.csv", std)
    print(f"facility_std.csv {len(std):,}행 → {od}")
    cmd_master()


def cmd_master():
    """재활용·처분·운반 업체를 업체_키로 묶고 표준데이터 좌표를 붙인다 → facility_master.csv"""
    od = out_dir()
    rd = lambda n: list(csv.DictReader((od / n).open(encoding="utf-8-sig"))) if (od / n).exists() else []
    rec, dis, tra, std = rd("partners_recycling.csv"), rd("partners_disposal.csv"), rd("partners_transport.csv"), rd("facility_std.csv")
    by_key = {}
    for s in std:
        if s["위도"] and s["경도"]:
            by_key.setdefault(s["업체_키"], s)
    by_name = {}
    for s in std:
        if s["위도"]:
            by_name.setdefault(s["업체_키"].split("|")[0], []).append(s)
    m = {}
    def add(r, role):
        e = m.setdefault(r["업체_키"], {"업체_키": r["업체_키"], "업체명": r["업체명"], "주소": r["주소"],
                                        "시도": r["시도"], "시군구": r["시군구"], "역할": set(), "전화번호": r.get("전화번호", "")})
        e["역할"].add(role)
    for r in rec:
        if r["위탁가능"] == "Y":
            add(r, f"재활용({r['허가신고']})")
    for r in dis:
        if r["영업중"] == "Y":
            add(r, f"처분({r['업종'].replace('중간처분_','').replace('중간처리_','')}·{r['대상군']})")
    for r in tra:
        if r["영업중"] == "Y":
            add(r, f"운반({r['대상군']})")
    out, hit = [], {"키": 0, "이름": 0, "없음": 0}
    for k, e in m.items():
        s, how = by_key.get(k), "업체_키"
        if s is None:  # 같은 이름이 표준데이터에 딱 하나면 시군구 표기 차이로 보고 연결
            cand = by_name.get(k.split("|")[0], [])
            if len({c["업체_키"] for c in cand}) == 1:
                s, how = cand[0], "이름(유일)"
        hit["키" if how == "업체_키" and s else "이름" if s else "없음"] += 1
        out.append({**{x: y for x, y in e.items() if x != "역할"}, "역할": ";".join(sorted(e["역할"])),
                    "위도": s["위도"] if s else "", "경도": s["경도"] if s else "", "좌표_연결": how if s else "없음(시군구 중심점 사용)",
                    "표준_업종명": s["업종명"] if s else "", "표준_처리폐기물_분류번호": s["처리폐기물_분류번호"] if s else "",
                    "표준_영업구역": s["영업구역"] if s else "", "표준_기준일자": s["데이터기준일자"] if s else ""})
    write_csv(od / "facility_master.csv", out)
    n = len(out)
    rep = ["# 업체 통합(facility_master) 보고서", "", f"업체 {n:,}곳 (재활용 위탁가능 + 처분·운반 영업중)",
           f"- 좌표 연결: 업체_키 {hit['키']:,} / 이름 유일 {hit['이름']:,} / 없음 {hit['없음']:,} → 연결률 {(n-hit['없음'])/max(n,1):.0%}",
           "- 연결 안 된 업체는 탄소 계산 때 시군구 중심점으로 대신",
           "- 표준데이터는 지자체가 올린 곳만 있어 전국을 다 덮지 않음 (공공데이터포털 15114147)"]
    (od / "master_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "recycling":
        cmd_recycling(a[1])
    elif len(a) == 2 and a[0] == "demand":
        cmd_demand(a[1])
    elif len(a) == 2 and a[0] == "facilities":
        cmd_facilities(a[1])
    elif len(a) == 2 and a[0] == "standard":
        cmd_standard(a[1])
    elif a == ["master"]:
        cmd_master()
    else:
        print(__doc__)