from __future__ import annotations

import csv
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

# 법령 가족: 같은 가족 안에서 "법"·"영"·"규칙"이 무엇인지
FAMILY = {
    "WMA": {"법": "WMA", "영": "WMA_DEC", "규칙": "WMA_RULE"},
    "WMA_DEC": {"법": "WMA", "영": "WMA_DEC", "규칙": "WMA_RULE"},
    "WMA_RULE": {"법": "WMA", "영": "WMA_DEC", "규칙": "WMA_RULE"},
    "CEA": {"법": "CEA", "영": "CEA_DEC", "규칙": "CEA_RULE"},
    "CEA_DEC": {"법": "CEA", "영": "CEA_DEC", "규칙": "CEA_RULE"},
    "CEA_RULE": {"법": "CEA", "영": "CEA_DEC", "규칙": "CEA_RULE"},
}
LAW_NAME = {
    "WMA": "폐기물관리법", "WMA_DEC": "폐기물관리법 시행령", "WMA_RULE": "폐기물관리법 시행규칙",
    "CEA": "순환경제사회 전환 촉진법", "CEA_DEC": "촉진법 시행령", "CEA_RULE": "촉진법 시행규칙",
    "CEA_DESIG": "순환자원 지정 고시", "CEA_RECOG": "순환자원 인정 절차 고시",
    "GUIDE_DECL": "배출자신고 업무처리지침", "ALLBARO_NOTICE": "올바로 고시", "RRA_RULE": "자원재활용법 시행규칙",
}
# 범위 밖 (계획서: 행정처분·벌칙 등). 제목에 이 말이 있으면 후보에서 뺀다
OUT_OF_SCOPE = ("벌칙", "과태료", "양벌", "부칙", "수수료", "행정처분", "청문", "포상금", "권한의 위임", "위임ㆍ위탁",
                "과징금", "벌금", "징역", "규제의 재검토", "민감정보")

# 절차 점검표: 단계 → 제목 키워드 (제목에 있으면 강하게, 본문에만 있으면 약하게)
STAGES = [
    ("배출 전: 배출자 신고·지정폐기물 확인", ("배출자", "신고", "지정폐기물의 확인", "분석")),
    ("보관", ("보관",)),
    ("위탁·계약", ("위탁", "계약", "수탁")),
    ("운반", ("운반",)),
    ("인계 (전자정보처리)", ("전자정보", "인계")),
    ("처리 기준", ("처리기준", "처리 기준", "처리의 기준")),
    ("재활용 기준·준수사항", ("재활용", "준수사항")),
    ("지정폐기물 처리계획", ("처리계획",)),
    ("사후: 처리 확인·보고·기록", ("보고", "장부", "기록", "실적", "확인")),
]

# RAG 검색 질문: 단계마다 제목 키워드로 못 찾는 조문까지 찾기 위해
STAGE_QUERIES = {
    "배출 전: 배출자 신고·지정폐기물 확인": ["사업장폐기물배출자 신고 대상과 시기", "지정폐기물 해당 여부 확인 분석"],
    "보관": ["사업장폐기물 보관 기간과 보관 장소 기준", "지정폐기물 보관 표지와 보관 방법"],
    "위탁·계약": ["폐기물 처리 위탁 계약서 작성", "수탁처리능력 확인 위탁 기준"],
    "운반": ["폐기물 수집 운반 차량 기준", "운반 중 폐기물 흩날림 유출 방지"],
    "인계 (전자정보처리)": ["전자정보처리프로그램 인계 인수 입력"],
    "처리 기준": ["사업장일반폐기물 처리 기준 및 방법", "지정폐기물 처리 기준 및 방법"],
    "재활용 기준·준수사항": ["폐기물 재활용 기준", "재활용하는 자의 준수사항"],
    "지정폐기물 처리계획": ["지정폐기물 처리계획 확인 대상과 제출 서류"],
    "사후: 처리 확인·보고·기록": ["위탁한 폐기물의 처리 과정 확인", "폐기물 배출 및 처리 실적 보고", "폐기물 관리대장 기록 보존"],
}

