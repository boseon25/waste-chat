"""draft_tables.py — 문장으로 된 법 원문 → 판정 표 초안 (LLM) + 인용 검증

    python draft_tables.py list                         # 표 목록·모델·원문 범위
    python draft_tables.py draft condition_texts        # 초안 생성 → data/staged/{최신}/
    python draft_tables.py draft all                    # 전체
    python draft_tables.py eval condition_texts gold.csv   # 사람이 만든 정답(골드셋)과 비교

흐름: data/watched 원문 → 청크 분할 → llm_client.extract_rows() → 검증(인용·분류번호·R코드·처리코드)
      → 통과 행 {표}.csv / 탈락 행 {표}_rejected.csv / 보고서 draft_report_{표}.md
- 바뀌지 않은 청크는 data/llm_cache 의 이전 결과를 재사용 → 개정 때는 바뀐 청크만 다시 호출.
- 결과는 staged 까지만. 사람이 검토 후 build_tables.py diff / promote.
- 모델 변경: 환경변수 LLM_MODEL_<표이름> (예: $env:LLM_MODEL_CONDITION_TEXTS="haiku")
"""
import os, csv, json, re, sys, hashlib, datetime
from pathlib import Path
import llm_client

BASE = Path(__file__).resolve().parent
# 경로: 코드는 src/<폴더>/, 데이터는 <프로젝트>/data/ (환경변수 WASTE_DATA_DIR 로 바꿀 수 있음)
def _data_root():
    """데이터 루트(<프로젝트>/data). WASTE_DATA_DIR가 판정표 폴더(data/tables/active)를 가리켜도 루트로 올린다."""
    env = os.environ.get("WASTE_DATA_DIR")
    if not env:
        return Path(__file__).resolve().parents[2] / "data"
    p = Path(env)
    if p.name in ("active", "staged", "old") and p.parent.name == "tables":
        return p.parent.parent
    return p


DATA_ROOT = _data_root()
LAW_DIR, TABLES = DATA_ROOT / "law", DATA_ROOT / "tables"   # 법령 원문·발췌 / 공용 판정표(staged·active·old)
WATCHED, CACHE = LAW_DIR / "watched", LAW_DIR / "llm_cache"
STAGED, ACTIVE = TABLES / "staged", TABLES / "active"
FEWSHOT = BASE / "fewshot"
PROMPT_VERSION = "v4"  # 프롬프트·스키마를 바꾸면 올린다 → 캐시 무효화
MAX_CHARS = 6000

# ───────────────────────── 공통 지시 ─────────────────────────
COMMON = """당신은 한국 폐기물 법령 원문을 판정용 표로 옮기는 작업자다.
반드시 지킬 것:
1. 원문에 있는 내용만 행으로 만든다. 추측·일반 상식·다른 법령 지식으로 채우지 않는다.
2. 근거_인용은 원문에서 연속된 구간을 그대로 복사한다(200자 이내, 글자 하나도 바꾸지 않음). 검증 코드가 원문과 대조해 다르면 그 행을 버린다.
3. 분류번호·R코드·처리방법 코드는 원문에 적혀 있거나, 아래 참조 목록의 명칭과 원문 표현이 명백히 같을 때만 넣는다. 애매하면 빈 배열로 두고 confidence 를 low 로 한다.
4. 판단이 필요한 조건(예: "~인 경우에 한정")은 조건 칸에 원문 표현을 살려 적는다.
5. 이 청크에 해당 표로 옮길 내용이 없으면 rows 를 빈 배열로 제출한다.
6. 수치 기준(무게, 면적, 농도, 기간)은 단위까지 원문 그대로 수치_기준 칸에 적는다.
"""

S = lambda **p: {"type": "object", "properties": p, "required": list(p)}
STR, ARR = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
EN = lambda *v: {"type": "string", "enum": list(v)}
CONF = EN("high", "medium", "low")

