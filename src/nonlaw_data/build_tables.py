"""build_tables.py — 법령 원문(data/watched)에서 판정 표(CSV)를 자동 생성

    python build_tables.py build            # data/watched → data/staged/{시행일}/ 생성 + 검증 보고서
    python build_tables.py diff  [시행일]   # staged 와 active 비교 (행 추가·삭제·변경)
    python build_tables.py promote 시행일   # 사람 승인 후 실행: active → old/{날짜}, staged → active

원칙
- 법 원문에서 기계적으로 나오는 칸만 코드가 만든다 (분류번호, 명칭, R코드, 해당 여부 …).
- 설계 판단 칸(tier, 탄소계수 키, 운반 필요 여부 등)은 config/*.csv 에 사람이 적고, 빌더가 붙이기만 한다.
- 생성 결과는 staged/ 까지만. active/ 교체는 promote(사람 승인) 로만.
- 표준 라이브러리만 사용.
"""
import csv, json, re, shutil, sys, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
WATCHED, STAGED, ACTIVE, OLD = DATA / "watched", DATA / "staged", DATA / "active", DATA / "old"
CONFIG = BASE / "config"

CODE3 = re.compile(r"^\d{2}-\d{2}-\d{2}$")
CODE2 = re.compile(r"^\d{2}-\d{2}$")
CODE1 = re.compile(r"^\d{2}$")
RCODE = re.compile(r"R-\d+(?:-\d+)?")
SECTION = re.compile(r"(\d)\.\s*(지정폐기물|사업장일반폐기물|생활폐기물)")
BORDER = set("─┌┐└┘├┤┬┴┼│ ")


# ───────────────────────── 입력 ─────────────────────────
def load_watched(law_id, src=None):
    """data/watched/{law_id}.jsonl → {spec: row}. spec 이 'BF5@…' 처럼 키워드가 달라도 접두어로 찾는다."""
    path = Path(src) if src else WATCHED / f"{law_id}.jsonl"
    if not path.exists():
        sys.exit(f"원문 없음: {path}  (law_watch.py extract {law_id} 먼저 실행)")
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["spec"]] = r
    return rows


def pick(rows, prefix):
    for k, r in rows.items():
        if k == prefix or k.startswith(prefix + "@") or k.startswith(prefix + ":"):
            return r
    raise KeyError(prefix)


def src_of(r):
    return f'{r["chunk_key"]}#{r.get("hash", "")}'


# ───────────────────────── 괘선 표 공통 ─────────────────────────
def is_rule(line):
    s = line.strip()
    return bool(s) and s[0] in "┌├└" and set(s) <= BORDER


def cells(line):
    s = line.strip()
    s = re.sub(r"[┐┤]\s*\S.*$", "", s) if not s.startswith("│") else s  # 표 밖 꼬리 제거
    parts = s.split("│")
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return parts


def join(a, b, sep=""):
    a, b = a.strip(), b.strip()
    if not a:
        return b
    if not b:
        return a
    if a.endswith(",") or sep == " ":
        return a + " " + b
    return a + b


# ───────────────────────── 별표 4: 분류번호 ─────────────────────────
def build_waste_codes(r4):
    """시행규칙 별표 4 → waste_codes_4.csv (분류번호 전체, 구분·상위·명칭). 지정폐기물 = hazardous."""
    out, sec, cur = [], None, None
    for line in r4["text"].splitlines():
        s = line.strip()
        m = SECTION.match(s)
        if m:
            sec = m.group(2)
            continue
        if s.startswith("비고"):
            break
        m = re.match(r"^(\d{2}(?:-\d{2}){0,2})\s+(.*)$", s)
        if m and sec:
            code, name = m.groups()
            lvl = code.count("-") + 1
            if code.endswith("-00-00") or code.endswith("-00"):
                lvl_kind = "단일"
            else:
                lvl_kind = {1: "대분류", 2: "중분류", 3: "세분류"}[lvl]
            eff = re.sub(r"(-00)+$", "", code)  # 04-01-00 → 04-01, 12-00-00 → 12
            cur = {"분류번호": code, "폐기물_구분": sec, "단계": lvl_kind,
                   "상위": "-".join(eff.split("-")[:-1]),
                   "명칭": name, "지정폐기물": "Y" if sec == "지정폐기물" else "N",
                   "근거": src_of(r4)}
            out.append(cur)
        elif cur and s and sec:
            cur["명칭"] = join(cur["명칭"], s)
    # 말단(자식이 없는 코드) 표시
    parents = {r["상위"] for r in out}
    for r in out:
        r["말단"] = "N" if r["분류번호"] in parents else "Y"
    return out


# ───────────────────────── 별표 4의2: R코드 세부 ─────────────────────────
TIER = {  # 설계 규칙(사람 결정, 계획서 관문 2): 법 원문이 아님
    "R-1": "reuse", "R-2": "reuse",
    "R-3": "material_recycle", "R-4": "material_recycle", "R-5": "material_recycle",
    "R-6": "material_recycle", "R-7": "material_recycle", "R-11": "material_recycle",
    "R-8": "energy_recovery", "R-9": "energy_recovery", "R-10": "",
}


