# route_agent/src/lawstore/__init__.py 에서 복사 (2026.11.17). 바꾼 점: 없음
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""법령 조문 저장소 (data/law/chunks) — 조회, 발췌, 검색(RAG).

판정에는 쓰지 않는다. 근거 원문을 보여주고(발췌), 빠진 법령을 찾고(law_audit), 에이전트가 법령을 찾을 때(agent) 쓴다.

키 표기 (판정표 근거 키와 맞춤)
  조문          "17", "17/항1/호3", "13의2/항2"
  별표          "B4의3"        (조문 파일에는 type=annex, key="4의3" → 조문 "5"와 별표 "5"가 겹치지 않게 B를 붙임)
  별표의 호      "B5:3"         (조문 파일 "B5:3." → 끝 점 제거. 같은 번호가 또 나오면 "B5:3~2")
  지침          "§Ⅵ", "§Ⅶ.2"
근거 키 전체: "WMA_RULE:B4의3@20261002#7aeb…" → resolve()

  store = LawStore.load(os.environ["WASTE_LAW_DIR"])      # = data/law/chunks
  store.excerpt("WMA_RULE:B4의3@20261002#…", codes=["51-03-01"])
  store.search("사업장폐기물 보관 기간", k=5)
  store.article("WMA_RULE", "18의2")                     # 조문 통째 + 항·호 구조 + 빠짐 검증
  store.find_articles("지정폐기물 처리계획 확인", k=3)     # RAG로 찾고 → 조문 통째로
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

LAW_NAME = {
    "WMA": "폐기물관리법", "WMA_DEC": "폐기물관리법 시행령", "WMA_RULE": "폐기물관리법 시행규칙",
    "CEA": "순환경제사회 전환 촉진법", "CEA_DEC": "순환경제사회 전환 촉진법 시행령",
    "CEA_RULE": "순환경제사회 전환 촉진법 시행규칙", "CEA_DESIG": "순환자원 지정 등에 관한 고시",
    "CEA_RECOG": "순환자원 인정 절차 및 방법 등에 관한 고시",
    "GUIDE_DECL": "사업장폐기물 배출자신고 및 처리계획 확인 업무처리지침",
    "ALLBARO_NOTICE": "폐기물 전자정보처리 프로그램 운영 및 사용 등에 관한 고시",
    "RRA_RULE": "자원의 절약과 재활용촉진에 관한 법률 시행규칙",
    "RRA": "자원의 절약과 재활용촉진에 관한 법률", "RRA_DEC": "자원의 절약과 재활용촉진에 관한 법률 시행령",
    "CWA": "건설폐기물의 재활용촉진에 관한 법률", "CWA_DEC": "건설폐기물의 재활용촉진에 관한 법률 시행령",
    "CWA_RULE": "건설폐기물의 재활용촉진에 관한 법률 시행규칙",
    "EWA": "전기ㆍ전자제품 및 자동차의 자원순환에 관한 법률",
    "EWA_DEC": "전기ㆍ전자제품 및 자동차의 자원순환에 관한 법률 시행령",
    "EWA_RULE": "전기ㆍ전자제품 및 자동차의 자원순환에 관한 법률 시행규칙",
    "CMA": "화학물질관리법", "CMA_DEC": "화학물질관리법 시행령", "CMA_RULE": "화학물질관리법 시행규칙",
}
TABLE_KEY = re.compile(r"^([A-Z][A-Z_]+):(.+?)@(\d{8})(?:#([0-9a-f]+))?$")
EXCERPT_MAX = 800


def norm_key(row: dict) -> str:
    t, k = row.get("type"), str(row["key"])
    if t == "annex":
        return "B" + k
    if t == "annex_part":
        m = re.match(r"^(.*?)\.(?:_(\d+))?$", k)
        if m:
            return m.group(1) + (f"~{m.group(2)}" if m.group(2) else "")
    if t == "whole":
        return "전체"
    return k


def top_key(key: str) -> str:
    """조항 키 → 조문·별표 단위 ("13의2/항2" → "13의2", "B5:3~2" → "B5", "§Ⅶ.2" → "§Ⅶ")"""
    head = key.split("/", 1)[0].split(":", 1)[0]
    if head.startswith("§"):
        return head.split(".", 1)[0]
    return head


