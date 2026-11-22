"""regress_tables.py — 판정 표 회귀 테스트 (관문 A·1·2의 법령 판단 부분)

    python regress_tables.py                 # data/active 표로 tests/regress_cases.csv 50건 검사
    python regress_tables.py staged          # 최신 data/staged/{시행일} 표로 검사 (promote 전 확인용)

무엇을 검사하나
- 사례마다 사람이 정한 사실(품목 해당 여부, 조건 충족, 상대 종류·자격 범위, 일 용량)은 tests/regress_cases.csv 에 고정.
- 법령 판단은 표에서만 꺼낸다:
    관문 A  prohibited_restricted (BLOCK / RESTRICT)
    관문 1  designated_resources (분류번호가 지정 품목인지, R코드가 허용 용도인지)
    관문 2  recycling_types_4_3 (분류번호×R코드 허용, 사전분석 해당 여부) + reporter_scope (별표 16 허용 R코드·용량 기준)
- 개정 후 표가 바뀌어 결과가 달라지면 FAIL 로 드러난다. 기대값이 법 개정으로 정말 바뀐 것이면 regress_cases.csv 를 고친다.
상대 업체 목록(허가·신고 명단) 조회는 파트 2 판정 코드 몫이라 여기서는 '상대' 칸 값으로 대신한다.
"""
import csv, re, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def table_dir():
    if len(sys.argv) > 1 and sys.argv[1] == "staged":
        return sorted(p for p in (DATA / "staged").iterdir() if p.is_dir())[-1]
    return DATA / "active"


def rd(d, name):
    p = d / name
    if not p.exists():
        sys.exit(f"표 없음: {p}")
    return list(csv.DictReader(p.open(encoding="utf-8-sig")))


def load(d):
    t = {}
    t["types"] = {}
    for r in rd(d, "recycling_types_4_3.csv"):
        if r["r_code"]:
            t["types"].setdefault((r["분류번호"], r["r_code"]), set()).add(r["사전분석확인"] or "해당")  # 빈 값 = 해당(보수)
    t["ban"] = {}
    for r in rd(d, "prohibited_restricted.csv"):
        if r["분류번호"] and r["action"] in ("BLOCK_TO_GATE4", "RESTRICT"):
            t["ban"].setdefault(r["분류번호"], []).append(r)
    t["desig"] = rd(d, "designated_resources.csv")
    t["rs"] = {r["scope_id"]: r for r in rd(d, "reporter_scope.csv")}
    return t


def gate_a(t, code, rc):
    for r in t["ban"].get(code, []):
        if r["action"] == "BLOCK_TO_GATE4":
            return "불가", f'관문A {r["rule_id"]} 차단'
        if r["action"] == "RESTRICT" and rc and rc not in r["허용_R코드"].split(";"):
            return "불가", f'관문A {r["rule_id"]} 제한(허용 {r["허용_R코드"]})'
    return None, ""


def gate1(t, c):
    code, rc = c["분류번호"], c["r_code"]
    items = [r for r in t["desig"] if r["분류번호"] == code]
    if not items:
        return "불가", "지정 품목 아님"
    if not rc:
        return "불가", "상대 용도 없음"
    allowed = set(";".join(r["허용_R코드"] for r in items).split(";"))
    if rc not in allowed:
        return "불가", f"{rc} 는 지정 용도 아님 (허용 {sorted(allowed)})"
    need_q = any(r["품목_한정"] == "Y" for r in items) or len(items) > 1 or "양곡가공업" in "".join(r["방법기준_원문"] for r in items)
    item = c["g1_품목해당"]
    if need_q and item == "?":
        return "확인 필요", "품목 해당 여부 질문"
    if need_q and item == "N":
        return "불가", "품목 한정·발생자 조건 불충족"
    cond = c["g1_조건"]
    if cond == "?":
        return "확인 필요", "혼합·이물질 등 조건 질문"
    if cond == "N_block":
        return "불가", "방법·기준(이물질 등) 불충족"
    if cond == "N_warn":
        return "가능(조건 미충족)", "보관 조건 개선 필요"
    return "가능", f'{items[0]["품목"]} {rc}'


def gate2(t, c):
    code, rc = c["분류번호"], c["r_code"]
    if not rc:
        return "불가", "상대 용도 없음"
    pre = t["types"].get((code, rc))
    if not pre:
        return "불가", f"별표 4의3: {code} 에 {rc} 없음"
    ok = "가능(사전 분석 필요)" if "해당" in pre else "가능"
    p, scope = c["상대"], c["상대_범위"]
    if p in ("none", "virtual", "self_only"):
        return "불가", f"자격 있는 상대 없음({p})"
    if scope == "N":
        return "불가", "상대 허가·신고 범위 밖"
    if p == "report" and c["신고_근거"].startswith("B16-"):
        rs = t["rs"].get("RS-" + c["신고_근거"])
        if not rs:
            return "FAIL", f'reporter_scope 에 RS-{c["신고_근거"]} 없음'
        if rs["허용_R코드"] and rc not in rs["허용_R코드"].split(";"):
            return "불가", f'별표 16 {c["신고_근거"]} 허용 R코드 {rs["허용_R코드"]} 밖'
        m = re.search(r"([\d.]+)\s*톤 미만", rs["수치_기준"])
        if m:
            tons = c["일_용량_톤"]
            if tons in ("", "?"):
                return "확인 필요", f'일 용량 질문 ({rs["수치_기준"]})'
            if float(tons) >= float(m.group(1)):
                return "불가", f'일 {tons}톤 ≥ {rs["수치_기준"]}'
    if scope == "?":
        return "확인 필요", "상대 처리 방법·범위 질문"
    return ok, f"4의3 {rc} 사전분석 {'/'.join(sorted(pre))}"