def build_rcode_detail(r42):
    """'가. R-1: …', '1) R-1-1: …', 접두어 없는 'R-10-1: …' 을 읽는다.
    대분류 제목 'N. …(R-7)' 처럼 괄호 안 코드가 하나면 그 제목을 유형 설명으로 쓴다 (R-7·R-10·R-11 은 가./나. 줄이 없음)."""
    out, cur, head = [], None, None

    def row(code, desc):
        top = re.match(r"R-\d+", code).group(0)
        return {"r_code": code, "단계": "세부" if code.count("-") == 2 else "유형",
                "상위": top if code != top else "", "설명": desc,
                "tier": TIER.get(top, "?"), "근거": src_of(r42)}

    def flush_head():
        if head:
            codes = RCODE.findall(head[-20:])
            if len(codes) == 1:
                desc = re.sub(r"^\d+\.\s*", "", head)
                desc = re.sub(r"\(R-[\d, R-]+\)\s*$", "", desc).strip()
                head_rows.append(row(codes[0], desc))

    head_rows = []
    for line in r42["text"].splitlines():
        s = line.strip()
        if s.startswith("비고"):
            break
        if re.match(r"^\d+\.\s", s):
            flush_head()
            head, cur = s, None
            continue
        m = re.match(r"^(?:[가-힣]\.|\d+\))?\s*(R-\d+(?:-\d+)?)\s*[:：]\s*(.*)$", s)
        if m:
            flush_head()
            head = None
            cur = row(*m.groups())
            out.append(cur)
        elif s and cur:
            cur["설명"] = join(cur["설명"], s)
        elif s and head:
            head = join(head, s)
    flush_head()
    have = {r["r_code"] for r in out}
    out += [r for r in head_rows if r["r_code"] not in have]
    key = lambda r: [int(x) for x in r["r_code"][2:].split("-")]
    return sorted(out, key=key)


# ───────────────────────── 별표 4의3: 재활용 가능 유형 ─────────────────────────
def build_recycling_types(r43, children=None):
    """괘선 표를 행 단위로 읽는다.
    - 전체 구분선(├…┤ 로 시작)마다 레코드 하나를 닫는다.
    - 같은 레코드 안의 부분 구분선(셀 안에 ├)은 R코드 묶음을 새로 시작한다 (묶음마다 사전분석 여부가 다름).
    - 칸이 2개인 행 = 소분류 제목 행 (예: 01-02 오니류) → R코드 없음.
    """
    recs, sec, rec, in_header = [], None, None, False

    def close():
        nonlocal rec
        if rec and rec["code"]:
            recs.append(rec)
        rec = None

    for line in r43["text"].splitlines():
        m = SECTION.search(line)
        if m:
            sec = m.group(2)
        s = line.strip()
        if s.startswith("비고"):
            close()
            break
        if not s or "│" not in s and not is_rule(s):
            continue
        if is_rule(re.sub(r"[┐┤].*$", "┐", s)) or (s[0] in "┌├└" and "│" not in s):
            close()
            in_header = False
            continue
        if s.startswith("│") and "├" in s:  # 셀 안 부분 구분선: │코드│종류이어짐├──┼──┤ → 새 R코드 묶음
            if rec is not None:
                left = s[:s.index("├")].split("│")
                if len(left) >= 3:
                    rec["name"] = join(rec["name"], left[2])  # 구분선 줄에도 종류 칸 글자가 이어짐
                rec["bundles"].append({"r": "", "pre": ""})
            continue
        c = cells(s)
        if c and c[0].strip() == "분류번호":
            in_header = True
            continue
        if in_header:
            continue
        if rec is None:
            rec = {"code": "", "name": "", "bundles": [], "sec": sec, "ncols": len(c)}
        if c[0].strip():
            rec["code"] = c[0].strip()
        if len(c) >= 2:
            rec["name"] = join(rec["name"], c[1])
        if len(c) >= 3:
            third = c[2]
            if "├" in third or "┼" in third:  # 부분 구분선 → 새 묶음
                rec["bundles"].append({"r": "", "pre": ""})
                continue
            if not rec["bundles"]:
                rec["bundles"].append({"r": "", "pre": ""})
            b = rec["bundles"][-1]
            b["r"] = join(b["r"], third, " ")
            if len(c) >= 4:
                b["pre"] = join(b["pre"], c[3], " ")
    close()

    rows, issues = [], []
    for rec in recs:
        code = rec["code"]
        base = {"분류번호": code, "폐기물_구분": rec["sec"], "폐기물의_종류": rec["name"]}
        if not CODE3.match(code):
            if not CODE2.match(code) and not CODE1.match(code):
                issues.append(f"분류번호 형식 이상: {code!r}")
            continue  # 대·중분류 제목 행
        if not rec["bundles"] or not any(b["r"].strip() for b in rec["bundles"]):
            rows.append({**base, "묶음": "", "r_code": "", "사전분석확인": "", "재활용_금지": "N",
                         "상태": "유형 미정(비고2: 재활용환경성평가)", "근거": src_of(r43)})
            continue
        for i, b in enumerate(rec["bundles"], 1):
            r = b["r"].replace(" ", "")
            pre = b["pre"].replace(" ", "")
            pre = {"해당": "해당", "해당없음": "해당 없음", "": ""}.get(pre, pre)
            if "재활용금지" in r:
                rows.append({**base, "묶음": i, "r_code": "", "사전분석확인": "", "재활용_금지": "Y",
                             "상태": "재활용 금지", "근거": src_of(r43)})
                continue
            codes = RCODE.findall(b["r"])
            left = RCODE.sub("", b["r"]).replace(",", "").strip()
            if left:
                issues.append(f"{code} 묶음{i}: R코드 외 글자 {left!r}")
            if pre not in ("해당", "해당 없음"):
                issues.append(f"{code} 묶음{i}: 사전분석 여부 값 이상 {pre!r}")
            seen = set()
            for rc in codes:
                kids = (children or {}).get(rc, [])
                rows.append({**base, "묶음": i, "r_code": rc, "사전분석확인": pre, "재활용_금지": "N",
                             "상태": "상위코드(아래 세부코드로 펼침)" if kids else "", "근거": src_of(r43)})
                seen.add(rc)
                # 상위코드(R-10, R-3 등)는 세부코드로 펼친 행을 더한다 → 판정에서 R-10-1 로 바로 찾을 수 있게
                for k in kids:
                    if k not in codes and k not in seen:
                        seen.add(k)
                        rows.append({**base, "묶음": i, "r_code": k, "사전분석확인": pre, "재활용_금지": "N",
                                     "상태": f"{rc}에서 펼침", "근거": src_of(r43)})
    return rows, issues