TASKS = {
    "condition_texts": {
        "model": "sonnet", "refs": ["codes"],
        "sources": [("WMA", "17/항1/호1"), ("WMA", "17/항1/호3"), ("WMA", "17/항5"), ("WMA", "18/항3"),
                    ("ALLBARO_NOTICE", "11"),
                    ("GUIDE_DECL", "§Ⅵ"), ("GUIDE_DECL", "§Ⅶ.2"), ("GUIDE_DECL", "§Ⅷ.1")],
        "task": """표: condition_texts — 경로 안내에 붙일 의무·조건 문구.
배출자(사용자 회사)가 지켜야 할 의무, 확인 대상, 서류, 기한, 물량 기준, 예외(작성 제외 대상 등)를 한 행씩 만든다.
안내문구는 사용자에게 보여 줄 존댓말 1~2문장. 관문: A(금지·제한), 1(지정 순환자원), 2(재활용), 3(인정), 4(처분), 공통.""",
        "schema": S(condition_id=STR, 관문=EN("A", "1", "2", "3", "4", "공통"), 트리거=STR, 적용_분류번호=ARR,
                    적용_지정폐기물=EN("Y", "N", "무관"), 수치_기준=STR, 안내문구=STR, 근거_인용=STR, confidence=CONF),
        "eval_fields": ["관문", "적용_지정폐기물", "수치_기준"],
    },
    "gate3_rules": {
        "model": "sonnet", "refs": [],
        "sources": [("CEA", "21/항1"), ("CEA", "23/항1"), ("CEA_DEC", "11"), ("CEA_RULE", "12/항1"),
                    ("CEA_RULE", "12/항2"), ("CEA_RECOG", "*", "제3조(")],  # 인정 고시는 신청인 자격(제3조)만
        "task": """표: gate3_rules — 관문 3(순환자원 인정) 후보를 거르는 규칙.
인정 기준·고려 사항·지정 요건·신청 자격을 한 행씩. 절차(접수·검토 기간 등)는 구분=절차 로 짧게.
불충족시: EXCLUDE(후보에서 제외), NEEDS_INFO(사용자에게 물어야 판단), NOTE(안내만).
판별_입력은 판정에 필요한 값의 이름(snake_case 영문, 예: intended_use, is_designated_waste, paid_trade).""",
        "schema": S(rule_id=STR, 구분=EN("인정기준", "고려사항", "지정요건", "신청자격", "절차"), 조건=STR, 판별_입력=STR,
                    불충족시=EN("EXCLUDE", "NEEDS_INFO", "NOTE"), 질문=STR, 근거_인용=STR, confidence=CONF),
        "eval_fields": ["구분", "불충족시"],
    },
    "reporter_scope": {
        "model": "sonnet", "refs": ["codes", "rcodes"],
        "sources": [("WMA", "46/항1"), ("WMA", "46/항5"), ("WMA_RULE", "66/항3"), ("WMA_RULE", "66/항4"),
                    ("WMA_RULE", "66/항5"), ("WMA_RULE", "66/항6"), ("WMA_RULE", "B16")],
        "task": """표: reporter_scope — 폐기물처리 신고자(허가 대신 신고로 재활용·수집운반 가능한 자)의 범위.
대상 폐기물, 허용 재활용 유형(R코드), 사업장 규모 기준, 시설·장비 기준, 스스로 수집·운반 가능 여부를 한 행씩.""",
        "schema": S(scope_id=STR, 구분=EN("수집운반_신고", "재활용_신고", "규모기준", "시설기준", "자가운반"),
                    대상_폐기물=STR, 적용_분류번호=ARR, 허용_R코드=ARR, 조건=STR, 수치_기준=STR, 근거_인용=STR, confidence=CONF),
        "eval_fields": ["구분", "허용_R코드", "수치_기준"],
    },
    "designated_check_targets": {
        "model": "sonnet", "refs": ["codes"],
        "sources": [("WMA_RULE", "17의2"), ("GUIDE_DECL", "§Ⅵ")],
        "task": """표: designated_check_targets — 배출자가 지정폐기물 해당 여부를 미리 확인(분석)해야 하는 대상·시기.
구분=확인대상: 원문의 확인대상 폐기물 목록을 품목 묶음 하나(원문 ○ 한 줄)마다 한 행. 대상 칸에 원문 품목명을 그대로.
  적용_분류번호: 이 표는 "지금은 사업장일반폐기물로 분류했지만 지정폐기물일 수 있는 것"을 찾는 데 쓴다.
  그래서 명칭이 같은 사업장일반 분류(51-xx, 예: 51-01 유기성오니류)와 지정폐기물 분류(01~12, 예: 01-02 오니류)를
  둘 다 넣는다. 참조 목록에 같은 명칭이 있을 때만, 대분류·중분류 앞자리로 (예: "51-11", "03-08").
  기름성분처럼 여러 분류에 걸치는 성분 기준은 빈 배열, 확인_항목에 성분 이름.
  원문 ○ 한 줄에 품목이 여럿이면 한 행에 모두 담는다(대상 칸에 원문 그대로).
구분=유해물질: 확인해야 하는 유해물질의 정의(별표 1 유해물질, 기름성분, 석면, PCB 등)를 한 행.
구분=확인시기: 미리 확인해야 하는 경우(신고·변경신고, 원료·공정 변경, 처리 대상 변경 등)를 경우마다 한 행.
확인_방법은 원문에 분석·시험·서류가 적혀 있을 때만, 없으면 빈 문자열.
원문의 다른 장(Ⅵ의 2. 이하: 처리계획, 위탁 등)은 행으로 만들지 않는다.
확인시기는 시행규칙(제17조의2 제2항) 원문에서만 만든다. 지침에 같은 내용이 있으면 행을 만들지 않는다.""",
        "schema": S(rule_id=STR, 구분=EN("확인대상", "유해물질", "확인시기"), 대상=STR, 적용_분류번호=ARR,
                    확인_항목=STR, 확인_방법=STR, 근거_인용=STR, confidence=CONF),
        "eval_fields": ["구분", "적용_분류번호"],
    },
    "disposal_methods_5": {
        "model": "opus", "refs": ["codes", "tcodes"],
        "sources": [("WMA_RULE", "B5:3", "처리의 경우"), ("WMA_RULE", "B5:4", "처리의 경우")],  # 3호·4호 중 "처리의 경우" 목만
        "task": """표: disposal_methods_5 — 관문 4(처분) 분류번호별 처리 기준.
각 폐기물에 대해 허용·의무·금지되는 처리 방법과 조건(보관 기간, 1일 평균 배출량 등)을 한 행씩.
처리방법_코드는 참조 목록의 4자리 코드(위탁 2xxx 기준)로, 원문 방법과 명백히 같을 때만.
action: ALLOW(가능), REQUIRE(그 방법으로 해야 함), FORBID(금지), NEEDS_INFO(조건 확인 필요).""",
        "schema": S(rule_id=STR, 대상_폐기물=STR, 적용_분류번호=ARR, 처리방법_코드=ARR, 처리방법_원문=STR, 조건=STR,
                    수치_기준=STR, action=EN("ALLOW", "REQUIRE", "FORBID", "NEEDS_INFO"), 근거_인용=STR, confidence=CONF),
        "eval_fields": ["action", "처리방법_코드"],
    },
}


