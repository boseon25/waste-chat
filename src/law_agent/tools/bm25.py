"""조각(청크) 단위 BM25 검색 — 글자 2개(bigram) 토큰. 표준 라이브러리만. 조회만 하고 판정하지 않는다.

lawstore 의 기존 검색(bigram TF-IDF, 조각 앞 3,000자만)과 다른 점
  - 조각 글 전체를 색인한다 (긴 조문 뒷부분의 호도 걸리게)
  - 문서 길이 보정(b) → 긴 조각이 점수를 독차지하지 않음
  - 제목 토큰은 TITLE_WEIGHT 번 더 센다

색인 대상: 통째 문서(type=whole, key "전체"), 별지 서식(BF…) 제외.
           별표 본체는 하위 조각(행)이 있으면 빼고 하위 조각을 색인한다 (긴 별표 본체가 아무 말에나 걸리지 않게).
같은 store 면 색인은 한 번만 만든다.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

K1 = 1.5
B = 0.75
TITLE_WEIGHT = 2


def bigrams(text: str) -> list[str]:
    """lawstore 와 같은 토큰: 한글·영문·숫자만 남기고 글자 2개씩."""
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")
    return [t[i:i + 2] for i in range(len(t) - 1)]


def _top_key(key: str) -> str:
    head = key.split("/", 1)[0].split(":", 1)[0]
    return head.split(".", 1)[0] if head.startswith("§") else head


@dataclass
class BM25Index:
    docs: dict[tuple[str, str], Counter] = field(default_factory=dict)
    lengths: dict[tuple[str, str], int] = field(default_factory=dict)
    df: Counter = field(default_factory=Counter)
    postings: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    avg_len: float = 1.0

    @classmethod
    def build(cls, store: Any) -> "BM25Index":
        idx = cls()
        has_parts = {(c.law, _top_key(c.key)) for c in store.chunks.values() if ":" in c.key}
        for (law, key), c in sorted(store.chunks.items()):
            if c.type == "whole" or key == "전체" or key.startswith("BF"):
                continue
            if key.startswith("B") and ":" not in key and "/" not in key and (law, key) in has_parts:
                continue                                    # 별표 본체 → 행 조각으로
            toks = Counter(bigrams(c.title) * TITLE_WEIGHT + bigrams(c.text))
            if not toks:
                continue
            idx.docs[(law, key)] = toks
            idx.lengths[(law, key)] = sum(toks.values())
            for t in toks:
                idx.df[t] += 1
                idx.postings[t].append((law, key))
        idx.avg_len = (sum(idx.lengths.values()) / len(idx.lengths)) if idx.lengths else 1.0
        return idx

    @classmethod
    def from_chunks(cls, chunks: list) -> "BM25Index":
        """주어진 조각만으로 작은 색인 (긴 별표 하나의 조각들 안에서 고를 때)."""
        idx = cls()
        for c in sorted(chunks, key=lambda c: (c.law, c.key)):
            toks = Counter(bigrams(c.title) * TITLE_WEIGHT + bigrams(c.text))
            if toks:
                idx.docs[(c.law, c.key)] = toks
                idx.lengths[(c.law, c.key)] = sum(toks.values())
                for t in toks:
                    idx.df[t] += 1
                    idx.postings[t].append((c.law, c.key))
        idx.avg_len = (sum(idx.lengths.values()) / len(idx.lengths)) if idx.lengths else 1.0
        return idx

    def idf(self, t: str) -> float:
        n, d = len(self.docs), self.df.get(t, 0)
        return math.log(1 + (n - d + 0.5) / (d + 0.5))

    def search(self, query: str, k: int = 20, laws: list[str] | None = None) -> list[dict]:
        """[{"law", "key", "top_key", "score"}] 점수 높은 순, 같으면 (law, key) 순. 점수 0 은 뺀다."""
        q = Counter(bigrams(query))
        scores: dict[tuple[str, str], float] = defaultdict(float)
        for t, qf in q.items():
            if t not in self.df:
                continue
            w = self.idf(t)
            for doc in self.postings[t]:
                if laws and doc[0] not in laws:
                    continue
                f = self.docs[doc][t]
                norm = K1 * (1 - B + B * self.lengths[doc] / self.avg_len)
                scores[doc] += w * f * (K1 + 1) / (f + norm)
        ranked = sorted(((s, d) for d, s in scores.items() if s > 0), key=lambda x: (-x[0], x[1]))[:k]
        return [{"law": d[0], "key": d[1], "top_key": _top_key(d[1]), "score": round(s, 6)} for s, d in ranked]


_CACHE: dict[int, BM25Index] = {}


def get_index(store: Any) -> BM25Index:
    """store 마다 한 번만 만든다 (id 기준)."""
    k = id(store)
    if k not in _CACHE:
        _CACHE.clear()                                      # store 는 보통 하나 — 옛 색인은 버림
        _CACHE[k] = BM25Index.build(store)
    return _CACHE[k]


def rrf(rank_lists: list[list[tuple[str, str]]], k: int = 60) -> list[tuple[tuple[str, str], float]]:
    """여러 순위 목록 합치기 (Reciprocal Rank Fusion). 점수는 0~1 로 나눠 맞춤 (모든 목록 1위 = 1.0).
    결과: [((law, key), score)] 점수 높은 순, 같으면 (law, key) 순."""
    if not rank_lists:
        return []
    acc: dict[tuple[str, str], float] = defaultdict(float)
    for lst in rank_lists:
        for r, doc in enumerate(lst, start=1):
            acc[doc] += 1.0 / (k + r)
    best = len(rank_lists) / (k + 1)
    return sorted(((d, round(s / best, 6)) for d, s in acc.items()), key=lambda x: (-x[1], x[0]))
