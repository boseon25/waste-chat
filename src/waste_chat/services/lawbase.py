"""법령 원문 검색. engine/law/corpus.jsonl(국가법령정보 OPEN API로 받은 조문·별표 조각)을 BM25로 찾는다.

외부 패키지 없이 한글 두 글자 조각과 숫자·조문 번호로 색인한다. 답변에 붙이는 인용은 모두 이 원문에서 나온다.
"""
import json
import math
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "engine" / "law" / "corpus.jsonl"
PRACTICE = ROOT / "engine" / "law" / "practice.jsonl"
# 근거의 등급. 답변은 법령 → 행정규칙·지침 → 서식 작성방법 → 실무 안내 순으로 기대고, 등급을 밝힌다.
KIND_LABEL = {"law": "법령", "rule": "행정규칙·지침", "form": "서식 작성방법", "practice": "실무 안내"}
RULE_LAWS = {"GUIDE_DECL", "ALLBARO_NOTICE", "CEA_DESIG", "CEA_RECOG"}


def kind_of(law, key):
    if law == "PRACTICE":
        return "practice"
    if law in RULE_LAWS:
        return "rule"
    if re.match(r"^(BF|F)\d", key or ""):
        return "form"
    return "law"
_LOCK = threading.Lock()
_DB = {}

SHORT = {  # 화면에 쓰는 짧은 법령 이름
    "WMA": "폐기물관리법", "WMA_DEC": "폐기물관리법 시행령", "WMA_RULE": "폐기물관리법 시행규칙",
    "CEA": "순환경제사회 전환 촉진법", "CEA_DEC": "순환경제사회법 시행령", "CEA_RULE": "순환경제사회법 시행규칙",
    "CEA_DESIG": "순환자원 지정 고시", "CEA_RECOG": "순환자원 인정 고시", "ALLBARO_NOTICE": "올바로 운영 고시",
    "GUIDE_DECL": "배출자신고·처리계획 업무처리지침", "RRA": "자원재활용법", "RRA_DEC": "자원재활용법 시행령",
    "RRA_RULE": "자원재활용법 시행규칙",
}


def _num(part):
    """'16의13' → '16조의13' 처럼 쓰기 위해 본번호와 가지번호를 나눈다."""
    base, _, sub = part.partition("의")
    return base, sub


def label(law, key):
    """조각 키를 사람이 읽는 조문 표시로 바꾼다. 예: WMA_RULE, '16의13/호3' → 폐기물관리법 시행규칙 제16조의13 제3호"""
    name = SHORT.get(law, law)
    if not key:
        return name
    if key.startswith("§"):
        return f"{name} {key[1:]}"
    m = re.match(r"^BF?(\d+(?:의\d+)?)(?::(.+))?$", key)
    if m and key.startswith("BF"):
        base, sub = _num(m.group(1))
        tail = (m.group(2) or "").split("_")[0].rstrip(".")
        return f"{name} 별지 제{base}호{'의' + sub if sub else ''}서식" + (f" 작성방법 {tail}." if tail else "")
    if m:
        base, sub = _num(m.group(1))
        tail = m.group(2)
        text = f"{name} 별표 {base}{'의' + sub if sub else ''}"
        if tail:
            text += " " + tail.rstrip(".") + "."
        return text
    m = re.match(r"^F(\d+(?:의\d+)?)$", key)
    if m:
        base, sub = _num(m.group(1))
        return f"{name} 별지 제{base}호{'의' + sub if sub else ''}서식"
    parts = key.split("/")
    base, sub = _num(parts[0])
    out = f"{name} 제{base}조{'의' + sub if sub else ''}"
    for p in parts[1:]:
        m = re.match(r"^(항|호|목)(.+)$", p)
        if not m:
            continue
        kind, val = m.groups()
        if kind == "목":
            out += f" {val}목"
        else:
            b, s = _num(val)
            out += f" 제{b}{kind}{'의' + s if s else ''}"
    return out


STOP = re.compile(r"^(어떻게|어디|어디에|어디서|무엇|무엇을|뭘|뭐|뭐야|누가|언제|왜|해야|해야해|하면|하나요|하나|되나|되나요|돼|알려|알려줘|"
                  r"주세요|해줘|해주세요|모르면|모를|물어|물어봐|물어보면|있어|있나|있나요|인가요|인가|이야|건가|할까|하고|그리고|"
                  r"이|그|저|것|좀|더|꼭|이거|이건|여기|거기|칸|칸은|칸에|칸에는)$")