LEGAL = r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(합\)|합자회사|\(사\)|사단법인|\(재\)|재단법인|농업회사법인|영농조합법인|\(합명\)"
core = lambda s: re.sub(r"\([^)]*\)$", "", re.sub(r"[\s·ㆍ.,'\"]+", "", re.sub(LEGAL, "", s or "")))


def partner_from_data(P, c):
    """partners_recycling 에서 상대 종류·허가 범위를 직접 찾는다. 가상 업체(목록에 없음)는 사례 값 유지."""
    if c["상대"] in ("virtual", "none") or not c["상대기업"]:
        return c["상대"], c["상대_범위"], ""
    hit = [p for p in P if core(p["업체명"]) == core(c["상대기업"])]
    if not hit:
        return "none", "", "목록에 없음"
    code, top = c["분류번호"], re.match(r"R-\d+", c["r_code"] or "R-0").group(0)
    rel = [p for p in hit if code.startswith(p["분류번호_중분류"])]
    ent = [p for p in rel if p["위탁가능"] == "Y"]
    if not ent:
        return ("self_only" if rel else "none"), "", "해당 코드 위탁 없음" if rel else "해당 코드 없음"
    kind = "permit" if any(p["허가신고"] == "허가" for p in ent) else "report"
    methods = {p["r_code"] for p in ent}
    if kind == "permit":
        return kind, ("Y" if top in methods else "N"), f"허가 {sorted(methods)}"
    return kind, c["상대_범위"], f"신고 {sorted(methods)}"  # 신고자 범위는 제66조·별표16 해석이 필요해 사례 값 유지


def main():
    d = table_dir()
    t = load(d)
    cases = list(csv.DictReader((BASE / "tests" / "regress_cases.csv").open(encoding="utf-8-sig")))
    pf = d / "partners_recycling.csv"
    P = list(csv.DictReader(pf.open(encoding="utf-8-sig"))) if pf.exists() else None
    diffs = []
    res, fails = [], 0
    for c in cases:
        if P is not None and "상대기업" in c:
            k, sc, note = partner_from_data(P, c)
            if (k, sc) != (c["상대"], c["상대_범위"]):
                diffs.append(f'{c["case"]} {c["상대기업"]}: 사례값 {c["상대"]}/{c["상대_범위"] or "-"} → 데이터 {k}/{sc or "-"} ({note})')
            c = {**c, "상대": k, "상대_범위": sc}
        a, why_a = gate_a(t, c["분류번호"], c["r_code"])
        g1, w1 = (a, why_a) if a else gate1(t, c)
        if g1.startswith("가능"):
            g2, w2 = "판정 불필요", "관문1 통과"
        else:
            g2, w2 = (a, why_a) if a else gate2(t, c)
        ok1, ok2 = g1 == c["기대_관문1"], g2 == c["기대_관문2"]
        fails += not (ok1 and ok2)
        res.append((c, g1, w1, ok1, g2, w2, ok2))
    lines = [f"# 회귀 테스트 — 표: {d.relative_to(BASE)}", "",
             f"**{len(cases) - fails}/{len(cases)} 통과**  (상대 기업: {'partners_recycling 데이터로 조회' if P is not None else '사례 값'})", "",
             "| 케이스 | 설명 | 관문1 결과 | 기대 | 관문2 결과 | 기대 | 근거 |", "|---|---|---|---|---|---|---|"]
    for c, g1, w1, ok1, g2, w2, ok2 in res:
        mark = "" if ok1 and ok2 else " ❌"
        lines.append(f'| {c["case"]}{mark} | {c["설명"]} | {g1} | {c["기대_관문1"]} | {g2} | {c["기대_관문2"]} | {w1} / {w2} |')
    if P is not None:
        lines += ["", f"## 상대 기업: 사례 값과 데이터가 다른 곳 ({len(diffs)})"] + [f"- {x}" for x in diffs]
    out = d / "regress_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(cases) - fails}/{len(cases)} 통과  → {out}")
    for x in diffs:
        print("  ·", x)
    for c, g1, w1, ok1, g2, w2, ok2 in res:
        if not (ok1 and ok2):
            print(f'  ❌ {c["case"]} {c["설명"]}: 관문1 {g1}(기대 {c["기대_관문1"]}, {w1}) / 관문2 {g2}(기대 {c["기대_관문2"]}, {w2})')
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
