"""법 원문 조회 도구 — 조회만 하고 판정하지 않는다.

  get_store()                       WASTE_LAW_DIR 로 LawStore 를 한 번만 로드
  find_by_title(law, no, title)     체크리스트 최소 조문 찾기 (번호 → 안 맞으면 제목으로, 번호 이동 대비)
  full_article(law, key)            조문·별표 통째 + 호 개수 검증 (lawstore.articles.article 그대로)
  excerpt_rows(law, key, codes)     긴 별표에서 분류번호·R코드 행만 (요약 아님, 원문 행 그대로)
  search(query, laws, k)            bigram TF-IDF 검색 (별지 서식 제외)
  citations(text, from_law)         원문 속 인용("법 제13조의2", "시행규칙 별표 4의3") → [(law, key)]
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from law_agent.settings import env
from law_agent.state import make_ref, norm_text
from law_agent.core.lawstore import LAW_NAME, LawStore, box_table_rows, top_key

_STORE: LawStore | None = None


# ── 저장소 ─────────────────────────────────────────────────────

def get_store(chunks_dir: str | Path | None = None) -> LawStore:
    """LawStore 를 한 번만 로드. chunks_dir 를 안 주면 settings.data.law_dir_env(WASTE_LAW_DIR)."""
    global _STORE
    if _STORE is None:
        _STORE = LawStore.load(chunks_dir or env("law_dir_env"))
    return _STORE


def set_store(store: LawStore | None) -> None:
    """테스트·재로드용. None 이면 다음 get_store() 때 다시 로드."""
    global _STORE
    _STORE = store


def _store(store: LawStore | None) -> LawStore:
    return store if store is not None else get_store()


def chunk_ref(law: str, key: str, store: LawStore | None = None) -> str | None:
    """조각 하나의 ref ("{law}:{key}@{시행일}#{해시}"). 없으면 None."""
    c = _store(store).get(law, key)
    return make_ref(c.law, c.key, c.effective, c.hash) if c else None


# ── 최소 조문 찾기 (번호 이동 대비) ─────────────────────────────

def _nt(text: str) -> str:
    """제목 비교용: 공백·가운뎃점 제거 + 끝의 "(… 관련)" 제거."""
    t = re.sub(r"\s*[(（][^()（）]*관련[)）]\s*$", "", text or "")
    return norm_text(t)


def _title_match(want: str, have: str) -> bool:
    a, b = _nt(want), _nt(have)
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _is_top(key: str) -> bool:
    return "/" not in key and ":" not in key and not key.startswith("BF")


def _kind(key: str) -> str:
    if key.startswith("B"):
        return "annex"
    if key.startswith("§"):
        return "guide"
    return "article"


def _key_candidates(no: str) -> list[str]:
    no = (no or "").strip()
    if not no:
        return []
    out = [no]
    if not no[0].isdigit() and not no.startswith(("B", "§")):
        out.append("§" + no)                       # 지침 장 번호 "Ⅵ" → "§Ⅵ"
    return out


def find_by_title(law: str, no: str, title: str, store: LawStore | None = None) -> dict | None:
    """체크리스트 최소 조문 하나 찾기.

    1. 번호(no)로 찾고 제목이 맞으면 그대로             → found_by="no",    moved=False
    2. 번호가 없거나 제목이 다르면 같은 법령에서 제목으로 → found_by="title", moved=True
       (같은 종류만: 별표는 별표끼리, 조문은 조문끼리. 후보가 둘 이상이면 고르지 않는다)
    3. 제목으로도 못 찾으면 번호로 찾은 조문을 돌려주되 title_match=False (evidence 가 problems 에 기록)
    4. 번호가 "" 이고 제목이 법령 이름이면 통째 문서("전체") → found_by="whole"
    못 찾으면 None.
    결과: {"law", "key", "title", "found_by", "moved", "title_match"}
    """
    s = _store(store)
    by_no = None
    for k in _key_candidates(no):
        c = s.get(law, k)
        if c is not None:
            by_no = c
            break
    if by_no is not None and _title_match(title, by_no.title):
        return {"law": law, "key": by_no.key, "title": by_no.title, "found_by": "no", "moved": False, "title_match": True}

    if not (no or "").strip() and (not title or _title_match(title, LAW_NAME.get(law, ""))):
        c = s.get(law, "전체")
        if c is not None:
            return {"law": law, "key": c.key, "title": c.title or LAW_NAME.get(law, ""), "found_by": "whole",
                    "moved": False, "title_match": True}

    want_kind = _kind(by_no.key) if by_no is not None else (_kind(_key_candidates(no)[0]) if no else None)
    tops = [c for (l, k), c in s.chunks.items() if l == law and _is_top(k)
            and (want_kind is None or _kind(k) == want_kind)]
    exact = [c for c in tops if _nt(c.title) == _nt(title)]
    near = exact or [c for c in tops if _title_match(title, c.title)]
    if len(near) == 1:
        c = near[0]
        return {"law": law, "key": c.key, "title": c.title, "found_by": "title",
                "moved": by_no is None or c.key != by_no.key, "title_match": True}

    if by_no is not None:
        return {"law": law, "key": by_no.key, "title": by_no.title, "found_by": "no", "moved": False, "title_match": False}
    return None


# ── 조문 통째·발췌 ─────────────────────────────────────────────

def full_article(law: str, key: str, store: LawStore | None = None) -> dict | None:
    """lawstore.articles.article 결과 그대로: {law, key, name, title, effective, ref, text, complete, problems, …}."""
    return _store(store).article(law, top_key(key))


# ── 근거 글 정리 (판단과 무관한 글자 빼기 — 법 내용은 그대로) ─────────
RX_AMEND = re.compile(r"<(?:개정|신설|전문개정|타법개정|일부개정)[^>]*>")         # <삭제 …> 는 남김 (호가 없어졌다는 뜻)
RX_NOTE = re.compile(r"\[(?:본조신설|제목개정|전문개정|종전)[^\]]*\]")
RX_BORDER_LINE = re.compile(r"^[\s─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬]*$")


def clean_law_text(text: str) -> str:
    """근거에 넣을 원문 정리: 개정 이력(<개정 …>, [본조신설 …]), 별표 머리글(■ … [별표 N] …),
    괘선만 있는 줄, 칸 경계(│ → ' | '), 겹친 공백·빈 줄. 같은 입력이면 같은 출력.
    judge 는 이 글을 보고 인용하고, verifier 도 이 글로 대조하므로 인용 검증은 그대로 된다."""
    out = []
    for ln in (text or "").split("\n"):
        if ln.lstrip().startswith("■"):
            continue
        if RX_BORDER_LINE.match(ln):
            continue
        ln = RX_NOTE.sub("", RX_AMEND.sub("", ln))
        ln = re.sub(r"[ \t　]+", " ", ln.replace("│", " | ")).strip(" |")
        if ln.strip():
            out.append(ln.rstrip())
    return "\n".join(out)


ROW_MAX_LINES = 30                  # 한 행이 이보다 길면 거기서 끊는다 (표 끝까지 읽어 버리는 것을 막음)
ROW_MAX_HITS = 3                    # 같은 코드가 여러 번 나오면 앞에서부터 이만큼
RX_ANY_CODE = re.compile(r"(?<![0-9A-Za-z-])(\d{2}-\d{2}(?:-\d{2})?|R-\d+(?:-\d+)*)(?![0-9-])")
BORDER = "├└┌┼┬┴─┐┘┤"


# 재활용 기준 별표(5의3)처럼 'R코드 유형의 재활용 기준' 제목으로 나뉜 글.
# 제목 줄만 — "가) R-4-5 유형의 …", "1) R-5 유형의 …", 앞 기호 없는 "R-10-1 유형의 …".
# 문장 속 언급("(1) 가)에 따른 R-7-1 유형의…", "…른 R-7-1 유형의…")은 제목이 아님.
RX_RTYPE_HEAD = re.compile(r"^\s*(?:(?:[가-하]|\d{1,2})\)\s*)?(R-\d+(?:-\d+)*)\s*유형의\s*재활용\s*기준")
RX_TOP_SECTION = re.compile(r"^\s*(?:\d{1,2}\.\s|비고)")   # 큰 호("3. ")나 비고가 나오면 유형 기준 끝
RTYPE_MAX_CHARS = 4000


def rtype_blocks(text: str, codes: list[str], max_chars: int = RTYPE_MAX_CHARS) -> list[dict]:
    """codes(R코드) 의 '유형의 재활용 기준' 부분만 원문 그대로. 상위 코드는 하위 유형을 포함한다
    (R-10 → R-10-1, R-3-2 → R-3 머리글 + R-3-2). 다음 유형 제목·큰 호·비고에서 끊는다.
    결과: [{"code": 제목 코드, "text": 원문, "cut": max_chars 로 잘렸는지}]"""
    rcs = [c for c in codes if c.startswith("R-")]
    if not rcs or not text:
        return []

    def wanted(head: str) -> bool:
        return any(head == r or head.startswith(r + "-") or r.startswith(head + "-") for r in rcs)

    lines = text.split("\n")
    out: list[dict] = []
    i = 0
    while i < len(lines):
        m = RX_RTYPE_HEAD.match(lines[i])
        if not (m and wanted(m.group(1))):
            i += 1
            continue
        block, j = [lines[i]], i + 1
        while j < len(lines) and not RX_RTYPE_HEAD.match(lines[j]) and not RX_TOP_SECTION.match(lines[j]):
            block.append(lines[j])
            j += 1
        body = "\n".join(block).rstrip()
        out.append({"code": m.group(1), "text": body[:max_chars], "cut": len(body) > max_chars})
        i = j
    return out


def _code_rx(code: str) -> re.Pattern:
    """코드가 '낱말'로 있는지: 51-03 은 51-03-01 안에서 걸리지 않는다, R-3 은 R-3-2 안에서 걸리지 않는다."""
    return re.compile(rf"(?<![0-9A-Za-z-]){re.escape(code)}(?![0-9-])")


def _first_cell(line: str) -> str:
    cells = [x for x in line.split("│") if x.strip()]
    return cells[0] if cells else ""


def _first_column(line: str) -> str:
    """자리로 본 첫 칸 (비어 있을 수 있음). '│      │지류는…│R-8-1,…' 의 첫 칸은 빈 칸 → 앞 행이 이어지는 줄.
    2026.11.17: _first_cell(비어 있지 않은 첫 칸)로 보면 R코드만 있는 이어진 줄을 '다른 행'으로 잘못 보고 끊었음
    → 별표 4의3 51-03-01 행이 'R-4-4,' 에서 잘려 R-8·R-9·R-10 이 근거에서 빠짐."""
    s = line.strip()
    cells = s.split("│")
    if s.startswith("│"):
        cells = cells[1:]
    return cells[0] if cells else ""


def _render_row(lines: list[str]) -> str:
    """괘선 칸 경계(│)를 ' | ' 로, 공백 정리. 글자는 바꾸지 않는다."""
    out = []
    for ln in lines:
        t = re.sub(r"[ \t　]+", " ", ln.replace("│", " | ")).strip(" |")
        if t:
            out.append(t)
    return "\n".join(out)


def row_blocks(text: str, code: str, max_lines: int = ROW_MAX_LINES, max_hits: int = ROW_MAX_HITS) -> list[str]:
    """괘선 표에서 code 가 있는 행. 코드가 있는 줄부터 → 경계선(├ 등)·빈 줄·다른 코드로 시작하는 행 전까지 (최대 max_lines 줄)."""
    lines = (text or "").split("\n")
    rx = _code_rx(code)
    out: list[str] = []
    i = 0
    while i < len(lines) and len(out) < max_hits:
        ln = lines[i]
        if "│" in ln and rx.search(ln):
            block, j = [ln], i + 1
            while j < len(lines) and len(block) < max_lines:
                s = lines[j].strip()
                if not s or s[0] in BORDER or "│" not in s:
                    break
                other = RX_ANY_CODE.findall(_first_column(lines[j]))
                if other and code not in other:
                    break
                block.append(lines[j])
                j += 1
            out.append(_render_row(block))
            i = j
            continue
        i += 1
    return out


RX_LINE_CODE = re.compile(r"^\s*(\d{2}(?:-\d{2}){0,2})(?![0-9-])")


def code_group_lines(text: str, code: str) -> str:
    """줄 목록 별표(예: 시행규칙 별표 4 '51-03-01 폐합성수지류')에서 code 가 속한 묶음만 원문 줄 그대로.
    51-03-01 → 대분류 제목 줄(51 …), 중분류 제목 줄(51-03 …), 51-03- 로 시작하는 형제 줄 전부.
    (형제 번호를 같이 넣는 이유: 더 맞는 번호가 있는지 judge 가 비교할 수 있게. 예: 51-03-07 플라스틱폐포장재)
    줄이 다음 줄로 이어지면(번호 없이 시작하는 줄) 앞 줄에 붙여 같이 넣는다. 못 찾으면 ""."""
    parts = code.split("-")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return ""
    top, mid = parts[0], "-".join(parts[:2])
    lines = (text or "").split("\n")
    out: list[str] = []
    keep = False
    for ln in lines:
        m = RX_LINE_CODE.match(ln)
        if m:
            c = m.group(1)
            keep = c == top or c == mid or c.startswith(mid + "-")
            if keep:
                out.append(ln.rstrip())
        elif keep and ln.strip():
            out.append(ln.rstrip())                  # 번호 없는 이어진 줄
    return "\n".join(out) if any(mid in x for x in out) else ""


def excerpt_rows(law: str, key: str, codes: list[str], store: LawStore | None = None) -> dict | None:
    """긴 별표(괘선 표)에서 codes(분류번호·R코드) 행만 원문 그대로 꺼낸다. 요약하지 않는다.

    결과: {"law", "key", "title", "effective", "ref", "text", "found": [코드], "missing": [코드]}
    행을 하나도 못 찾으면 text="" — 호출하는 쪽(evidence)이 다른 방법(관련 조각)을 쓴다.
    """
    c = _store(store).get(law, key)
    if c is None:
        return None
    rows, found, missing = [], [], []
    for code in dict.fromkeys(codes):                # 순서 유지 중복 제거
        r = row_blocks(c.text, code)
        if r:
            rows.extend(r)
            found.append(code)
        else:
            missing.append(code)
    return {"law": law, "key": c.key, "title": c.title, "effective": c.effective,
            "ref": make_ref(c.law, c.key, c.effective, c.hash), "text": "\n".join(dict.fromkeys(rows)),
            "found": found, "missing": missing}


PART_REL_FLOOR = 0.5


def relevant_sections(law: str, queries: list[str], budget: int, max_parts: int = 4,
                      store: LawStore | None = None) -> list:
    """통째 문서(고시·지침 "전체")가 너무 길 때: 같은 법령의 조문·장(조각 아닌 윗단위) 중 관련도가 높은 것.
    relevant_parts 와 같은 방식 (BM25 + 순위 합치기 + 1위 대비 PART_REL_FLOOR)."""
    s = _store(store)
    tops = [c for (l, k), c in sorted(s.chunks.items())
            if l == law and k != "전체" and "/" not in k and ":" not in k and not k.startswith("BF")]
    return _pick_relevant(tops, queries, budget, max_parts, s)


def _pick_relevant(chunks: list, queries: list[str], budget: int, max_parts: int, s: LawStore) -> list:
    from law_agent.tools.bm25 import BM25Index, rrf
    if not chunks:
        return []
    idx = BM25Index.from_chunks(chunks)
    lists = []
    for qt in queries:
        hits = idx.search(qt, k=len(chunks)) if qt else []
        if hits:
            lists.append([(h["law"], h["key"]) for h in hits if h["score"] >= hits[0]["score"] * PART_REL_FLOOR])
    out, used = [], 0
    for (l, k), _score in rrf([x for x in lists if x]):
        p = s.get(l, k)
        if p is None or used + len(p.text) > budget:
            continue
        out.append(p)
        used += len(p.text)
        if len(out) >= max_parts:
            break
    return out


def relevant_parts(law: str, key: str, queries: list[str], budget: int, max_parts: int = 4,
                   store: LawStore | None = None) -> list:
    """긴 별표의 조각 중 queries 와 관련도가 높은 것 (조각 안 BM25 + 순위 합치기). 원문 조각 그대로.
    글자 합계 budget, 최대 max_parts 개. 관련도 0 이거나 그 검색어 1위의 PART_REL_FLOOR 미만인 조각은 넣지 않는다."""
    s = _store(store)
    return _pick_relevant(sorted(s.children(law, key), key=lambda p: p.key), queries, budget, max_parts, s)


PART_REL_FLOOR = 0.5


def relevant_sections(law: str, queries: list[str], budget: int, max_parts: int = 4,
                      store: LawStore | None = None) -> list:
    """통째 문서(고시·지침 "전체")가 너무 길 때: 같은 법령의 조문·장(조각 아닌 윗단위) 중 관련도가 높은 것.
    relevant_parts 와 같은 방식 (BM25 + 순위 합치기 + 1위 대비 PART_REL_FLOOR)."""
    s = _store(store)
    tops = [c for (l, k), c in sorted(s.chunks.items())
            if l == law and k != "전체" and "/" not in k and ":" not in k and not k.startswith("BF")]
    return _pick_relevant(tops, queries, budget, max_parts, s)


def _pick_relevant(chunks: list, queries: list[str], budget: int, max_parts: int, s: LawStore) -> list:
    from law_agent.tools.bm25 import BM25Index, rrf
    if not chunks:
        return []
    idx = BM25Index.from_chunks(chunks)
    lists = []
    for qt in queries:
        hits = idx.search(qt, k=len(chunks)) if qt else []
        if hits:
            lists.append([(h["law"], h["key"]) for h in hits if h["score"] >= hits[0]["score"] * PART_REL_FLOOR])
    out, used = [], 0
    for (l, k), _score in rrf([x for x in lists if x]):
        p = s.get(l, k)
        if p is None or used + len(p.text) > budget:
            continue
        out.append(p)
        used += len(p.text)
        if len(out) >= max_parts:
            break
    return out


def relevant_parts(law: str, key: str, queries: list[str], budget: int, max_parts: int = 4,
                   store: LawStore | None = None) -> list:
    """긴 별표의 조각 중 queries 와 관련도가 높은 것 (조각 안 BM25 + 순위 합치기). 원문 조각 그대로.
    글자 합계 budget, 최대 max_parts 개. 관련도 0 인 조각은 넣지 않는다."""
    from law_agent.tools.bm25 import BM25Index, rrf
    s = _store(store)
    parts = sorted(s.children(law, key), key=lambda p: p.key)
    if not parts:
        return []
    idx = BM25Index.from_chunks(parts)
    lists = []
    for qt in queries:
        hits = idx.search(qt, k=len(parts)) if qt else []
        if hits:                                     # 그 검색어 1위의 PART_REL_FLOOR 미만은 우연히 겹친 것으로 봄
            lists.append([(h["law"], h["key"]) for h in hits if h["score"] >= hits[0]["score"] * PART_REL_FLOOR])
    out, used = [], 0
    for (l, k), _score in rrf([x for x in lists if x]):
        p = s.get(l, k)
        if p is None or used + len(p.text) > budget:
            continue
        out.append(p)
        used += len(p.text)
        if len(out) >= max_parts:
            break
    return out


# ── 호·목 단위 발췌 (분류번호 행이 없는 긴 별표: 별표 5 처리 기준) ──────────────
# 2026.11.17 데모: 별표 5(7만 8천 자)에서 관련도 검색이 생활폐기물·수집운반증 조각을 골라 F-1·F-2 가 답하지 못함.
# 체크리스트가 "폐기물 구분별 호 + 목"을 정해 주면 그 부분만, 길면 그 안의 1) 2) 항목 중 사례와 관련 있는 것만.
LETTERS = "가나다라마바사아자차카타파하"
RX_LETTER_HEAD = re.compile(r"^\s*([가-하])\.\s")
RX_ITEM_HEAD = re.compile(r"^\s*(\d{1,2})\)\s")
RX_SUB_HEAD = re.compile(r"^\s*([가-하])\)\s")


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def find_section_part(law: str, key: str, section_kw: str, store: LawStore | None = None):
    """긴 별표의 조각 중 첫 줄(호 머리)에 section_kw 가 든 것. 공백 무시."""
    want = _nospace(section_kw)
    for p in sorted(_store(store).children(law, key), key=lambda p: p.key):
        first = next((ln for ln in p.text.split("\n") if ln.strip()), "")
        if want and want in _nospace(first):
            return p
    return None


def _letter_block(lines: list[str], sub_kw: str) -> list[str] | None:
    """머리 말이 sub_kw 인 목(가. 나. …)의 줄. 끝 = 다음 순서 글자의 목 (본문 속 '다. 이 경우' 같은 줄에 속지 않게)."""
    want = _nospace(sub_kw)
    for i, ln in enumerate(lines):
        m = RX_LETTER_HEAD.match(ln)
        if m and want in _nospace(ln):
            idx = LETTERS.find(m.group(1))
            nxt = LETTERS[idx + 1] if 0 <= idx < len(LETTERS) - 1 else None
            for j in range(i + 1, len(lines)):
                m2 = RX_LETTER_HEAD.match(lines[j])
                if nxt and m2 and m2.group(1) == nxt:
                    return lines[i:j]
            return lines[i:]
    return None


def _items(lines: list[str]) -> list[list[str]]:
    """목 안의 1) 2) … 항목 (번호가 차례로 이어지는 것만 항목 머리로 본다). 첫 덩어리 = 목 머리."""
    out, cur, expect = [], [], 1
    for ln in lines:
        m = RX_ITEM_HEAD.match(ln)
        if m and int(m.group(1)) == expect:
            out.append(cur)
            cur, expect = [ln], expect + 1
        else:
            cur.append(ln)
    out.append(cur)
    return out


def _subitems(lines: list[str]) -> list[list[str]]:
    """항목 안의 가) 나) … (글자 순서가 이어지는 것만). 첫 덩어리 = 항목 머리."""
    out, cur, expect = [], [], 0
    for ln in lines:
        m = RX_SUB_HEAD.match(ln)
        if m and LETTERS.find(m.group(1)) == expect:
            out.append(cur)
            cur, expect = [ln], expect + 1
        else:
            cur.append(ln)
    out.append(cur)
    return out


def _window(lines: list[str], words: list[str], budget: int, around: int = 1) -> list[str]:
    """words 가 든 줄과 앞뒤 around 줄만 (첫 줄은 항상). budget 을 넘지 않게. 건너뛴 곳은 '…'."""
    hit = {i for i, ln in enumerate(lines) if any(w and w in _nospace(ln) for w in words)}
    want = {0} | {j for i in hit for j in range(i - around, i + around + 1) if 0 <= j < len(lines)}
    out, used, last = [], 0, -1
    for i in sorted(want):
        n = len(lines[i]) + 1
        if used + n > budget:
            break
        if i != last + 1 and out:
            out.append("…")
            used += 2
        out.append(lines[i])
        used += n
        last = i
    return out if len(out) > 1 or hit else []


def _item_score(text: str, terms: list[str], weak: list[str] | None = None) -> int:
    """사례 말(폐기물 이름 등)은 3점, 질문 말(소각·매립 등)은 1점."""
    t = _nospace(text)
    return 3 * sum(t.count(w) for w in terms) + sum(t.count(w) for w in weak or [])


def section_excerpt(law: str, key: str, section_kw: str, sub_kw: str, terms: list[str], budget: int,
                    store: LawStore | None = None, weak: list[str] | None = None) -> dict | None:
    """{part, text, note}. 호 조각 → (sub_kw 가 있으면) 그 목 → 길면 terms 가 많이 든 항목부터 budget 안에서 (원래 순서 유지)."""
    part = find_section_part(law, key, section_kw, store)
    if part is None:
        return None
    lines = part.text.split("\n")
    head = next((ln for ln in lines if ln.strip()), "")
    note = f"{head.strip()[:40]}"
    if sub_kw:
        blk = _letter_block(lines, sub_kw)
        if blk is not None:
            lines = blk
            note += f" › {lines[0].strip()[:30]}"
    text = "\n".join(lines)
    if len(text) <= budget:
        return {"part": part, "text": text if text.startswith(head) else head + "\n" + text, "note": note}
    items = _items(lines)
    lead, rest = items[0], items[1:]
    terms = [_nospace(w) for w in terms if len(_nospace(w)) >= 2]
    weak = [_nospace(w) for w in weak or [] if len(_nospace(w)) >= 2]
    # 단위 = 항목(1) 2) …). 예산의 절반보다 긴 항목은 그 안의 가) 나) 로 더 나눈다 (머리 줄은 같이 넣음).
    # 2026.11.17 데모: 별표 5 제3호 라목은 항목 2개인데 둘째가 6천 자 → 통째로 빠져 F-1 근거가 155자뿐이었음.
    units: list[tuple[int, list[str], list[str]]] = []       # (항목 순번, 머리 줄, 본문 줄)
    for i, it in enumerate(rest):
        if len("\n".join(it)) > budget // 2:
            subs = _subitems(it)
            units.append((i, [], subs[0]))
            units += [(i, subs[0][:1], s) for s in subs[1:]]
        else:
            units.append((i, [], it))
    score = [_item_score("\n".join(u[2]), terms, weak) for u in units]
    ranked = sorted(range(len(units)), key=lambda k: (-score[k], k))
    keep, used = set(), len("\n".join(lead[:3])) + len(head)
    for k in ranked:
        n = len("\n".join(units[k][2])) + len("\n".join(units[k][1]))
        if score[k] == 0 and keep:
            continue
        if used + n > budget:
            if score[k] == 0 or budget - used < 200:
                continue
            win = _window(units[k][2], terms + weak, budget - used)   # 너무 긴 부분은 말이 든 줄 앞뒤만
            if not win:
                continue
            units[k] = (units[k][0], units[k][1], win)
            n = len("\n".join(win)) + len("\n".join(units[k][1]))
        keep.add(k)
        used += n
    out = [head] + lead[:3]
    last, heads = -1, set()
    for k in sorted(keep):
        if k != last + 1:
            out.append("…(중략)…")
        i, h, body = units[k]
        if h and (i not in heads) and (k == 0 or units[k - 1][0] != i or (k - 1) not in keep):
            out += h                                               # 잘린 항목의 머리 줄
        heads.add(i)
        out += body
        last = k
    if last != len(units) - 1:
        out.append("…(중략)…")
    return {"part": part, "text": "\n".join(out),
            "note": note + f" — 사례 관련 부분 {len(keep)}/{len(units)}개"}


LONG_ANNEX_CHARS = 8000             # 이보다 긴 별표는 통째로 넣지 않고 행(조각)만 — retriever·evidence 가 같이 씀


def is_long_annex(law: str, key: str, store: LawStore | None = None) -> bool:
    """별표 본체가 LONG_ANNEX_CHARS 보다 긴가 (별표 4의3 ≈ 6만 5천 자, 별표 5)."""
    if not key.startswith("B") or key.startswith("BF"):
        return False
    c = _store(store).get(law, top_key(key))
    return c is not None and len(c.text) > LONG_ANNEX_CHARS


def search_chunks(query: str, laws: list[str] | None = None, k: int = 30, store: LawStore | None = None) -> list[dict]:
    """조각 단위 BM25 (tools/bm25.py). [{"law", "key", "top_key", "score"}]"""
    from law_agent.tools.bm25 import get_index
    return get_index(_store(store)).search(query, k=k, laws=laws)


# ── 검색 ───────────────────────────────────────────────────────

def search(query: str, laws: list[str] | None = None, k: int = 12, store: LawStore | None = None) -> list[dict]:
    """LawStore.search(forms=False) + 조문 단위 키(top_key). 점수 높은 순.
    결과 항목: {"law", "key", "top_key", "name", "title", "score", "text"(앞 300자)}"""
    hits = _store(store).search(query, k=k, laws=laws, forms=False)
    return [{**h, "top_key": top_key(h["key"])} for h in hits]


# ── 인용 뽑기 ──────────────────────────────────────────────────
# 시행령·시행규칙·고시가 가리키는 다른 조문을 (law, key) 로. key 는 조문·별표 단위 ("13의2", "B4의3").
#   "법 제13조의2제2항"              → (WMA, 13의2)        (출발이 WMA_RULE 이면 법 = WMA)
#   "영 별표 1", "시행령 제7조"       → (WMA_DEC, B1), (WMA_DEC, 7)
#   "이 규칙 제3조", "별표 4의3"      → (WMA_RULE, 3), (WMA_RULE, B4의3)    (번호만 있으면 출발 법령)
#   "「자원의 절약과 재활용촉진에 관한 법률 시행령」 제18조" → (RRA_DEC, 18)
#   "같은 법 시행령 제7조"           → 바로 앞 「」 법령의 시행령
#   "제13조 및 제14조"              → 두 번째도 앞 법령을 이어받음 (사이가 접속어·항호뿐일 때)
# 부칙 조문, 이름을 모르는 법령(LAW_NAME 에 없음)은 뺀다.

# 고시·지침의 "법" 이 가리키는 법
FAMILY = {"CEA_DESIG": "CEA", "CEA_RECOG": "CEA", "GUIDE_DECL": "WMA", "ALLBARO_NOTICE": "WMA"}

_NAME_TO_LAW = {norm_text(v): k for k, v in LAW_NAME.items()}

_Q = (r"(?P<q>「(?P<name>[^」]{2,60})」"
      r"|(?:같은|동)\s*법\s*시행령|(?:같은|동)\s*법\s*시행규칙|(?:같은|동)\s*법|같은\s*영|같은\s*규칙"
      r"|이\s*법|이\s*영|이\s*규칙"
      r"|(?<![가-힣])시행령|(?<![가-힣])시행규칙|(?<![가-힣])법|(?<![가-힣])영|(?<![가-힣])규칙)")
_R = r"(?:제\s*(?P<art>\d+)\s*조(?:\s*의\s*(?P<art2>\d+))?|별표\s*(?P<annex>\d+(?:\s*의\s*\d+)?))"
RX_CITE = re.compile(rf"(?:{_Q}\s*)?{_R}")
RX_GAP = re.compile(r"^[\s,·ㆍ및또는와과부터까지제\d항호목의\-~에서각]*$")


def law_family(law: str) -> str:
    """법령 ID → 그 법률 ID. WMA_RULE → WMA, CEA_DESIG → CEA, GUIDE_DECL → WMA."""
    if law in FAMILY:
        return FAMILY[law]
    for suf in ("_DEC", "_RULE"):
        if law.endswith(suf):
            return law[: -len(suf)]
    return law


def _qual_law(q: str, name: str | None, from_law: str, last_named: str | None) -> tuple[str | None, str | None]:
    """수식어 → (법령 ID, 갱신된 last_named 법률). 법령을 모르면 (None, last_named)."""
    base = law_family(from_law)
    if name is not None:
        law = _NAME_TO_LAW.get(norm_text(name))
        return law, (law_family(law) if law else last_named)
    qq = re.sub(r"\s+", "", q)
    same = last_named or base
    table = {
        "같은법시행령": same + "_DEC", "동법시행령": same + "_DEC",
        "같은법시행규칙": same + "_RULE", "동법시행규칙": same + "_RULE",
        "같은법": same, "동법": same, "같은영": same + "_DEC", "같은규칙": same + "_RULE",
        "이법": base, "이영": base + "_DEC", "이규칙": base + "_RULE",
        "법": base, "시행령": base + "_DEC", "영": base + "_DEC", "시행규칙": base + "_RULE", "규칙": base + "_RULE",
    }
    return table.get(qq), last_named


def citations(text: str, from_law: str, self_key: str | None = None) -> list[tuple[str, str]]:
    """원문 text 속 인용 → [(law, key)] (나온 순서, 중복 제거).
    from_law: 이 글이 속한 법령 ID. self_key: 이 글 자신의 조문 키 (자기 자신 인용은 뺌)."""
    out: list[tuple[str, str]] = []
    last_law: str | None = None
    last_end = -1                                    # -1 이면 앞 인용 없음 (last_law 가 None 이어도 이어받음)
    last_named: str | None = None
    for m in RX_CITE.finditer(text or ""):
        start = m.start()
        if "부칙" in text[max(0, start - 6):start]:
            last_law, last_end = None, m.end()       # 부칙 뒤 "및 제3조" 도 이어받아 빠진다
            continue
        if m.group("q"):
            law, last_named = _qual_law(m.group("q"), m.group("name"), from_law, last_named)
        elif last_end >= 0 and len(text[last_end:start]) <= 20 and RX_GAP.match(text[last_end:start]):
            law = last_law                           # "제13조 및 제14조" — 앞 법령을 이어받음 (모르는 법령이면 None)
        else:
            law = from_law
        last_end = m.end()
        last_law = law
        if law is None or law not in LAW_NAME:
            continue
        if m.group("annex"):
            key = "B" + re.sub(r"\s+", "", m.group("annex"))
        else:
            key = m.group("art") + (f"의{m.group('art2')}" if m.group("art2") else "")
        if law == from_law and self_key and key == top_key(self_key):
            continue
        if (law, key) not in out:
            out.append((law, key))
    return out


def resolve_citations(text: str, from_law: str, self_key: str | None = None,
                      store: LawStore | None = None) -> list[dict[str, Any]]:
    """citations() 중 저장소에 실제 있는 조문만 {"law", "key", "title", "ref"} 로."""
    s = _store(store)
    out = []
    for law, key in citations(text, from_law, self_key):
        c = s.get(law, key)
        if c is not None:
            out.append({"law": law, "key": c.key, "title": c.title, "ref": make_ref(c.law, c.key, c.effective, c.hash)})
    return out


# ── "다음 각 호/목" 펼치기 (2026.11.20) ─────────────────────────────
# 인용이 "…다음 각 목의 방법으로 처리할 것"처럼 머리말에서 끝나면 내용은 바로 뒤 호·목에 있다.
# 위치가 정해진 연결이라 검색하지 않고 원문에서 이어 붙인다 (인용 따라가기와 같은 원리). 무엇이 해당하는지는 judge 가 본다.
RX_POINTER = re.compile(r"다음\s*각\s*(호|목)")
RX_STOP = {"목": re.compile(r"(?:^|\s)(?:\d{1,2}\.\s|[①-⑳]|제\d+조(?:의\d+)?\s*\()"),   # 다음 호·항·조에서 끝
           "호": re.compile(r"(?:^|\s)(?:[①-⑳]|제\d+조(?:의\d+)?\s*\()")}            # 다음 항·조에서 끝
RX_HEAD = {"목": re.compile(r"(?:^|\s)([가-하]\.\s[^가-하]{0,60}?)(?=\s[가-하]\.\s|$)"),
           "호": re.compile(r"(?:^|\s)(\d{1,2}\.\s.{0,60}?)(?=\s\d{1,2}\.\s|$)")}
POINTER_MAX = 2500
POINTER_TAIL = 60          # 인용 끝에서 이만큼 안에 "다음 각 호/목"이 있어야 머리말로 봄


def expand_pointer(quote: str, text: str, max_chars: int = POINTER_MAX) -> str | None:
    """quote 가 '다음 각 호/목' 머리말로 끝나면 text(조문 원문)에서 그 뒤 하위 항목을 붙인 글. 아니면 None.
    너무 길면 하위 항목 첫머리만 (…)."""
    q = " ".join((quote or "").split())
    tail = q[-POINTER_TAIL:]
    m = None
    for m in RX_POINTER.finditer(tail):
        pass
    if m is None:
        return None
    level = m.group(1)
    t = " ".join((text or "").split())
    flat_q, flat_t = q.replace(" ", ""), t.replace(" ", "")
    i = flat_t.find(flat_q[-40:]) if len(flat_q) >= 40 else flat_t.find(flat_q)
    if i < 0:
        return None
    end_flat = i + len(flat_q[-40:] if len(flat_q) >= 40 else flat_q)
    j = k = 0                                           # 공백 뺀 위치 → 원문 위치
    while j < len(t) and k < end_flat:
        k += 0 if t[j] == " " else 1
        j += 1
    rest = t[j:]
    stop = RX_STOP[level].search(rest)
    body = rest[: stop.start()] if stop else rest
    body = body.strip()
    if not body:
        return None
    if len(body) > max_chars:
        heads = [h.strip() for h in RX_HEAD[level].findall(body)]
        body = " … ".join(heads) + " …" if heads else body[:max_chars] + " …"
    return q + " " + body