@dataclass
class Chunk:
    law: str
    key: str
    type: str
    title: str
    text: str
    effective: str
    hash: str
    parent: str | None = None
    counts: dict | None = None          # 원문 기준 항·호 개수 (law_chunk 새 판). 옛 청크에는 없음

    @property
    def name(self) -> str:
        return f"{LAW_NAME.get(self.law, self.law)} {self.label}"

    @property
    def label(self) -> str:
        k = self.key
        if k.startswith("BF"):
            m = re.match(r"^BF([^:~]+)(?::([^~]+))?", k)
            return f"별지 제{m.group(1)}호서식" + (f" ({m.group(2)})" if m.group(2) else "")
        if k.startswith("B"):
            m = re.match(r"^B([^:~]+)(?::([^~]+))?", k)
            return f"별표 {m.group(1)}" + (f" 제{m.group(2)}호" if m.group(2) else "")
        if k.startswith("§"):
            return k[1:]
        parts = k.split("/")
        out = [f"제{parts[0].replace('의', '조의', 1)}" if "의" in parts[0] else f"제{parts[0]}조"]
        out += [f"제{p[1:]}{p[0]}" for p in parts[1:] if p and p[0] in "항호목"]
        return " ".join(out)


@dataclass
class LawStore:
    chunks: dict[tuple[str, str], Chunk] = field(default_factory=dict)
    _index: dict | None = None

    # ── 읽기 ──
    @classmethod
    def load(cls, chunks_dir: str | Path | None = None) -> "LawStore":
        chunks_dir = Path(chunks_dir or os.environ["WASTE_LAW_DIR"])
        if not chunks_dir.is_dir():
            raise FileNotFoundError(f"법령 조문 폴더가 없습니다: {chunks_dir} (data/law/chunks, 환경변수 WASTE_LAW_DIR)")
        s = cls()
        for law_dir in sorted(p for p in chunks_dir.iterdir() if p.is_dir()):
            files = sorted(law_dir.glob("*.jsonl"))
            if not files:
                continue
            for line in files[-1].open(encoding="utf-8"):             # 법령마다 최신 시행일 파일
                if not line.strip():
                    continue
                r = json.loads(line)
                c = Chunk(r.get("law_id") or law_dir.name, norm_key(r), r.get("type", ""), r.get("title") or "",
                          r.get("text") or "", str(r.get("effective") or ""), r.get("hash") or "", r.get("parent"),
                          r.get("counts"))
                s.chunks.setdefault((c.law, c.key), c)
        return s

    def get(self, law: str, key: str) -> Chunk | None:
        return self.chunks.get((law, key)) or self.chunks.get((law, key.lstrip("§") and key))

    def resolve(self, table_key: str) -> tuple[Chunk | None, dict]:
        """근거 키 → (조각, 정보). 조각이 없으면 상위 조문·별표로 내려간다."""
        m = TABLE_KEY.match(table_key.strip())
        if not m:
            return None, {"error": "근거 키 형식이 아님"}
        law, key, date, h = m.groups()
        key = key.split("@", 1)[0]                                     # BF5@처리구분 → BF5
        info = {"law": law, "key": key, "effective_in_key": date, "hash_in_key": h}
        c = self.get(law, key)
        if c is None:
            c = self.get(law, top_key(key))
            info["fallback"] = "상위 조문·별표" if c else None
        if c:
            info["effective_in_store"] = c.effective
            info["same_effective"] = c.effective == date
        return c, info

    def find_by_name(self, law_name: str, article: str | None) -> str | None:
        """"폐기물관리법", "제13조의2 제2항" → 근거 키 ("WMA:13의2/항2@시행일"). 못 찾으면 None"""
        law = next((k for k, v in LAW_NAME.items() if v == law_name), None)
        m = re.match(r"^제(\d+)조(?:의(\d+))?(?:\s*제(\d+)항)?(?:\s*제(\d+)호)?", (article or "").strip())
        if not law or not m:
            return None
        key = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        full = key + (f"/항{m.group(3)}" if m.group(3) else "") + (f"/호{m.group(4)}" if m.group(4) else "")
        c = self.get(law, full) or self.get(law, key)
        return f"{law}:{c.key}@{c.effective}" if c else None

    def children(self, law: str, key: str) -> list[Chunk]:
        pre = (key + "/", key + ":")
        return [c for (l, k), c in self.chunks.items() if l == law and k.startswith(pre)]

    # ── 발췌: 근거로 보여줄 부분만 ──
    def excerpt(self, table_key: str, codes: list[str] | None = None, max_len: int = EXCERPT_MAX) -> dict:
        c, info = self.resolve(table_key)
        if c is None:
            return {"text": None, "text_basis": "조문 없음", **info}
        out = {"name": c.name, "title": c.title, "effective": c.effective, **info}
        if codes and len(c.text) > max_len:
            rows = [r for code in codes for r in box_table_rows(c.text, code)]
            if rows:
                return {**out, "text": "\n".join(rows)[:max_len * 2], "text_basis": "분류번호 행 발췌"}
        text = clean(c.text)
        if len(text) <= max_len:
            return {**out, "text": text, "text_basis": "조각 그대로"}
        return {**out, "text": text[:max_len] + " …", "text_basis": "앞부분 (길어서 줄임)"}

    # ── 검색 (RAG): 글자 2개 단위 TF-IDF ──
    def _build(self) -> dict:
        docs, df = {}, Counter()
        for k, c in self.chunks.items():
            if c.type == "whole" or (c.type in ("annex",) and len(c.text) > 20000):
                continue                                                # 통째 문서·아주 긴 별표 본체는 조각으로 검색
            toks = Counter(_bigrams(c.title) * 3 + _bigrams(c.text[:3000]))
            if toks:
                docs[k] = toks
                df.update(toks.keys())
        n = len(docs)
        idf = {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}
        vecs = {}
        for k, toks in docs.items():
            v = {t: (1 + math.log(f)) * idf[t] for t, f in toks.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            vecs[k] = {t: x / norm for t, x in v.items()}
        return {"idf": idf, "vecs": vecs}

    def search(self, query: str, k: int = 5, laws: list[str] | None = None,
               exclude_titles: tuple[str, ...] = (), forms: bool = True) -> list[dict]:
        """forms=False: 별지 서식(신청서·보고서 양식)은 빼고 조문·별표·지침만"""
        if self._index is None:
            self._index = self._build()
        idf, vecs = self._index["idf"], self._index["vecs"]
        q = Counter(_bigrams(query))
        qv = {t: (1 + math.log(f)) * idf.get(t, 0.0) for t, f in q.items() if t in idf}
        qn = math.sqrt(sum(x * x for x in qv.values())) or 1.0
        scored = []
        for key, v in vecs.items():
            if laws and key[0] not in laws:
                continue
            c = self.chunks[key]
            if any(w in c.title for w in exclude_titles) or (not forms and is_form(c)):
                continue
            s = sum(x * v.get(t, 0.0) for t, x in qv.items()) / qn
            if s > 0:
                scored.append((s, key))
        scored.sort(reverse=True)
        return [{"law": l, "key": kk, "name": self.chunks[(l, kk)].name, "title": self.chunks[(l, kk)].title,
                 "score": round(s, 4), "text": clean(self.chunks[(l, kk)].text)[:300]} for s, (l, kk) in scored[:k]]


def is_form(c: Chunk) -> bool:
    return c.key.startswith("BF")


def _bigrams(text: str) -> list[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    return [t[i:i + 2] for i in range(len(t) - 1)]


def clean(text: str) -> str:
    """괘선·공백 정리 (원문 글자는 바꾸지 않음)"""
    t = re.sub(r"[─━┼┬┴├┤┌┐└┘]+", " ", text)
    t = t.replace("│", " ")
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def box_table_rows(text: str, code: str) -> list[str]:
    """괘선 표(별표 4의3 등)에서 분류번호 행만 꺼낸다. 칸은 ' | '로, 행 안 묶음은 줄로 나눈다."""
    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        if code not in line or not line.lstrip().startswith("│"):
            continue
        block = []
        for ln in lines[i:]:
            if block and ln.lstrip().startswith("├"):                 # 다음 행 경계
                break
            block.append(ln)
        groups, cur = [], []
        for ln in block:
            if "├" in ln or "┼" in ln:                                 # 행 안 묶음 경계 (예: 사전분석 해당/해당 없음)
                if cur:
                    groups.append(cur)
                cur = []
                continue
            cur.append(ln)
        if cur:
            groups.append(cur)
        rendered = []
        for g in groups:
            cols: dict[int, list[str]] = defaultdict(list)
            for ln in g:
                for j, cell in enumerate(ln.split("│")):
                    if cell.strip():
                        cols[j].append(cell.strip())
            rendered.append(" | ".join("".join(v) if j <= 2 else " ".join(v) for j, v in sorted(cols.items())))
        out.append("\n".join(rendered))
        break
    return out


from .articles import attach as _attach_articles  # noqa: E402

_attach_articles(LawStore)

__all__ = ["LawStore", "Chunk", "LAW_NAME", "norm_key", "top_key", "clean", "box_table_rows", "is_form"]
