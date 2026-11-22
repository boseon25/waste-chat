"""조문 통째 가져오기 + 빠짐 검증.

RAG 검색은 비슷한 조각 몇 개만 찾아서 한 조문의 호가 일부만 걸릴 수 있다 (제18조의2 제1항 5~9호 누락 사례).
그래서 찾는 것은 RAG, 가져오는 것은 조문 구조대로 통째로 하고, 호 개수를 원문 기준과 맞춰 본다.

  store.article("WMA_RULE", "18의2")
    → 조문 전체 원문, 항·호 구조, complete(빠짐 없음 여부), problems(무엇이 안 맞는지)
  store.find_articles("지정폐기물 처리계획 확인", k=3)
    → 검색 결과를 조문 단위로 묶어 각각 article() 결과로

기준 개수
  1순위 counts   : law_chunk가 API 구조(항·호 칸)에서 센 값. 가장 믿을 만함
  2순위 본문 번호 : counts가 없는 옛 청크면 조문 본문 줄머리 번호(①, 1., 3의2.)로 센다

맞춰 보는 것
  - 항마다 호 조각(article_part) 수 = 기준 개수
  - 본문에 있는 호 번호가 조각에 다 있는지 (어느 호가 빠졌는지 이름으로)
  - 호 번호가 1부터 빠짐없이 이어지는지 (가지번호 "3의2"는 건너뛰어도 됨)
  - 조각 글이 조문 전체 원문 안에 실제로 있는지
"""
from __future__ import annotations

import re

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
RX_HANG = re.compile(rf"^\s*([{CIRCLED}])")
RX_HANG_NUM = re.compile(r"^\s*[<〈](\d+)[>〉]")      # ⑳ 넘는 항·신설 항이 "<16>"처럼 오는 경우
RX_HO = re.compile(r"^\s*(\d+(?:의\d+)?)\.(?!\d)")
RX_PART = re.compile(r"^(?:항(?P<hang>[^/]+))?(?:/?호(?P<ho>[^/]+))?$")
NO_HANG = "-"                       # 항 번호가 없는 조문 ("2/호4")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def parse_article(text: str) -> list[dict]:
    """조문 본문 → [{hang, lead, hos: [{no, text}]}]. 항이 없는 조문은 hang = "-" 하나."""
    hangs: list[dict] = []
    cur: dict | None = None
    ho: dict | None = None
    for line in (text or "").splitlines():
        m = RX_HANG.match(line)
        n = None if m else RX_HANG_NUM.match(line)
        if m or n:
            no = str(CIRCLED.index(m.group(1)) + 1) if m else n.group(1)
            cur = {"hang": no, "lead": line.strip(), "hos": []}
            hangs.append(cur)
            ho = None
            continue
        m = RX_HO.match(line)
        if m:
            if cur is None:
                cur = {"hang": NO_HANG, "lead": "", "hos": []}
                hangs.append(cur)
            ho = {"no": m.group(1), "text": line.strip()}
            cur["hos"].append(ho)
            continue
        if ho is not None:
            ho["text"] += "\n" + line.strip()          # 목·세목·이어지는 줄
        elif cur is not None:
            cur["lead"] += "\n" + line.strip()
    return hangs


def _ho_sort(no: str) -> tuple[int, int]:
    a, _, b = no.partition("의")
    return (int(a) if a.isdigit() else 0, int(b) if b.isdigit() else 0)


def _gaps(nos: list[str]) -> list[str]:
    """본번호(가지번호 제외)가 1부터 이어지지 않으면 빠진 번호"""
    main = sorted({int(n) for n in nos if n.isdigit()})
    if not main:
        return []
    return [str(i) for i in range(1, main[-1] + 1) if i not in main]