# ───────────────────────── 별지 제5호서식: 처리방법 코드 ─────────────────────────
def build_treatment_codes(rf5):
    t = rf5["text"]
    i = t.find("폐기물처리 방법별 분류", t.find("<참고>"))
    t = t[i:] if i >= 0 else t
    blocks, gubun, bunryu = {}, None, None
    for line in t.splitlines():
        s = line.strip()
        if "│" not in s:
            continue
        c = cells(s)
        if len(c) < 3 or c[0].strip() == "처리구분" or "처리분류" in c[1]:
            continue
        if c[0].strip():
            gubun = c[0].strip()
        if "├" in c[1] or "─" in c[1]:
            continue
        if c[1].strip():
            bunryu = c[1].strip()
        key = (gubun, bunryu)
        blocks[key] = blocks.get(key, "") + c[2].strip()
    rows, seen = [], set()
    for (g, b), txt in blocks.items():
        for m in re.finditer(r"([^,\[\]]*)\[(\d{4})\]", txt):
            name, code = m.group(1).strip(" ,"), m.group(2)
            if code in seen:
                continue
            seen.add(code)
            rows.append({"code": code, "처리구분": g, "처리분류": b, "세분류": name or b, "근거": src_of(rf5)})
    rows.sort(key=lambda r: r["code"])
    return rows