# ───────────────────────── 원문·참조 ─────────────────────────
def load_chunks(law_id, spec):
    p = WATCHED / f"{law_id}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["spec"] == spec or (spec.endswith(":") and r["spec"].startswith(spec)):
                out.append(r)
    return out


def split_text(text, limit=MAX_CHARS):
    """긴 별표는 최상위 호(1. 2. …) → 목(가. 나. …) → 줄 단위 순서로 나눈다."""
    if len(text) <= limit:
        return [text]
    for pat in (r"^\s{0,4}\d+\.\s", r"^\s{0,8}[가-하]\.\s"):
        lines, parts, cur = text.splitlines(), [], []
        for ln in lines:
            if re.match(pat, ln) and cur:
                parts.append("\n".join(cur)); cur = []
            cur.append(ln)
        parts.append("\n".join(cur))
        if len(parts) > 1:
            res = []
            for p_ in parts:
                res += split_text(p_, limit) if len(p_) > limit else [p_]
            return res
    lines, res, cur = text.splitlines(), [], ""
    for ln in lines:
        if len(cur) + len(ln) > limit and cur:
            res.append(cur); cur = ""
        cur += ln + "\n"
    return res + [cur]


def section(text, kw):
    """긴 원문에서 필요한 구간만: '제3조(' 같은 조문이면 다음 '제N조(' 앞까지,
    '처리의 경우' 같은 목 제목이면 같은 깊이의 다음 목(가. 나. …) 앞까지."""
    if kw.startswith("제") and kw.endswith("("):
        i = text.find(kw)
        if i < 0:
            return None
        m = re.search(r"제\d+조(?:의\d+)?\(", text[i + len(kw):])
        return text[i:i + len(kw) + m.start()] if m else text[i:]
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)([가-하])\.\s", ln)
        if m and kw in ln:
            ind, out = len(m.group(1)), [ln]
            for nx in lines[i + 1:]:
                m2 = re.match(r"^(\s*)[가-하]\.\s", nx)
                if m2 and len(m2.group(1)) == ind:  # 같은 깊이의 다음 목 (줄바꿈으로 생긴 "  다." 는 들여쓰기가 달라 무시)
                    break
                if re.match(r"^\s{0,4}\d+\.\s", nx):
                    break
                out.append(nx)
            return "\n".join(out)
    return None