KEY_RE = re.compile(r"\b([A-Z][A-Z_]+):([^\s,;\"]+?)@(\d{8})")


@dataclass
class Chunk:
    law: str
    key: str
    type: str
    title: str
    text: str
    effective: str
    parent: str | None = None
    refs: list[tuple[str, str]] = field(default_factory=list)   # (law, article/annex key)


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------
def load_chunks(chunks_dir: str | Path):
    """법령마다 가장 최근 시행일 파일. 키 표기는 lawstore 규칙 (별표 B4의3, 별표의 호 B5:3, 지침 §Ⅵ)"""
    from lawstore import LawStore
    return LawStore.load(chunks_dir).chunks


EXCLUDE_TABLES = ("gate3_rules",)        # 관문 3을 쓰지 않으므로 그 표의 근거는 "쓰는 조문"에서 뺌


def used_keys(tables_dir: str | Path, exclude: tuple[str, ...] = EXCLUDE_TABLES) -> dict[tuple[str, str], list[str]]:
    """판정표 전체에서 근거 키를 모은다 → {(법령, 조항): [쓰는 표 이름]}"""
    used: dict[tuple[str, str], list[str]] = defaultdict(list)
    for f in sorted(Path(tables_dir).glob("*.csv")):
        if f.stem in exclude:
            continue
        text = f.read_text(encoding="utf-8-sig")
        for law, key, _ in KEY_RE.findall(text):
            key = key.split("@", 1)[0]                     # BF5@처리구분 → BF5
            if f.stem not in used[(law, key)]:
                used[(law, key)].append(f.stem)
    return dict(used)


# ---------------------------------------------------------------------------
# 인용 추출
# ---------------------------------------------------------------------------
def _top(key: str) -> str:
    from lawstore import top_key
    return top_key(key)


ART = r"제(\d+)조(의\d+)?"
ANNEX = r"별표\s*(\d+)(의\d+)?"


def extract_refs(c: Chunk) -> list[tuple[str, str]]:
    fam = FAMILY.get(c.law, {})
    refs: set[tuple[str, str]] = set()
    text = c.text

    def add(law: str | None, key: str) -> None:
        if law:
            refs.add((law, key))

    # "법 제N조", "영 제N조", "규칙 제N조", "법 별표 N"
    for who, num, sub in re.findall(r"(?<![가-힣])(법|영|규칙)\s*" + ART, text):
        add(fam.get(who), num + (sub or ""))
    for who, num, sub in re.findall(r"(?<![가-힣])(법|영|규칙)\s*" + ANNEX, text):
        add(fam.get(who), "B" + num + (sub or ""))
    # 앞에 "법/영/규칙"이 없는 "제N조", "별표 N" → 같은 법령
    plain = re.sub(r"(?<![가-힣])(법|영|규칙)\s*(" + ART + "|" + ANNEX + ")", "", text)
    for num, sub in re.findall(ART, plain):
        add(c.law, num + (sub or ""))
    for num, sub in re.findall(ANNEX, plain):
        add(c.law, "B" + num + (sub or ""))
    refs.discard((c.law, _top(c.key)))                    # 자기 자신
    return sorted(refs)


# ---------------------------------------------------------------------------
# 점검
# ---------------------------------------------------------------------------
def _covered(law: str, key: str, used: dict) -> list[str]:
    """이 조문(또는 그 일부·상위)을 판정표가 쓰는지 → 쓰는 표 목록"""
    tables = []
    for (ul, uk), ts in used.items():
        if ul != law:
            continue
        if uk == key or _top(uk) == _top(key):
            tables += ts
    return sorted(set(tables))


def _out_of_scope(c) -> bool:
    return any(w in c.title for w in OUT_OF_SCOPE) or c.key.startswith("BF")     # 별지 서식은 의무 조문이 아님