DEFAULT_DESIGN = {"treatment_design.csv": """code,r_code,baseline,tier,carbon_factor_key,transport_needed,confidence,note
1001,R-1,recycle,reuse,recycle_reuse,N,high,
1002,R-2,recycle,reuse,recycle_reuse,N,high,
1003,R-3,recycle,material_recycle,recycle_material,N,high,
1004,R-4,recycle,material_recycle,recycle_material,N,high,
1005,R-5,recycle,material_recycle,recycle_material,N,high,
1006,R-6,recycle,material_recycle,recycle_material,N,high,
1007,R-7,recycle,material_recycle,recycle_material,N,high,
1008,R-8,recycle,energy_recovery,energy_recovery,N,high,
1009,R-9,recycle,energy_recovery,energy_recovery,N,high,
1010,R-10,recycle,,null,N,medium,"중간가공 → 최종 처리 미상, tier 미정"
1011,R-11,recycle,material_recycle,recycle_material,N,high,재활용환경성평가 승인 유형
1101,,incineration,disposal,incineration,N,high,
1102,,incineration,disposal,incineration_high_temp,N,high,
1103,,incineration,disposal,pyrolysis_disposal,N,high,
1104,,incineration,disposal,melting_high_temp,N,high,
1105,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1106,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1107,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1108,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1109,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1110,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1111,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1112,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null (의료폐기물)
1113,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1114,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1115,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1116,,intermediate_disposal,,null,N,low,최종 처분 미상 → carbon_saving=null
1117,,intermediate_disposal,,null,N,low,
1201,,landfill,disposal,landfill_managed,N,high,
1202,,landfill,disposal,landfill_isolated,N,high,
1203,,landfill,disposal,landfill,N,high,
1300,,ocean_disposal,,null,N,high,범위 밖
1500,,export,,null,N,high,범위 밖
2001,R-1,recycle,reuse,recycle_reuse,Y,high,
2002,R-2,recycle,reuse,recycle_reuse,Y,high,
2003,R-3,recycle,material_recycle,recycle_material,Y,high,
2004,R-4,recycle,material_recycle,recycle_material,Y,high,
2005,R-5,recycle,material_recycle,recycle_material,Y,high,
2006,R-6,recycle,material_recycle,recycle_material,Y,high,
2007,R-7,recycle,material_recycle,recycle_material,Y,high,
2008,R-8,recycle,energy_recovery,energy_recovery,Y,high,
2009,R-9,recycle,energy_recovery,energy_recovery,Y,high,
2010,R-10,recycle,,null,Y,medium,"중간가공 → 최종 처리 미상, tier 미정"
2011,R-11,recycle,material_recycle,recycle_material,Y,high,재활용환경성평가 승인 유형
2101,,incineration,disposal,incineration,Y,high,
2102,,incineration,disposal,incineration_high_temp,Y,high,
2103,,incineration,disposal,pyrolysis_disposal,Y,high,
2104,,incineration,disposal,melting_high_temp,Y,high,
2105,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2106,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2107,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2108,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2109,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2110,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2111,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2112,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null (의료폐기물)
2113,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2114,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2115,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2116,,intermediate_disposal,,null,Y,low,최종 처분 미상 → carbon_saving=null
2117,,incineration,disposal,incineration,Y,high,
2118,,incineration,disposal,incineration,Y,high,한국환경공단·수도권매립지관리공사 운영
2119,,intermediate_disposal,,null,Y,low,
2201,,landfill,disposal,landfill_managed,Y,high,
2202,,landfill,disposal,landfill_isolated,Y,high,
2204,,landfill,disposal,landfill,Y,high,2203 결번
2205,,landfill,disposal,landfill_managed,Y,high,
2206,,landfill,disposal,landfill_managed,Y,high,
2300,,ocean_disposal,,null,Y,high,범위 밖
"""}


def overlay(rows, key, cfg_name):
    """config/{cfg_name} 의 설계 칸을 붙인다. 원문에 새로 생긴 코드는 '설계값 필요'로 표시."""
    p = CONFIG / cfg_name
    if not p.exists() and cfg_name in DEFAULT_DESIGN:  # 처음 실행: 기본 설계값을 config/ 에 써 두고 사용
        CONFIG.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_DESIGN[cfg_name], encoding="utf-8-sig")
        print(f"config/{cfg_name} 없음 → 기본값으로 생성: {p}")
    if not p.exists():
        return rows, [f"설계 파일 없음: config/{cfg_name} → 설계 칸 비움"]
    design = {r[key]: r for r in csv.DictReader(p.open(encoding="utf-8-sig"))}
    notes = []
    for r in rows:
        d = design.get(r[key])
        if d is None:
            notes.append(f"{r[key]}: 설계값 없음 (새 코드)")
            continue
        for k, v in d.items():
            if k not in r:
                r[k] = v
    gone = set(design) - {r[key] for r in rows}
    notes += [f"{k}: 원문에서 사라진 코드 (설계 파일에만 있음)" for k in sorted(gone)]
    return rows, notes


# ───────────────────────── 지정 순환자원 (관문 1) ─────────────────────────
def build_designated(rd):
    """순환자원 지정 고시 별표 2 '품목별 준수사항' → 품목 × 분류번호 한 줄씩.
    분류번호는 1) 적용범위의 괄호 안 코드, 허용 R코드는 2) 순환이용의 용도에서 뽑는다. 조건 원문은 3)·4)를 그대로 둔다."""
    t = rd["text"]
    m = re.search(r"(?m)^\s*\d*\.?\s*품목별 준수사항\s*$", t)  # 제목 줄만 (일반적 준수사항 본문의 같은 단어 제외)
    i = m.start() if m else -1
    if i < 0:
        return [], ["CEA_DESIG 별표 2에서 '품목별 준수사항'을 못 찾음"]
    body, rows, issues = t[i:], [], []
    heads = list(re.finditer(r"(?m)^\s*([가-하])\.\s*(\S[^\n]*)$", body))
    for k, h in enumerate(heads):
        seg = body[h.end(): heads[k + 1].start() if k + 1 < len(heads) else len(body)]
        name = h.group(2).strip()
        part = lambda n: (re.search(rf"(?s){n}\)\s*(.*?)(?=\n\s*{n + 1}\)\s|\Z)", seg) or [None, ""])[1].strip()
        scope, use, how, extra = part(1), part(2), part(3), part(4)
        codes = list(dict.fromkeys(re.findall(r"\d{2}-\d{2}-\d{2}", scope)))
        rcs = list(dict.fromkeys(re.findall(r"R-\d+-\d+", use)))
        if not codes or not rcs:
            issues.append(f"지정 품목 '{name}': 분류번호 {len(codes)}개, R코드 {len(rcs)}개 — 원문 확인")
        한정 = "Y" if re.search(r"가운데|에 한하여 적용한다\. 다만|중 [^(]*에 한하여|없는 것에 한하여", scope) else "N"
        for c in codes:
            rows.append({"품목": name, "분류번호": c, "허용_R코드": ";".join(rcs), "품목_한정": 한정,
                         "적용범위_원문": re.sub(r"\s+", " ", scope), "방법기준_원문": re.sub(r"\s+", " ", how)[:1500],
                         "그밖의_준수사항": re.sub(r"\s+", " ", extra)[:800], "근거": src_of(rd)})
    return rows, issues