def ref_dir():
    if ACTIVE.exists():
        return ACTIVE
    st = sorted(p for p in STAGED.iterdir() if p.is_dir()) if STAGED.exists() else []
    if not st:
        sys.exit("참조 표가 없습니다. 먼저 build_tables.py build 를 실행하세요.")
    return st[-1]


def read_csv(p):
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def references(kinds):
    d = ref_dir()
    ref, text = {}, []
    if "codes" in kinds:
        rows = read_csv(d / "waste_codes_4.csv")
        ref["codes"] = {r["분류번호"] for r in rows}
        text.append("<분류번호 목록 (시행규칙 별표 4)>\n" + "\n".join(f'{r["분류번호"]} {r["명칭"][:40]}' for r in rows) + "\n</분류번호 목록>")
    if "rcodes" in kinds:
        rows = read_csv(d / "rcode_detail_4_2.csv")
        ref["rcodes"] = {r["r_code"] for r in rows}
        text.append("<R코드 목록 (시행규칙 별표 4의2)>\n" + "\n".join(f'{r["r_code"]} {r["설명"][:60]}' for r in rows) + "\n</R코드 목록>")
    if "tcodes" in kinds:
        rows = read_csv(d / "treatment_code_map.csv")
        ref["tcodes"] = {r["code"] for r in rows}
        text.append("<처리방법 코드 (별지 제5호서식)>\n" + "\n".join(f'{r["code"]} {r["처리구분"]} {r["처리분류"]} {r["세분류"]}' for r in rows) + "\n</처리방법 코드>")
    return ref, "\n\n".join(text)


def load_fewshot(table):
    p = FEWSHOT / f"{table}.jsonl"
    if not p.exists():
        return []
    return [(e["source"], e["rows"]) for e in map(json.loads, p.read_text(encoding="utf-8").splitlines()) if e]


# ───────────────────────── 검증 ─────────────────────────
NZ = lambda t: re.sub(r"[\s│┃├┤┼─┬┴┌┐└┘]+", "", (t or "").replace("ㆍ", "·").replace("‧", "·"))


def validate(row, chunk_text, ref):
    errs = []
    q = NZ(row.get("근거_인용", ""))
    if len(q) < 6:
        errs.append("근거_인용 너무 짧음")
    elif q not in NZ(chunk_text):
        errs.append("근거_인용이 원문에 없음")
    for k in ("적용_분류번호",):
        for c in row.get(k, []) or []:
            if "codes" in ref and c not in ref["codes"] and not any(x.startswith(c + "-") for x in ref["codes"]):
                errs.append(f"없는 분류번호 {c}")
    for c in row.get("허용_R코드", []) or []:
        if "rcodes" in ref and c not in ref["rcodes"]:
            errs.append(f"없는 R코드 {c}")
    for c in row.get("처리방법_코드", []) or []:
        if "tcodes" in ref and c not in ref["tcodes"]:
            errs.append(f"없는 처리코드 {c}")
    return errs