def article(store, law: str, key: str) -> dict | None:
    """조문 하나를 통째로 + 빠짐 검증. 별표·지침이면 annex() 결과."""
    key = key.split("/", 1)[0]
    if key.startswith("B") or key.startswith("§"):
        return annex(store, law, key)
    c = store.get(law, key)
    if c is None:
        return None

    parsed = parse_article(c.text)
    parts: dict[str, dict[str, str]] = {}                 # 항 → {호: 글}
    for ch in store.children(law, key):
        m = RX_PART.match(ch.key[len(key) + 1:])
        if not m or not m.group("ho"):
            continue
        hang = re.sub(r"[<>〈〉]", "", m.group("hang") or "") or NO_HANG     # 옛 청크의 "항<17>" 키도 받음
        parts.setdefault(hang, {})[m.group("ho")] = ch.text

    if c.counts:
        expected = {h: n for h, n in (c.counts.get("호") or {}).items()}
        basis = "counts (API 구조 기준)"
    else:
        expected = {h["hang"]: len(h["hos"]) for h in parsed if h["hos"]}
        basis = "본문 번호 (옛 청크, counts 없음)"

    problems: list[str] = []
    full = _norm(c.text)
    by_hang = {h["hang"]: h for h in parsed}
    for hang in sorted(set(expected) | set(parts) | {h for h, v in by_hang.items() if v["hos"]},
                       key=lambda x: (x == NO_HANG, int(x) if x.isdigit() else 0)):
        label = "항 없음" if hang == NO_HANG else f"제{hang}항"
        want = expected.get(hang, 0)
        got = parts.get(hang, {})
        text_nos = [h["no"] for h in by_hang.get(hang, {}).get("hos", [])]
        if len(got) != want:
            problems.append(f"{label}: 호 조각 {len(got)}개, 원문 기준 {want}개")
        missing = [n for n in text_nos if n not in got]
        if missing:
            problems.append(f"{label}: 조각에 없는 호 {missing}")
        if len(text_nos) != want:
            problems.append(f"{label}: 본문에서 읽은 호 {len(text_nos)}개, 원문 기준 {want}개 (본문 잘림 의심)")
        gaps = _gaps(text_nos)
        if gaps:
            problems.append(f"{label}: 번호가 비어 있음 {gaps}")
        for no, t in got.items():
            body = t.split("\n", 2)[-1] if t.count("\n") >= 2 else t
            if _norm(body)[:30] and _norm(body)[:30] not in full:
                problems.append(f"{label} 제{no}호: 조각 글이 조문 원문에 없음")

    if c.counts and c.counts.get("항") and len([h for h in parsed if h["hang"] != NO_HANG]) != c.counts["항"]:
        problems.append(f"항 {len([h for h in parsed if h['hang'] != NO_HANG])}개, 원문 기준 {c.counts['항']}개")

    hangs_out = []
    for h in parsed:
        hangs_out.append({
            "hang": h["hang"], "lead": h["lead"],
            "hos": [{"no": x["no"], "key": f"{key}/항{h['hang']}/호{x['no']}" if h["hang"] != NO_HANG
                     else f"{key}/호{x['no']}", "text": x["text"]}
                    for x in sorted(h["hos"], key=lambda x: _ho_sort(x["no"]))],
        })
    return {
        "law": law, "key": key, "name": c.name, "title": c.title, "effective": c.effective,
        "ref": f"{law}:{key}@{c.effective}#{c.hash}",
        "text": c.text, "hangs": hangs_out,
        "ho_count": {h: len(v) for h, v in parts.items()}, "expected": expected, "basis": basis,
        "complete": not problems, "problems": problems,
    }


def annex(store, law: str, key: str) -> dict | None:
    """별표·지침 장 통째로. 별표는 호("1." "2.") 번호가 이어지는지만 본다."""
    c = store.get(law, key)
    if c is None:
        return None
    subs = sorted(store.children(law, key), key=lambda x: x.key)
    nos = [re.sub(r"~\d+$", "", s.key.split(":", 1)[1]) for s in subs if ":" in s.key]
    problems = [f"번호가 비어 있음 {g}" for g in [_gaps(nos)] if g]
    return {
        "law": law, "key": key, "name": c.name, "title": c.title, "effective": c.effective,
        "ref": f"{law}:{key}@{c.effective}#{c.hash}", "text": c.text,
        "parts": [{"key": s.key, "text": s.text} for s in subs],
        "basis": "별표 호 번호", "complete": not problems, "problems": problems,
    }


def find_articles(store, query: str, k: int = 3, laws: list[str] | None = None, pool: int = 30) -> list[dict]:
    """RAG로 찾고 → 조문 단위로 묶어 통째로. 검색 점수는 그 조문 조각 중 가장 높은 값."""
    from . import top_key
    hits = store.search(query, k=pool, laws=laws, forms=False)
    best: dict[tuple[str, str], dict] = {}
    for h in hits:
        tk = (h["law"], top_key(h["key"]))
        if tk not in best:
            best[tk] = {"score": h["score"], "matched": [h["key"]]}
        else:
            best[tk]["matched"].append(h["key"])
    out = []
    for (law, key), b in sorted(best.items(), key=lambda x: -x[1]["score"])[:k]:
        a = article(store, law, key)
        if a:
            out.append({**a, "score": b["score"], "matched": b["matched"]})
    return out


def attach(cls) -> None:
    cls.article = lambda self, law, key: article(self, law, key)
    cls.annex = lambda self, law, key: annex(self, law, key)
    cls.find_articles = lambda self, query, k=3, laws=None, pool=30: find_articles(self, query, k, laws, pool)
