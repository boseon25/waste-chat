#!/usr/bin/env python3
"""review_tables.py — 법 기준 표 3개를 원문과 자동 대조하고, 통과한 행만 confirmed로 바꾼 판을 staged에 만든다.

    python review_tables.py                 # 대조만 → 보고서 (data/tables/staged/review_{날짜}/review_report.md)
    python review_tables.py --confirm       # 대조 통과 행을 confirmed로 바꾼 표도 staged/review_{날짜}/에 저장
    python review_tables.py disposal --confirm   # 표 이름 일부로 골라서
    → 보고서를 사람이 읽고 맞으면: cp staged/review_{날짜}/*.csv staged/{시행일}/ → build_tables.py diff → promote

대상 표 (data/tables/active 우선, 없으면 최신 staged)
  plan_check_targets.csv         시행규칙 제18조의2 (처리계획 확인 대상)
  energy_recovery_criteria.csv   시행규칙 제3조 제1항 (에너지 회수기준)
  designated_check_targets.csv   시행규칙 제17조의2 + 배출자신고 지침 Ⅵ (지정폐기물 사전 확인)
  disposal_methods_5.csv         시행규칙 별표 5 제3·4호 (관문 4 처분 방법, LLM 초안)
  condition_texts.csv            법 제17·18조 등 (서류 안내 문구, LLM 초안) — 안내문구는 항상 사람이 읽음(경고)
  reporter_scope.csv             법 제46조, 시행규칙 제66조·별표 16 (신고 업체 범위, LLM 초안)
  LLM 초안 표의 근거 키는 발췌 기준 해시라 조각 해시와 다를 수 있어, 해시 대신 "인용이 현재 원문에 있는지"로 본다.

대조 항목
  - 근거 키의 조각이 현재 원문(chunk_v2 최신 시행일)에 있는지, 해시가 같은지 (원문이 바뀌었으면 실패)
  - 표의 원문·인용 문장이 그 조각 안에 글자 그대로 있는지
  - 숫자(기준량·합계·기준값·기한)가 원문 문장에 있는지
  - 분류번호(앞자리 포함)가 별표 4(waste_codes_4)에 있는지, 명칭이 품목과 맞는지 (명칭은 경고만)
  - 처리계획 표: 제1항 호가 원문 호 개수만큼 다 있는지
자동 대조는 "원문과 다르지 않다"까지만 확인한다. 품목 → 분류번호 대응처럼 해석이 들어간 칸은 경고를 사람이 본다.
표준 라이브러리만 사용.
"""
import csv, datetime, hashlib, json, os, re, sys
from pathlib import Path


def _data_root():
    env = os.environ.get("WASTE_DATA_DIR")
    if not env:
        return Path(__file__).resolve().parents[2] / "data"
    p = Path(env)
    return p.parent.parent if p.name in ("active", "staged", "old") and p.parent.name == "tables" else p


DATA = _data_root()
TABLES = DATA / "tables"
ACTIVE, STAGED = TABLES / "active", TABLES / "staged"
CHUNKS = Path(os.environ.get("WASTE_LAW_DIR") or DATA / "law" / "chunk_v2")

NORM = [(r"기후에너지환경부", "환경부"), (r"<(개정|신설|삭제|본조신설|전문개정|제목개정|종전)[^>]*>", ""),
        (r"\[(본조신설|전문개정|제목개정|종전)[^\]]*\]", ""), (r"\s+", "")]
NZ = lambda t: re.sub(r"[\s│┃├┤┼─┬┴┌┐└┘]+", "", (t or "").replace("ㆍ", "·").replace("‧", "·"))
ALIAS = {"PCB": "폴리클로리네이티드비페닐", "폐래커": "폐락카", "고형화처리물": "고형화"}


