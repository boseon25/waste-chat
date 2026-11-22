"""원문 JSON → 조문·별표·행정규칙 청크

해시(hash)는 예전 law_watch·law_chunk와 같은 방식 그대로다. 바꾸면 기준선(registry.json)과 비교가 깨진다.
counts(항·호 개수)는 조문 청크에만 붙이고 해시 계산에는 넣지 않는다.
"""
import hashlib, re
from typing import Any

# ── 정규화: 명칭만 바뀐 개정을 걸러내기 위함 ─────────────────────────
NORMALIZE = [
    (r"기후에너지환경부", "환경부"),
    (r"<(개정|신설|삭제|본조신설|전문개정|제목개정|종전)[^>]*>", ""),
    (r"\[(본조신설|전문개정|제목개정|종전)[^\]]*\]", ""),
    (r"\s+", ""),
]


def normalize(text: str) -> str:
    for pat, rep in NORMALIZE:
        text = re.sub(pat, rep, text)
    return text


def h(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


hash_text = h  # 예전 law_chunk 이름


# ── 응답 구조를 모를 때 쓰는 탐색 도우미 ─────────────────────────────
def walk(o: Any):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def text_of(o: Any) -> str:
    if isinstance(o, str):
        return o
    if isinstance(o, dict):
        return "\n".join(text_of(v) for v in o.values())
    if isinstance(o, list):
        return "\n".join(text_of(v) for v in o)
    return ""


# ── 번호·키 ─────────────────────────────────────────────────────────
CIRCLED = {c: str(i) for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", 1)}
HANGUL_MARK = "가나다라마바사아자차카타파하"


def norm_no(v) -> str:
    v = str(v or "").strip()
    v = "".join(CIRCLED.get(ch, ch) for ch in v)
    return re.sub(r"[.\s()<>]", "", v)  # <, > 도 지움 ("항<16>" 같은 키 방지)


def art_key(d) -> str:
    no = str(d.get("조문번호", "")).strip()
    br = str(d.get("조문가지번호", "") or "").strip()
    return f"{no}의{br}" if br and br != "0" else no


def annex_key(d) -> str:
    no = str(d.get("별표번호", "")).lstrip("0") or "0"
    br = str(d.get("별표가지번호", "") or "").lstrip("0")
    kind = str(d.get("별표구분", ""))
    k = f"{no}의{br}" if br else no
    return f"F{k}" if "서식" in kind else k


def move_hash(c) -> str:
    """번호 이동 찾기용 해시: 맨 앞의 자기 번호("제16조의12", "[별표 5의3]")를 빼고 계산.
    보통 해시(hash)는 자기 번호까지 포함해서, 번호만 바뀌어도 값이 달라진다. 저장하지 않고 그때그때 계산."""
    t = c.get("text") or ""
    if c.get("type") == "article":
        t = re.sub(r"^\s*제\d+조(?:의\d+)?", "", t, count=1)
    elif c.get("type") == "annex":
        t = re.sub(r"\[별[표지][^\]]*\]", "", t)
    return h(t)


# ── 조문 ─────────────────────────────────────────────────────────────
def article_text(d, k):
    """조문 본문만 순서대로 (조문내용 → 항 → 호 → 목) + 항·호 하위 청크"""
    head = text_of(d.get("조문내용", "")).strip()
    title = d.get("조문제목", "")
    lines, subs = [head] if head else [], []
    for hg in as_list(d.get("항")):
        hn = norm_no(hg.get("항번호"))
        h_txt = text_of(hg.get("항내용", "")).strip()
        hang_lines = [h_txt] if h_txt else []
        for ho in as_list(hg.get("호")):
            no = norm_no(ho.get("호번호"))
            ho_lines = [text_of(ho.get("호내용", "")).strip()]
            for mk in as_list(ho.get("목")):
                ho_lines.append(text_of(mk.get("목내용", "")).strip())
                for sub in as_list(mk.get("세목")):
                    ho_lines.append(text_of(sub.get("세목내용", "")).strip())
            ho_txt = "\n".join(x for x in ho_lines if x)
            hang_lines.append(ho_txt)
            key = f"{k}/항{hn}/호{no}" if hn else f"{k}/호{no}"
            ctx = "\n".join(x for x in [head.split("\n")[0] if head else "", h_txt] if x)  # 조문 제목 줄 + 항 첫 문장
            subs.append({"type": "article_part", "key": key, "title": title, "parent": k,
                         "text": (ctx + "\n" + ho_txt).strip(), "hash": h(ho_txt)})
        hang_txt = "\n".join(x for x in hang_lines if x)
        lines.append(hang_txt)
        if hn:
            subs.append({"type": "article_part", "key": f"{k}/항{hn}", "title": title, "parent": k,
                         "text": (head.split("\n")[0] + "\n" + hang_txt).strip() if head else hang_txt,
                         "hash": h(hang_txt)})
    return "\n".join(x for x in lines if x), subs


def article_counts(data) -> dict:
    """원문 기준 항·호 개수 (가져온 조각이 빠짐없는지, 호가 늘거나 줄었는지 비교용). 해시에는 안 넣음.
    항번호 없이 바로 호가 오는 조문(예: 제2조)은 호 개수를 "-" 키에 넣는다."""
    hangs = as_list(data.get("항"))
    return {"항": len([x for x in hangs if norm_no(x.get("항번호"))]),
            "호": {norm_no(x.get("항번호")) or "-": len(as_list(x.get("호"))) for x in hangs if as_list(x.get("호"))}}


# ── 행정규칙 ─────────────────────────────────────────────────────────
ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"
ROMAN_SEQ = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ", "Ⅹ", "ⅩⅠ", "ⅩⅡ", "ⅩⅢ", "ⅩⅣ", "ⅩⅤ"]
SKIP_KEYS = ("부칙", "제개정이유", "개정문", "첨부", "파일", "링크", "담당", "연락", "전화", "소관", "부서")
RX_ARTICLE = re.compile(r"(?:^|(?<=[\s　]))(제\d+조(?:의\d+)?)\s*\(")
RX_ROMAN = re.compile(rf"(?<!\()([{ROMAN}]+)\.\s*")
RX_NUM = re.compile(r"(?<![\d(])(\d{1,2})\.\s")


def admrul_body(body) -> str:
    """행정규칙 응답에서 본문만: '조문내용' 칸 → 없으면 '…내용' 칸(부칙·개정이유 제외) → 없으면 가장 긴 문자열"""
    found = []
    for d in walk(body):
        if "조문내용" in d:
            v = d["조문내용"]
            found += [text_of(x) for x in v] if isinstance(v, list) else [text_of(v)]
    if found:
        return "\n".join(x for x in found if x and x.strip())
    parts = []
    for d in walk(body):
        for k, v in d.items():
            if k.endswith("내용") and not any(sk in k for sk in SKIP_KEYS) and isinstance(v, (str, list)):
                parts.append(text_of(v))
    if parts:
        return "\n".join(parts)
    strings = [v for d in walk(body) for v in d.values() if isinstance(v, str)]
    return max(strings, key=len) if strings else ""


def split_at(text, rx):
    """정규식이 맞는 위치마다 잘라 (표지, 조각) 목록으로"""
    ms = list(rx.finditer(text))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.append((m.group(1), text[m.start():end].strip()))
    return out


def norm_roman(r):
    return r.replace("Ⅺ", "ⅩⅠ").replace("Ⅻ", "ⅩⅡ")


def split_seq(text, rx, seq_of):
    """번호가 순서대로(다음 순번)일 때만 자른다 → 본문 속 참조 문구는 건너뜀"""
    keep, i = [], 0
    for m in rx.finditer(text):
        if i < len(seq_of) and seq_of[i] == m.group(1):
            keep.append(m)
            i += 1
    out = []
    for j, m in enumerate(keep):
        end = keep[j + 1].start() if j + 1 < len(keep) else len(text)
        out.append((m.group(1), text[m.start():end].strip()))
    return out


def admrul_chunks(t, body):
    txt = admrul_body(body)
    out = [{"type": "whole", "key": "전체", "title": t["name"], "hash": h(txt), "text": txt}]
    arts = split_at(txt, RX_ARTICLE)
    if arts:  # 조문형 고시: 제N조 단위
        for mark, seg in arts:
            k = mark[1:].replace("조", "", 1)  # "제11조의2" → "11의2"
            tm = re.match(r"제\d+조(?:의\d+)?\s*\(([^)]*)\)", seg)
            out.append({"type": "article", "key": k, "title": tm.group(1) if tm else "", "hash": h(seg), "text": seg})
        return out
    txt_r = norm_roman(txt)
    for mark, seg in split_seq(txt_r, RX_ROMAN, ROMAN_SEQ):  # 목차형 지침: Ⅰ. Ⅱ. … 장 → 1. 2. 절
        subs = split_seq(seg, RX_NUM, [str(n) for n in range(1, 31)])
        first = subs and seg.find(subs[0][1][:10])
        sec_title = (seg[:first] if subs else seg.split("\n")[0]).strip()[:60]
        out.append({"type": "doc_part", "key": f"§{mark}", "title": sec_title, "parent": "전체", "hash": h(seg), "text": seg})
        for num, sub in subs:
            out.append({"type": "doc_part", "key": f"§{mark}.{num}", "title": sub[:40], "parent": "전체",
                        "hash": h(sub), "text": f"{sec_title}\n{sub}"})
    return out


# ── 전체 ─────────────────────────────────────────────────────────────
def chunks_of(t, meta, body):
    out = []
    arts = [d for d in walk(body) if "조문번호" in d and ("조문내용" in d or "항" in d)]
    for d in arts:
        if str(d.get("조문여부", "조문")) not in ("조문", ""):
            continue  # 장·절 제목 등 제외
        k = art_key(d)
        txt, subs = article_text(d, k)
        out.append({"type": "article", "key": k, "title": d.get("조문제목", ""), "hash": h(txt), "text": txt,
                    "counts": article_counts(d)})
        out += subs
    for d in [d for d in walk(body) if "별표번호" in d or "별표제목" in d]:
        k = annex_key(d)
        body_txt = d.get("별표내용")
        txt = text_of(body_txt) if body_txt else text_of({kk: vv for kk, vv in d.items() if "링크" not in kk and "파일명" not in kk})
        cur, buf = None, []

        def flush():
            if cur and buf:
                out.append({"type": "annex_part", "key": f"B{k}:{cur}", "title": d.get("별표제목", ""), "parent": f"B{k}",
                            "text": "\n".join(buf).strip(), "hash": h("\n".join(buf))})
        for line in txt.splitlines():
            m = re.match(r"^\s*(\d+)\.(?!\d)", line)
            if m:
                flush()
                cur, buf = f"{m.group(1)}.", []
            if cur:
                buf.append(line)
        flush()
        out.append({"type": "annex", "key": k, "title": d.get("별표제목", ""), "hash": h(txt), "text": txt,
                    "annex_effective": d.get("별표시행일자", ""),
                    "hwp": d.get("별표서식파일링크", ""), "pdf": d.get("별표서식PDF파일링크", "")})
    if not any(c["type"] == "article" for c in out) and t["kind"] == "admrul":
        out += admrul_chunks(t, body)  # 행정규칙: 본문만 꺼내 문서 목차대로 자른다
    if t["kind"] != "admrul" and not out:
        txt = text_of(body)
        out.append({"type": "whole", "key": "전체", "title": t["name"], "hash": h(txt), "text": txt})
    seen = {}
    for c in out:  # 같은 키가 두 번 나오면 _2, _3 … 붙임 (덮어쓰기 방지)
        base = ("B" if c["type"] == "annex" else "") + c["key"]
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            c["key"] = f"{c['key']}_{seen[base]}"
    for c in out:
        c.update(law_id=t["law_id"], law_name=t["name"], grade=t["grade"], kind=t["kind"],
                 effective=meta.get("effective", ""), promulgated=meta.get("promulgated", ""),
                 law_serial=meta.get("serial", ""))
        c["chunk_key"] = f"{t['law_id']}_{'B' if c['type'] == 'annex' else ''}{c['key']}_{meta.get('effective', '')}"
    return out