# ───────────────────────── 금지·제한 (관문 A) ─────────────────────────
DEFAULT_DESIGN["prohibited_rules.csv"] = """rule_id,근거,chunk_key_spec,인용,대상,분류번호_선택,action,허용_R코드,필요한_입력,모를_때,note
PR-L1,법 제13조의2 ② 1호,WMA:13의2/항2,1. 폐석면,폐석면,금지:07,BLOCK_TO_GATE4,,,,
PR-L1b,법 제13조의2 ② 1호 (해석 필요),WMA:13의2/항2,1. 폐석면,폐석면고형화처리물(석면 1% 이상),전체:03-06-05,NEEDS_REVIEW,,,,"폐석면에 포함되는지 해석 필요. 4의3에 유형 없음 → 최소한 R-11만"
PR-L2,법 제13조의2 ② 2호,WMA:13의2/항2,폴리클로리네이티드비페닐(PCBs)이 기후에너지환경부령으로 정하는 농도 이상 들어있는 폐기물,PCB 함유 폐기물,금지:08,BLOCK_TO_GATE4,,,,
PR-L2b,법 제13조의2 ② 2호,WMA:13의2/항2,폴리클로리네이티드비페닐(PCBs)이 기후에너지환경부령으로 정하는 농도 이상 들어있는 폐기물,PCB 함유 가능 품목(폐절연유 등),전체:06-01-07,NEEDS_INFO,,analysis.pcb,"PCB 분석 결과가 있나요? (전기절연유는 1리터당 2밀리그램 미만인지 확인)","농도 기준 조문(시행규칙) 미확인 → 임시로 시행령 별표 1 제8호 기준 사용 (액상 2mg/L, 액상 외 용출액 0.003mg/L)"
PR-L3,법 제13조의2 ② 3호,WMA:13의2/항2,의료폐기물(태반은 제외한다),의료폐기물,금지:10,BLOCK_TO_GATE4,,,,
PR-L3b,법 제13조의2 ② 3호 (태반 예외),WMA:13의2/항2,의료폐기물(태반은 제외한다),태반,전체:10-12-06,RESTRICT,R-4-10,,,규칙 별표 4의3에서 R-4-10만 허용
PR-D1,시행령 별표 4의3 제1호,WMA_DEC:B4의3,「화학물질의 등록 및 평가 등에 관한 법률」 제27조제1항에 따라 금지물,금지물질,금지:09-01,BLOCK_TO_GATE4,,,,
PR-D1b,시행령 별표 4의3 제1호,WMA_DEC:B4의3,「화학물질의 등록 및 평가 등에 관한 법률」 제27조제1항에 따라 제한물,허가·제한물질,전체:09-04-00,RESTRICT,R-2-1,,,규칙 별표 4의3에서 R-2-1만 허용
PR-D1c,시행령 별표 4의3 제1호,WMA_DEC:B4의3,「산업안전보건법」 제117조제1항에 따라 제조 등이 금지된 물질,"다른 분류번호인데 금지·제한물질이 들어 있거나 그 물질 자체를 폐기",,NEEDS_INFO,,banned_restricted_substance,금지·제한 화학물질이 들어 있거나 그 물질 자체를 폐기하나요?,분류번호로 판별 불가 → 파트 1 입력
PR-D2,시행령 별표 4의3 제2호,WMA_DEC:B4의3,폐농약(「농약관리법」 제2조제1호에 따른 농약 중 폐기되는 것을 말한다),폐농약,금지:01-03,BLOCK_TO_GATE4,,,,
PR-D3,시행령 별표 4의3 제3호,WMA_DEC:B4의3,폐의약품(「약사법」 제2조제4호에 따른 의약품 중 폐기되는 것을 말한다),폐의약품,금지:51-42,BLOCK_TO_GATE4,,,,
PR-D4,시행령 별표 4의3 제4호,WMA_DEC:B4의3,의료폐기물을 멸균ㆍ분쇄한 잔재물,의료폐기물 멸균분쇄잔재물,금지:51-46,BLOCK_TO_GATE4,,,,
PR-D5,시행령 별표 4의3 제5호,WMA_DEC:B4의3,세부분류에 해당하지 않는,별표 4의3에 없는 유형으로 재활용,,NO_ROUTE,,,,경로의 R코드가 해당 분류번호의 허용 R코드에 없으면 경로 생성 안 함 (재활용환경성평가 승인 시 예외)
PR-D5b,시행령 별표 4의3 제5호 + 규칙 별표 4의3 비고 2,WMA_DEC:B4의3,재활용환경성평가를 받아 재활용하는 경우는,4의3에 유형이 없는 분류번호,미정:*,RESTRICT,R-11,,,재활용환경성평가 승인 경로만 (관문 3 장기 후보)
PR-D6,시행령 별표 4의3 제6호,WMA_DEC:B4의3,천연방사성제품폐기물,천연방사성제품폐기물·소각재,"전체:12-00-00;전체:03-05-07;전체:91-20-00",BLOCK_TO_GATE4,,,,6호는 재활용환경성평가 예외 문구 없음 → R-11도 막음
PR-D6b,시행령 별표 4의3 제6호,WMA_DEC:B4의3,천연방사성제품폐기물,"다른 분류번호인데 천연방사성 제품(음이온·모나자이트 등) 폐기물",,NEEDS_INFO,,natural_radioactive,음이온·모나자이트 등 천연방사성 물질이 든 제품인가요?,
PR-D7,시행령 별표 4의3 제7호,WMA_DEC:B4의3,기후에너지환경부장관이 재활용하는 경우,장관 고시 폐기물,전체:30-00-00,NEEDS_REVIEW,,,,고시 존재 여부 확인 전
PR-R1,시행규칙 별표 4의3 (재활용 금지),WMA_RULE:B4의3,재활용 금지,연구·검사용 폐시약,금지:09-02,BLOCK_TO_GATE4,,,,법·시행령 목록에는 없고 규칙 표에서 금지
PR-R0,시행규칙 별표 4의3 (재활용 금지) 전체,WMA_RULE:B4의3,재활용 금지,4의3에서 재활용 금지인 모든 분류번호,금지:*,BLOCK_TO_GATE4,,,,안전망: 개정으로 새 금지 코드가 생겨도 자동 차단
"""