def _tokens(text, query=False):
    text = re.sub(r"<[^>]*>", " ", text or "")
    out = []
    for word in re.findall(r"[가-힣]+|[A-Za-z]+|\d+(?:-\d+)*", text):
        if query and STOP.match(word):
            continue
        if re.fullmatch(r"[가-힣]+", word):
            out += [word[i:i + 2] for i in range(len(word) - 1)] or [word]
        else:
            out.append(word.lower())
    return out


def _load():
    with _LOCK:
        if _DB:
            return _DB
        rows = []
        for path in (CORPUS, PRACTICE):
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    r["id"] = f"{r['law']}:{r['key']}"
                    r["kind"] = kind_of(r["law"], r["key"])
                    r["label"] = (f"{r['law_name']} · {r['title']}" if r["kind"] == "practice" else label(r["law"], r["key"]))
                    rows.append(r)
        index = defaultdict(list)
        lengths = []
        for i, r in enumerate(rows):
            tf = Counter(_tokens(r["title"] + " " + r["title"] + " " + r["text"]))  # 제목은 두 번 센다
            lengths.append(sum(tf.values()))
            for t, n in tf.items():
                index[t].append((i, n))
        _DB.update(rows=rows, index=index, lengths=lengths,
                   avg=(sum(lengths) / len(lengths)) if lengths else 1,
                   by_id={r["id"]: r for r in rows})
        return _DB


def ready():
    return bool(_load()["rows"])


def get(chunk_id):
    """'WMA_RULE:16의13/호3' 같은 id로 조각 하나. 정확히 없으면 가장 가까운 상위 조문."""
    db = _load()
    if chunk_id in db["by_id"]:
        return db["by_id"][chunk_id]
    law, _, key = chunk_id.partition(":")
    while "/" in key:
        key = key.rsplit("/", 1)[0]
        if f"{law}:{key}" in db["by_id"]:
            return db["by_id"][f"{law}:{key}"]
    return None


def search(query, k=6, laws=None, prefer=(), parts_only=True, kinds=None):
    """BM25. prefer에 있는 조각 id(또는 그 하위)는 점수를 높인다. 같은 조문의 큰 조각과 작은 조각이 겹치면 작은 조각만 쓴다."""
    db = _load()
    if not db["rows"]:
        return []
    terms = Counter(_tokens(query, query=True))
    n = len(db["rows"])
    scores = defaultdict(float)
    k1, b = 1.4, 0.7
    for t, qn in terms.items():
        posting = db["index"].get(t)
        if not posting:
            continue
        idf = math.log(1 + (n - len(posting) + .5) / (len(posting) + .5))
        for i, tf in posting:
            dl = db["lengths"][i]
            scores[i] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / db["avg"])) * min(qn, 2)
    prefer = tuple(prefer)
    if prefer:
        top = max(scores.values(), default=1.0)
        for i, r in enumerate(db["rows"]):
            if any(r["id"] == p or r["id"].startswith(p + "/") or r["id"].startswith(p + ":") for p in prefer):
                scores[i] += top * 0.6
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    out, seen = [], set()
    for i, s in ranked:
        r = db["rows"][i]
        if laws and r["law"] not in laws:
            continue
        if kinds and r["kind"] not in kinds:
            continue
        if parts_only and r["type"] in ("article", "annex") and len(r["text"]) > 2500:
            continue  # 긴 원문 전체보다 항·호 단위 조각이 인용하기 좋다
        root = r["id"].split("/")[0]
        if r["id"] in seen or (root in seen and r["type"] in ("article", "annex")):
            continue
        if r["kind"] == "practice":  # 같은 자료의 여러 조각은 하나만
            root = "P:" + r["law_name"] + r["title"]
            if root in seen:
                continue
        seen.add(r["id"])
        seen.add(root)
        out.append({**r, "score": round(s, 3)})
        if len(out) >= k:
            break
    return out


def tiered(query, prefer=(), k_law=5, k_practice=3):
    """법령·행정규칙·서식 작성방법과 실무 안내를 따로 찾아 합친다. 실무 자료가 법령 검색에 묻히지 않게 한다."""
    first = search(query, k=k_law, prefer=prefer, kinds={"law", "rule", "form"})
    extra = search(query, k=k_practice, kinds={"practice"})
    return first + extra


def brief(r, limit=700):
    """프롬프트·화면에 넣는 짧은 원문."""
    text = re.sub(r"<개정[^>]*>|<신설[^>]*>|<전문개정[^>]*>", "", r["text"])
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def public(r, limit=1200):
    return {"id": r["id"], "label": r["label"], "title": r.get("title", "") if r["kind"] != "practice" else "",
            "effective": r.get("effective", ""), "kind": r["kind"], "kind_label": KIND_LABEL[r["kind"]],
            "url": r.get("url", ""), "text": brief(r, limit)}