def flat(row):
    return {k: ";".join(v) if isinstance(v, list) else v for k, v in row.items()}


# ───────────────────────── 실행 ─────────────────────────
def mock_rows(table):
    """LLM_MOCK=1 테스트용: 원문 첫 문장을 인용한 행 1개 + 일부러 틀린 행 1개."""
    schema = TASKS[table]["schema"]["properties"]

    def fn(user):
        src = user.split("<source>")[-1].split("</source>")[0]
        sent = next((ln.strip() for ln in src.splitlines() if len(ln.strip()) > 20), src[:40]).strip()[:80]
        good = {k: ([] if v.get("type") == "array" else (v.get("enum", [""])[0])) for k, v in schema.items()}
        good.update(근거_인용=sent, confidence="low")
        bad = dict(good, 근거_인용="원문에 없는 지어낸 문장입니다 테스트")
        return [good, bad]
    return fn


def cmd_draft(table):
    spec = TASKS[table]
    model = llm_client.resolve_model(table, spec["model"])
    ref, ref_text = references(spec["refs"])
    shots = load_fewshot(table)
    alltext = NZ("".join(json.loads(l)["text"] for f in WATCHED.glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines() if l.strip()))
    stale = [r["근거_인용"][:30] for _, rows in shots for r in rows if NZ(r["근거_인용"]) not in alltext]
    if stale:
        print(f"⚠️ 퓨샷 인용이 현행 원문에 없음 (개정됐을 수 있음 → fewshot/{table}.jsonl 수정): {stale}")
    system = COMMON + "\n" + spec["task"] + ("\n\n" + ref_text if ref_text else "")
    st = sorted(p for p in STAGED.iterdir() if p.is_dir()) if STAGED.exists() else []
    out_dir = st[-1] if st else STAGED / f"draft_{datetime.date.today():%Y%m%d}"  # 최신 staged 에 같이 넣음
    out_dir.mkdir(parents=True, exist_ok=True)
    cdir = CACHE / table
    cdir.mkdir(parents=True, exist_ok=True)

    good, bad, log, stats = [], [], [], {"chunks": 0, "cached": 0, "api": 0, "in": 0, "out": 0, "cache_read": 0}
    missing = []
    for src in spec["sources"]:
        law_id, sp, kw = (list(src) + [None])[:3]
        chunks = load_chunks(law_id, sp)
        if not chunks:
            missing.append(f"{law_id}:{sp}")
        for ch in chunks:
            body = section(ch["text"], kw) if kw else ch["text"]
            if body is None:
                missing.append(f"{law_id}:{sp} 안에서 '{kw}' 구간을 못 찾음 → 전체 사용")
                body = ch["text"]
            parts = split_text(body)
            for i, part in enumerate(parts, 1):
                stats["chunks"] += 1
                key = ch["chunk_key"] + (f"#p{i}" if len(parts) > 1 else "")
                user = (f"<chunk_key>{key}</chunk_key>\n<title>{ch.get('title','')}</title>\n"
                        f"<source>\n{part}\n</source>\n위 원문에서 표 행을 뽑아 submit_rows 로 제출하라.")
                h = hashlib.sha1(f"{PROMPT_VERSION}|{model}|{system}|{json.dumps(shots, ensure_ascii=False)}|{user}".encode()).hexdigest()[:16]
                cp = cdir / f"{h}.json"
                if cp.exists():
                    rows, meta = json.loads(cp.read_text(encoding="utf-8")), {"model": model, "cached": True}
                    stats["cached"] += 1
                else:
                    rows, meta = llm_client.extract_rows(model=model, system=system, schema=spec["schema"],
                                                         user=user, fewshot=shots, mock_fn=mock_rows(table))
                    cp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                    stats["api"] += 1
                    stats["in"] += meta.get("input_tokens", 0); stats["out"] += meta.get("output_tokens", 0)
                    stats["cache_read"] += meta.get("cache_read", 0)
                log.append({"chunk_key": key, "meta": meta, "rows": rows})
                for r in rows:
                    errs = validate(r, part, ref)
                    rec = {**flat(r), "근거_청크": f'{key}#{ch.get("hash","")}', "모델": model, "status": "draft"}
                    (bad if errs else good).append({**rec, "탈락_사유": "; ".join(errs)} if errs else rec)

    def w(path, rows):
        if not rows:
            path.unlink(missing_ok=True); return
        cols = []
        for r in rows:
            cols += [k for k in r if k not in cols]
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader(); wr.writerows(rows)

    w(out_dir / f"{table}.csv", good)
    w(out_dir / f"{table}_rejected.csv", bad)
    with (out_dir / f"llm_log_{table}.jsonl").open("w", encoding="utf-8") as f:
        for x in log:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    low = sum(1 for r in good if r.get("confidence") == "low")
    rep = [f"# LLM 초안 보고서 — {table}", f"생성: {datetime.datetime.now():%Y-%m-%d %H:%M}  모델: {model}  프롬프트: {PROMPT_VERSION}  퓨샷: {len(shots)}개", "",
           f"| 항목 | 값 |", "|---|---|",
           f"| 청크 | {stats['chunks']} (캐시 재사용 {stats['cached']}, API 호출 {stats['api']}) |",
           f"| 토큰 | 입력 {stats['in']:,} (캐시 읽기 {stats['cache_read']:,}) / 출력 {stats['out']:,} |",
           f"| 통과 행 | {len(good)} (confidence low {low}) |",
           f"| 탈락 행 | {len(bad)} → {table}_rejected.csv |"]
    if missing:
        rep += ["", "## 원문 없음 (law_watch extract 확인)"] + [f"- {m}" for m in missing]
    if bad:
        rep += ["", "## 탈락 사유"] + [f"- {r['근거_청크'].split('#')[0]}: {r['탈락_사유']}" for r in bad[:30]]
    rep += ["", "## 사람이 볼 것", "- confidence low 행, 탈락 행", "- 조건 칸이 원문 뜻과 같은지", "- 승인한 행만 남기고 build_tables.py diff → promote"]
    (out_dir / f"draft_report_{table}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep)); print(f"\n→ {out_dir}")


def cmd_eval(table, gold_path):
    """골드셋(사람이 확인한 행)과 초안 비교: 인용이 겹치는 행끼리 짝지어 재현율·정밀도·칸 일치율."""
    out_dir = sorted(p for p in STAGED.iterdir() if p.is_dir())[-1]
    draft, gold = read_csv(out_dir / f"{table}.csv"), read_csv(Path(gold_path))
    fields = TASKS[table]["eval_fields"]
    used, hit, same = set(), 0, {f: 0 for f in fields}
    for g in gold:
        gq = NZ(g.get("근거_인용", ""))
        for i, d in enumerate(draft):
            dq = NZ(d.get("근거_인용", ""))
            if i not in used and gq and dq and (gq in dq or dq in gq):
                used.add(i); hit += 1
                for f in fields:
                    same[f] += NZ(g.get(f, "")) == NZ(d.get(f, ""))
                break
    rec, prec = hit / max(len(gold), 1), hit / max(len(draft), 1)
    print(f"[{table}] 골드 {len(gold)}행 / 초안 {len(draft)}행 / 짝 {hit}")
    print(f"재현율 {rec:.0%}  정밀도 {prec:.0%}")
    for f in fields:
        print(f"  {f} 일치 {same[f]}/{hit}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "list":
        for t, s in TASKS.items():
            print(f"{t:20} 모델={llm_client.resolve_model(t, s['model']):18} 원문={', '.join(':'.join(x for x in src if x) for src in s['sources'])}")
    elif a[0] == "draft" and len(a) > 1:
        for t in (TASKS if a[1] == "all" else [a[1]]):
            cmd_draft(t)
    elif a[0] == "eval" and len(a) > 2:
        cmd_eval(a[1], a[2])
    else:
        print(__doc__)