def load_config(name):
    p = CONFIG / name
    if not p.exists() and name in DEFAULT_DESIGN:
        CONFIG.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_DESIGN[name], encoding="utf-8-sig")
        print(f"config/{name} 없음 → 기본값으로 생성: {p}")
    return list(csv.DictReader(p.open(encoding="utf-8-sig")))


def watched_text(law_id, spec_prefix):
    p = WATCHED / f"{law_id}.jsonl"
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["spec"] == spec_prefix or r["spec"].startswith(spec_prefix):
                return r
    return None


def build_prohibited(codes, rtypes, not_in43, rows_rule):
    """규칙(사람이 config 에 적음) × 분류번호(원문 표에서 코드가 채움) → 한 줄씩.
    분류번호_선택: 금지:접두어 (4의3 재활용 금지 중), 전체:코드, 미정:* (4의3에 유형 없음), 비우면 입력 기반 규칙."""
    rules = load_config("prohibited_rules.csv")
    ban = sorted({r["분류번호"] for r in rtypes if r["재활용_금지"] == "Y"})
    leafs = {r["분류번호"]: r["명칭"] for r in codes}
    names = {**leafs, **{r["분류번호"]: r["폐기물의_종류"] for r in rtypes}}
    out, issues, covered = [], [], set()
    nz = lambda t: re.sub(r"\s+", "", t.replace("ㆍ", "·"))
    for rule in rules:
        sel = [x.strip() for x in rule["분류번호_선택"].split(";") if x.strip()]
        picked = []
        for x in sel:
            kind, _, arg = x.partition(":")
            if kind == "금지":
                picked += [c for c in ban if arg == "*" or c.startswith(arg)]
            elif kind == "전체":
                if arg in leafs:
                    picked.append(arg)
                else:
                    issues.append(f'{rule["rule_id"]}: 별표 4에 없는 분류번호 {arg}')
            elif kind == "미정":
                picked += list(not_in43)
        if sel and not picked:
            issues.append(f'{rule["rule_id"]}: 선택 결과 0건 ({rule["분류번호_선택"]}) — 개정으로 코드가 바뀌었는지 확인')
        if rule["rule_id"] != "PR-R0" and any(s.startswith("금지:") for s in sel):
            covered |= set(picked)
        # 인용 검증: 인용 문구가 원문(data/watched)에 실제로 있는지
        law_id, _, spec = rule["chunk_key_spec"].partition(":")
        src = rows_rule.get(spec) if law_id == "WMA_RULE" else watched_text(law_id, spec)
        if src is None:
            cite = "원문 없음"
            issues.append(f'{rule["rule_id"]}: 원문 {rule["chunk_key_spec"]} 을 data/watched 에서 못 찾음')
        elif nz(rule["인용"]) not in nz(src["text"]):
            cite = "불일치"
            issues.append(f'{rule["rule_id"]}: 인용 문구가 원문에 없음 → 사람 확인')
        else:
            cite = "확인"
        base = {k: rule[k] for k in ("rule_id", "근거", "대상", "action", "허용_R코드", "필요한_입력", "모를_때", "note")}
        base.update({"인용_검증": cite, "근거_청크": src_of(src) if src else rule["chunk_key_spec"]})
        if picked:
            for c in picked:
                out.append({**base, "분류번호": c, "명칭": names.get(c, "")})
        else:
            out.append({**base, "분류번호": "", "명칭": ""})
    unc = [c for c in ban if c not in covered]
    for c in unc:
        issues.append(f"{c}: 4의3 재활용 금지인데 개별 규칙 없음 (PR-R0 안전망으로만 차단)")
    return out, issues