def audit(chunks: dict, used: dict, depth: int = 2, store=None, search_k: int = 6) -> dict:
    """store(LawStore)를 주면 단계별 RAG 검색 후보도 찾는다."""
    # 조문·별표 단위 노드 (article_part는 상위 조문에 합침)
    nodes: dict[tuple[str, str], Chunk] = {}
    texts: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (law, key), c in chunks.items():
        top = (law, _top(key))
        texts[top].append(c.text)
        if top not in nodes or c.type in ("article", "annex", "doc_part"):
            nodes[top] = c
    edges: dict[tuple[str, str], set] = defaultdict(set)
    for top, c in nodes.items():
        merged = Chunk(c.law, top[1], c.type, c.title, "\n".join(texts[top])[:20000], c.effective)
        for r in extract_refs(merged):
            if r in nodes:
                edges[top].add(r)

    # 1. 인용 그래프: 판정표가 쓰는 조문에서 depth 단계까지
    starts = {(l, _top(k)) for (l, k) in used if (l, _top(k)) in nodes}
    seen = {s: [s] for s in starts}
    q = deque((s, 0) for s in starts)
    while q:
        cur, d = q.popleft()
        if d >= depth:
            continue
        for nxt in sorted(edges[cur]):
            if nxt not in seen:
                seen[nxt] = seen[cur] + [nxt]
                q.append((nxt, d + 1))
    graph_cands = []
    for node, path in seen.items():
        if node in starts:
            continue
        c = nodes[node]
        if _out_of_scope(c) or _covered(node[0], node[1], used):
            continue
        graph_cands.append({
            "law": node[0], "law_name": LAW_NAME.get(node[0], node[0]), "key": node[1], "title": c.title,
            "reason": "인용: " + " → ".join(f"{LAW_NAME.get(l, l)} {k}" for l, k in path),
            "depth": len(path) - 1, "text": " ".join(texts[node])[:200]})
    graph_cands.sort(key=lambda x: (x["depth"], x["law"], x["key"]))

    # 2. 절차 점검표
    stages = []
    for name, words in STAGES:
        rows = []
        for node, c in nodes.items():
            if _out_of_scope(c):
                continue
            in_title = [w for w in words if w in c.title]
            if not in_title:
                continue
            tables = _covered(node[0], node[1], used)
            rows.append({"law": node[0], "law_name": LAW_NAME.get(node[0], node[0]), "key": node[1],
                         "title": c.title, "matched": in_title, "used_by": tables,
                         "text": " ".join(texts[node])[:200]})
        rows.sort(key=lambda r: (bool(r["used_by"]), r["law"], r["key"]))
        searched = []
        if store is not None:                                      # RAG: 질문으로 검색
            have = {(r["law"], r["key"]) for r in rows}
            for q in STAGE_QUERIES.get(name, []):
                for hit in store.search(q, k=search_k, forms=False, exclude_titles=OUT_OF_SCOPE):
                    node = (hit["law"], _top(hit["key"]))
                    if node in have or node not in nodes:
                        continue
                    have.add(node)
                    tables = _covered(node[0], node[1], used)
                    searched.append({"law": node[0], "law_name": LAW_NAME.get(node[0], node[0]), "key": node[1],
                                     "title": nodes[node].title, "matched": [f"검색: {q}"], "hit": hit["key"],
                                     "score": hit["score"], "used_by": tables, "text": hit["text"][:200]})
        stages.append({"stage": name, "total": len(rows), "used": sum(1 for r in rows if r["used_by"]),
                       "not_used": [r for r in rows if not r["used_by"]],
                       "used_rows": [r for r in rows if r["used_by"]],
                       "search_not_used": [r for r in searched if not r["used_by"]]})

    # 판정표가 쓰는데 조문 파일에 없는 키 (오타·개정 등)
    missing_in_chunks = sorted(f"{l}:{k}" for (l, k) in used if (l, _top(k)) not in nodes)
    return {"graph_candidates": graph_cands, "stages": stages, "used_count": len(starts),
            "node_count": len(nodes), "missing_in_chunks": missing_in_chunks}