def h(text):
    for p, r in NORM:
        text = re.sub(p, r, text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── 읽기 ─────────────────────────────────────────────────────────────
def read(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def find_table(name):
    if (ACTIVE / name).exists():
        return ACTIVE / name
    st = sorted((p for p in STAGED.iterdir() if p.is_dir() and (p / name).exists()), key=lambda p: p.stat().st_mtime) \
        if STAGED.exists() else []
    return st[-1] / name if st else None


_chunks = {}


def chunks(law):
    if law not in _chunks:
        files = sorted((CHUNKS / law).glob("*.jsonl"))
        d = {}
        if files:
            for line in files[-1].read_text(encoding="utf-8").splitlines():
                if line.strip():
                    c = json.loads(line)
                    # 별표 조각(type annex)은 "B16"처럼 B를 붙여 둔다 — 조문 "16"(제16조)과 키가 겹치지 않게
                    k = ("B" + c["key"]) if c.get("type") == "annex" and not c["key"].startswith("B") else c["key"]
                    d.setdefault(k, c)
        _chunks[law] = d
    return _chunks[law]


REF = re.compile(r"^([A-Z_]+):(.+?)@(\d{8})(?:#([0-9a-f]+))?$")


def resolve(ref):
    """근거 키 → (조각, 문제 목록)"""
    m = REF.match((ref or "").strip())
    if not m:
        return None, [f"근거 키 형식 아님: {ref}"]
    law, key, eff, hh = m.groups()
    c = chunks(law).get(key)
    if c is None:
        return None, [f"원문에 {law}:{key} 없음"]
    probs = []
    if str(c.get("effective")) != eff:
        probs.append(f"시행일 {eff} → 현재 원문 {c.get('effective')}")
    if hh and c.get("hash") != hh:
        probs.append(f"원문 해시가 다름 (표 {hh} / 현재 {c.get('hash')}) → 원문이 바뀜, 표 다시 만들기")
    return c, probs


def code_names():
    p = find_table("waste_codes_4.csv")
    return {r["분류번호"]: r["명칭"] for r in read(p)} if p else {}


def check_codes(sel, target, names):
    """분류번호 앞자리가 별표 4에 있는지(실패) / 명칭이 품목과 맞는지(경고)"""
    fails, warns = [], []
    t = NZ(target)
    for k, v in ALIAS.items():
        t = t.replace(NZ(k), NZ(v))
    for c in [x for x in (sel or "").split(";") if x]:
        hits = [k for k in names if k == c or k.startswith(c + "-")]
        if not hits:
            fails.append(f"분류번호 {c}가 별표 4에 없음")
            continue
        # 말단 명칭이 품목과 글자로 안 맞아도 중분류·대분류 명칭이 맞으면 통과 (예: 51-01-01 정수처리오니 ⊂ 51-01 유기성오니류)
        parts = c.split("-")
        cands = [names.get(c) or names[sorted(hits)[0]]] + [names.get("-".join(parts[:i])) for i in (2, 1)]
        cands += [names.get("-".join(parts[:i]) + "-00" * (3 - i)) for i in (2, 1)]
        words = [w for w in re.split(r"[,·및또는()]", t) if len(w) >= 2]
        ok = any(n and any(w[:2] in NZ(n) or NZ(n)[:2] in w for w in words) for n in cands)
        if not ok:
            warns.append(f"{c} 명칭 '{cands[0]}' ↔ 품목 '{target}' 확인")
    return fails, warns


# ── 표별 대조 ────────────────────────────────────────────────────────
def review_plan(rows, names):
    out = []
    want = None
    art = chunks("WMA_RULE").get("18의2")
    if art and art.get("counts"):
        want = (art["counts"].get("호") or {}).get("1")
    have = {r["호"] for r in rows if r["구분"] == "대상"}
    for r in rows:
        c, fails = resolve(r["근거키"])
        warns = []
        if c is not None and NZ(r["원문"]) not in NZ(c["text"]):
            fails.append("표의 원문 문장이 원문 조각에 없음")
        text = r["원문"]
        if r["구분"] == "대상":
            for col in ("각각_kg_월", "합계_kg_월"):
                v = r.get(col) or ""
                if v and v not in ("0", "고시") and not re.search(rf"{v}\s*킬로그램", text.replace(",", "")):
                    fails.append(f"{col} {v}kg가 원문에 없음")
            if r.get("각각_kg_월") == "0" and re.search(r"킬로그램", text):
                fails.append("양과 관계없음(0)인데 원문에 kg 기준이 있음")
            f2, w2 = check_codes(r["분류번호_앞자리"], r["대상"], names)
            fails += f2
            warns += w2
        else:
            for part in [x.strip() for x in (r.get("내용") or "").split(";") if x.strip()]:
                val = part.split("=", 1)[-1]
                if NZ(val) not in NZ(text):
                    fails.append(f"'{val}'가 원문에 없음")
        out.append((r, fails, warns))
    extra = []
    if want is not None and len(have) != want:
        extra.append(f"제1항 호 {len(have)}개 / 원문 {want}개 → 빠진 호 있음")
    elif want is None:
        extra.append("원문 counts 없음 → 호 개수 대조 생략 (law_chunk 새 판으로 다시 받기)")
    return out, extra


def review_energy(rows, names):
    out = []
    for r in rows:
        c, fails = resolve(r["근거키"])
        if c is not None and NZ(r["원문"]) not in NZ(c["text"]):
            fails.append("표의 원문 문장이 원문 조각에 없음")
        v = r.get("기준값") or ""
        if v:
            kilo = str(int(v) // 1000) + "천" if v.isdigit() and int(v) >= 1000 and int(v) % 1000 == 0 else None
            if not (v in r["원문"] or (kilo and kilo in r["원문"])):
                fails.append(f"기준값 {v}가 원문에 없음")
        out.append((r, fails, []))
    return out, []


def review_designated(rows, names):
    out = []
    for r in rows:
        c, fails = resolve(r.get("근거_청크"))
        warns = []
        if c is not None and NZ(r["근거_인용"]) not in NZ(c["text"]):
            fails.append("근거_인용이 원문 조각에 없음")
        if r.get("적용_분류번호"):
            f2, w2 = check_codes(r["적용_분류번호"], r["대상"], names)
            fails += f2
            warns += w2
        out.append((r, fails, warns))
    ids = {}
    for r in rows:
        ids.setdefault((r["구분"], NZ(r["대상"])[:20]), []).append(r["rule_id"])
    extra = [f"같은 내용 중복: {v}" for v in ids.values() if len(v) > 1]
    return out, extra


# ── LLM 초안 표 공통 (disposal_methods_5, condition_texts, reporter_scope) ──
def _find_quote(law, quote, prefer=None):
    """근거 키로 못 찾았을 때: 같은 법령의 현재 원문 어디에 인용이 있는지. prefer(예: "B16")로 시작하는 조각을 먼저 본다"""
    q = NZ(quote)
    items = list(chunks(law).items())
    if prefer:
        items.sort(key=lambda kv: not (kv[0] == prefer or kv[0].startswith(prefer + ":")))
    for k, c in items:
        if q and q in NZ(c.get("text")):
            return {**c, "key": k}
    return None


def resolve_lenient(ref, quote):
    """LLM 초안 표의 근거 키는 발췌(watched) 기준 해시라 조각 해시와 다를 수 있다.
    → 키로 조각을 찾고(별표 'B5:3' ↔ 'B5:3.' 차이 허용), 해시가 달라도 인용이 현재 원문에 있으면 통과(경고)."""
    m = REF.match((ref or "").strip())
    if not m:
        return None, [f"근거 키 형식 아님: {ref}"], []
    law, key, eff, hh = m.groups()
    d = chunks(law)
    base = key.split(":")[0].split("/")[0]
    c = d.get(key) or d.get(key + ".") or d.get(base)
    fails, warns = [], []
    q = NZ(quote)
    if c is not None and q and q not in NZ(c.get("text")):
        c2 = _find_quote(law, quote, base)
        if c2 is not None:
            if c2["key"].split(":")[0].split("/")[0] != base:
                warns.append(f"인용이 {key}가 아니라 {c2['key']}에 있음")     # 같은 조문·별표 안이면 경고하지 않음
            c = c2
        else:
            fails.append("근거_인용이 현재 원문에 없음 (원문이 바뀌었거나 인용이 틀림)")
    elif c is None:
        c = _find_quote(law, quote, base)
        if c is None:
            return None, [f"원문에 {law}:{key} 없음, 인용도 못 찾음"], []
        if c["key"].split(":")[0].split("/")[0] != base:
            warns.append(f"키 {key} 대신 {c['key']}에서 인용 찾음")
    if c is not None and str(c.get("effective")) != eff and not fails:
        warns.append(f"근거 시행일 {eff} → 현재 원문 {c.get('effective')} (인용은 그대로 있음)")
    return c, fails, warns


def _numbers(text):
    return re.findall(r"\d+(?:\.\d+)?", (text or "").replace(",", ""))


def review_llm(rows, names, idcol, text_col=None, codes_col="적용_분류번호", tc_col=None, tcodes=None):
    out = []
    for r in rows:
        c, fails, warns = resolve_lenient(r.get("근거_청크"), r.get("근거_인용"))
        if len(NZ(r.get("근거_인용"))) < 6:
            fails.append("근거_인용이 너무 짧음")
        src = NZ((c or {}).get("text"))
        for n in _numbers(r.get("수치_기준")):
            if n not in (c or {}).get("text", "").replace(",", ""):
                fails.append(f"수치_기준의 {n}이 원문에 없음")
        if r.get(codes_col):
            f2, w2 = check_codes(r[codes_col], r.get("대상_폐기물") or r.get("트리거") or "", names)
            fails += f2
            warns += [w for w in w2]
        if tc_col and r.get(tc_col):
            bad = [x for x in r[tc_col].split(";") if x and tcodes and x not in tcodes]
            if bad:
                fails.append(f"처리방법 코드 {bad}가 별지 제5호서식 표에 없음")
        if (r.get("confidence") or "").lower() == "low":
            warns.append("confidence low")
        if text_col and r.get(text_col):
            warns.append("안내 문구는 LLM이 쓴 문장 → 사람이 읽고 확인")
        out.append((r, fails, warns))
    return out


def review_disposal(rows, names):
    tm = find_table("treatment_code_map.csv")
    tcodes = {x["code"] for x in read(tm)} if tm else None
    return review_llm(rows, names, "rule_id", tc_col="처리방법_코드", tcodes=tcodes), []


def review_condition(rows, names):
    return review_llm(rows, names, "condition_id", text_col="안내문구"), []


def review_reporter(rows, names):
    return review_llm(rows, names, "scope_id"), []


TABLES_SPEC = [("plan_check_targets.csv", "상태", "행ID", review_plan),
               ("energy_recovery_criteria.csv", "상태", "행ID", review_energy),
               ("designated_check_targets.csv", "status", "rule_id", review_designated),
               ("disposal_methods_5.csv", "status", "rule_id", review_disposal),
               ("condition_texts.csv", "status", "condition_id", review_condition),
               ("reporter_scope.csv", "status", "scope_id", review_reporter)]


def main(argv):
    confirm = "--confirm" in argv
    only = [a for a in argv if not a.startswith("--")]          # 표 이름 일부를 주면 그 표만 (예: disposal)
    names = code_names()
    out_dir = STAGED / f"review_{datetime.date.today():%Y%m%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = [f"# 법 기준 표 자동 대조 — {datetime.datetime.now():%Y-%m-%d %H:%M}", f"원문: {CHUNKS}", ""]
    for name, scol, idcol, fn in TABLES_SPEC:
        if only and not any(o in name for o in only):
            continue
        path = find_table(name)
        if path is None:
            rep += [f"## {name}", "표 없음", ""]
            continue
        rows = read(path)
        res, extra = fn(rows, names)
        ok = sum(1 for _, f, _ in res if not f)
        warn = sum(1 for _, f, w in res if not f and w)
        rep += [f"## {name}  ({path.parent.name}/)", f"통과 {ok} / {len(res)} (그중 경고 {warn}), 실패 {len(res) - ok}", ""]
        rep += [f"- ⚠️ {x}" for x in extra]
        rep += ["", "| 행 | 결과 | 내용 |", "|---|---|---|"]
        for r, f, w in res:
            mark = "실패" if f else ("경고" if w else "통과")
            rep.append(f"| {r[idcol]} | {mark} | {'; '.join(f + w) or '-'} |")
        rep.append("")
        if confirm:
            cols = list(rows[0])
            for r, f, w in res:
                r[scol] = "confirmed" if not f else (r.get(scol) or "draft")
            with (out_dir / name).open("w", encoding="utf-8-sig", newline="") as fp:
                wr = csv.DictWriter(fp, fieldnames=cols)
                wr.writeheader()
                wr.writerows(rows)
    rep += ["## 다음", "- 실패 행: 원문이 바뀌었거나 표 값이 원문과 다름 → build_tables.py build / draft_tables.py로 다시 만들기",
            "- 경고 행: 분류번호 명칭과 품목이 글자로 안 맞음 → 사람이 별표 4에서 확인",
            "- --confirm 결과는 통과 행(경고 포함)만 confirmed. 경고 행이 틀렸으면 그 행만 draft로 되돌린 뒤 반영"]
    (out_dir / "review_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    print(f"\n→ {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1:])