# ───────────────────────── 저장·검증 ─────────────────────────
def write_csv(path, rows):
    if not rows:
        return
    cols = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def cmd_build(src=None):
    rows = load_watched("WMA_RULE", src)
    r4, r42, r43 = pick(rows, "B4"), pick(rows, "B4의2"), pick(rows, "B4의3")
    eff = r43.get("effective") or "unknown"
    out = STAGED / eff
    out.mkdir(parents=True, exist_ok=True)
    rep = [f"# 표 생성 보고서 — 시행규칙 시행일 {eff}", f"생성: {datetime.datetime.now():%Y-%m-%d %H:%M}", ""]

    codes = build_waste_codes(r4)
    rdet = build_rcode_detail(r42)
    children = {}
    for r in rdet:
        if r["상위"]:
            children.setdefault(r["상위"], []).append(r["r_code"])
    rtypes, issues = build_recycling_types(r43, children)

    # 교차 검증
    code_set = {r["분류번호"] for r in codes}
    leaf = {r["분류번호"] for r in codes if r["말단"] == "Y" and CODE3.match(r["분류번호"])}
    in43 = {r["분류번호"] for r in rtypes}
    known_r = {r["r_code"] for r in rdet} | {"R-11"}
    for c in sorted(in43 - code_set):
        issues.append(f"{c}: 별표 4의3에 있으나 별표 4에 없음")
    for r in rtypes:
        if r["r_code"] and r["r_code"] not in known_r:
            issues.append(f'{r["분류번호"]}: 별표 4의2에 없는 R코드 {r["r_code"]}')
    not_in43 = sorted(leaf - in43)
    nm = lambda s: s.replace(" ", "").replace("ㆍ", "·")
    names4 = {r["분류번호"]: r["명칭"] for r in codes}
    name_diff = sorted({(r["분류번호"], names4.get(r["분류번호"], ""), r["폐기물의_종류"])
                        for r in rtypes if nm(names4.get(r["분류번호"], "")) != nm(r["폐기물의_종류"])})

    write_csv(out / "waste_codes_4.csv", codes)
    write_csv(out / "hazardous_codes.csv", [r for r in codes if r["지정폐기물"] == "Y" and r["말단"] == "Y"])
    write_csv(out / "rcode_detail_4_2.csv", rdet)
    write_csv(out / "recycling_types_4_3.csv", rtypes)
    write_csv(out / "not_in_4_3.csv", [{"분류번호": c, "상태": "별표 4의3에 유형 없음 → 비고2 재활용환경성평가(R-11)만"} for c in not_in43])
    desig = watched_text("CEA_DESIG", "B2")
    if desig:
        dr, d_issues = build_designated(desig)
        bad = sorted({r["분류번호"] for r in dr} - {r["분류번호"] for r in codes})
        d_issues += [f"지정 고시 분류번호 {c}가 별표 4에 없음" for c in bad]
        known_all = {r["r_code"] for r in rdet}
        d_issues += [f'지정 품목 {r["품목"]}: 없는 R코드 {x}' for r in dr for x in r["허용_R코드"].split(";") if x not in known_all]
        write_csv(out / "designated_resources.csv", dr)
        issues += d_issues
    else:
        dr = []
        issues.append("CEA_DESIG 발췌 없음 → designated_resources 생략 (law_watch.py extract CEA_DESIG)")
    pr, pr_issues = build_prohibited(codes, rtypes, not_in43, rows)
    write_csv(out / "prohibited_restricted.csv", pr)
    issues += pr_issues

    n_code = len(in43)
    rep += ["## 결과", "| 표 | 행 | 비고 |", "|---|---|---|",
            f"| waste_codes_4 | {len(codes)} | 말단 세분류 {len(leaf)} |",
            f"| hazardous_codes | {sum(1 for r in codes if r['지정폐기물']=='Y' and r['말단']=='Y')} | 지정폐기물 말단 |",
            f"| rcode_detail_4_2 | {len(rdet)} | |",
            f"| recycling_types_4_3 | {len(rtypes)} | 분류번호 {n_code}, 재활용 금지 {sum(1 for r in rtypes if r['재활용_금지']=='Y')}, "
            f"묶음 2개 이상 {len({r['분류번호'] for r in rtypes if r['묶음']==2})}, 유형 미정 {sum(1 for r in rtypes if r['상태'].startswith('유형 미정'))} |",
            f"| not_in_4_3 | {len(not_in43)} | 별표 4 말단인데 4의3에 없음 |",
            f"| designated_resources | {len(dr)} | 지정 품목 {len({r['품목'] for r in dr})}개 (순환자원 지정 고시 별표 2) |",
            f"| prohibited_restricted | {len(pr)} | 규칙 {len({r['rule_id'] for r in pr})}개, 인용 확인 {len({r['rule_id'] for r in pr if r['인용_검증']=='확인'})}개 |"]

    try:
        rf5 = pick(rows, "BF5")
        tc = build_treatment_codes(rf5)
        tc, notes = overlay(tc, "code", "treatment_design.csv")
        write_csv(out / "treatment_code_map.csv", tc)
        rep.append(f"| treatment_code_map | {len(tc)} | 설계칸 overlay: config/treatment_design.csv |")
        issues += notes
    except KeyError:
        rep.append("| treatment_code_map | - | BF5 발췌 없음 |")

    # 같은 종류의 경고는 묶어서 보여 준다
    grouped = {}
    for x in issues:
        k = re.sub(r"^\S+(?: 묶음\d+)?: ", "", x)
        grouped.setdefault(k, []).append(x.split(":")[0])
    rep += ["", f"## 확인 필요 ({len(issues)})"]
    rep += [f"- {k} — {', '.join(v[:8])}{' …' if len(v) > 8 else ''}" for k, v in grouped.items()] or ["없음"]
    rep += ["", f"## 참고: 별표 4와 4의3의 명칭 차이 ({len(name_diff)}) — 원문끼리 표기가 다름. 판정은 분류번호로 하므로 영향 없음"]
    rep += [f"- {c}: 별표4 「{a}」 / 4의3 「{b}」" for c, a, b in name_diff]
    manifest = {"effective": eff, "built": datetime.datetime.now().isoformat(timespec="seconds"),
                "sources": {k: src_of(v) for k, v in rows.items()}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "build_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    print(f"\n→ {out}")


def read_rows(p):
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def row_key(name, r):
    return tuple(r.get(k, "") for k in {
        "recycling_types_4_3.csv": ("분류번호", "묶음", "r_code"),
        "treatment_code_map.csv": ("code",), "rcode_detail_4_2.csv": ("r_code",),
        "prohibited_restricted.csv": ("rule_id", "분류번호"),
        "gate3_rules.csv": ("rule_id",), "disposal_methods_5.csv": ("rule_id",),
        "reporter_scope.csv": ("scope_id",), "condition_texts.csv": ("condition_id",),
        "designated_resources.csv": ("품목", "분류번호"),
        "partners_recycling.csv": ("업체_키", "폐기물_구분", "폐기물코드", "처리방법코드", "주소"),
        "waste_code_crosswalk.csv": ("폐기물코드",),
        "demand_designated.csv": ("품목", "업체_키", "공장주소"),
    }.get(name, ("분류번호",)))


def cmd_diff(eff=None):
    eff = eff or sorted(p.name for p in STAGED.iterdir())[-1]
    st = STAGED / eff
    lines = [f"# staged/{eff} vs active", ""]
    for p in sorted(st.glob("*.csv")):
        if p.name.endswith("_rejected.csv"):
            continue
        a = {row_key(p.name, r): r for r in read_rows(ACTIVE / p.name)}
        s = {row_key(p.name, r): r for r in read_rows(p)}
        if len(s) < len(read_rows(p)):
            lines.append(f"⚠️ {p.name}: 비교 키가 겹치는 행이 있음 (row_key 설정 확인)")
        ign = {"근거", "근거_청크", "모델", "status"}
        add = [k for k in s if k not in a]
        rem = [k for k in a if k not in s]
        chg = [k for k in s if k in a and {x: y for x, y in s[k].items() if x not in ign} != {x: y for x, y in a[k].items() if x not in ign}]
        lines.append(f"## {p.name}: +{len(add)} −{len(rem)} ~{len(chg)}" + ("  (active 없음 = 최초)" if not a else ""))
        if a:
            lines += [f"- 추가 {k}" for k in add[:50]] + [f"- 삭제 {k}" for k in rem[:50]]
            for k in chg[:50]:
                d = {x: (a[k].get(x), s[k].get(x)) for x in s[k] if x not in ign and a[k].get(x) != s[k].get(x)}
                lines.append(f"- 변경 {k}: {d}")
    (st / "diff_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def cmd_promote(eff):
    st = STAGED / eff
    if not st.exists():
        sys.exit(f"없음: {st}")
    if ACTIVE.exists():
        dst = OLD / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ACTIVE), str(dst))
        print(f"active → {dst}")
    shutil.copytree(st, ACTIVE)
    print(f"staged/{eff} → active  (회귀 테스트 다시 실행하세요)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "build":
        cmd_build(a[1] if len(a) > 1 else None)
    elif a[0] == "diff":
        cmd_diff(a[1] if len(a) > 1 else None)
    elif a[0] == "promote" and len(a) > 1:
        cmd_promote(a[1])
    else:
        print(__doc__)