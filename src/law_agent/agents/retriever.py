"""② 검색 — 질문마다 조문 후보를 세 갈래로 찾고 합친다. 판단하지 않는다.

대상: schedule.ready_questions(state) (이번 차례 + 재검색 대기)

  ① 지정   : 체크리스트 min_laws 를 law_tools.find_by_title 로 찾아, 원문과 '검증된' 것만 강제로 포함 (B안, 2026.11.17)
             검증 = 번호와 제목이 모두 맞음, 또는 번호가 옮겨졌지만 제목으로 하나만 찾아짐
             검증 안 됨(제목이 다름)·못 찾음 → 강제하지 않고 그 제목을 검색어로만 쓴다 (틀린 조문이 맨 앞에 들어가지 않게)
  ② 검색   : 조각(청크) 단위 BM25 (tools/bm25.py). 검색어를 나눠 따로 찾고 순위를 합친다 (RRF):
               질문 핵심어 / 분류번호 + 법정 명칭 / 폐기물 이름 / (하위 질문) 성분 이름·성분 분류번호 / (재검색) retry_hint
             찾은 조각은 조문으로 올린다 — 단 긴 별표(LONG_ANNEX_CHARS 초과)의 조각(행)은 그 조각 키 그대로 둔다
             (evidence 가 그 행만 넣게). 합친 점수(0~1)가 min_score 미만이면 버림
  ③ 인용   : 최소 조문에서 출발한 인용은 citation_depth 단계까지, 검색 조문에서 출발한 인용은 1단계만
             (검색이 엉뚱한 조문을 잡아도 그 인용이 자리를 차지하지 않게). 점수 = 출발 점수 × 0.5
  단계     : schedule.is_wide 가 거짓(1회차)이면 좁게 — 인용은 최소 조문에서 1단계만 + 같은 법 계열 안에서만
             (폐기물관리법 정의가 화학물질관리법 정의를 끌고 오지 않게), 검색은 상위 narrow_search_top 개만, 검색 출발 인용 없음. 참(재검색·J 질문)이면 위 설명대로 넓게.
  합치기   : (law, key) 중복 제거. min_laws 먼저(체크리스트 순서), 그다음 점수 높은 순, 같으면 (law, key) 순.
             최대 max_articles_per_question — 단 min_laws 는 넘쳐도 빼지 않는다.

부모 질문과 하위 질문은 ①과 그 인용이 같으므로 한 번 실행 안에서 한 번만 찾는다 (작업 안 캐시).
②는 질문마다(성분마다) 따로 찾는다 — 성분 이름으로만 걸리는 조문이 있어서.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from law_agent.agents.context import effective_facts
from law_agent.agents.schedule import is_wide, ready_questions
from law_agent.settings import load_settings
from law_agent.state import AgentState, Candidate, Question, error_record, parse_ref
from law_agent.tools import law_tools
from law_agent.tools.bm25 import rrf
from law_agent.core.lawstore import top_key

NODE = "retriever"
CITATION_DECAY = 0.5
CITATIONS_PER_SOURCE = 8            # 조문 하나가 가리키는 조문은 앞에서부터 이만큼만 (긴 별표가 수십 개를 가리킴)
SEARCH_POOL = 3                     # 검색은 max_articles × 이만큼 가져와서 조문 단위로 묶는다
REL_FLOOR = 0.2                     # 검색어마다 1위 점수의 이 비율 미만은 버림


@dataclass
class RetrieverConfig:
    max_articles: int = 12
    citation_depth: int = 2
    min_score: float = 0.15
    narrow_search_top: int = 3
    same_family_search: bool = True     # 검색 결과를 그 질문의 지정 조문과 같은 법 계열로만 (J·search 질문 제외)

    @classmethod
    def from_settings(cls, settings: dict | None = None) -> "RetrieverConfig":
        r = (settings if settings is not None else load_settings()).get("retrieval") or {}
        return cls(max_articles=int(r.get("max_articles_per_question", 12)),
                   citation_depth=int(r.get("citation_depth", 2)), min_score=float(r.get("min_score", 0.15)),
                   narrow_search_top=int(r.get("narrow_search_top", 3)),
                   same_family_search=bool(r.get("same_family_search", True)))


def law_family(law: str) -> str:
    """법 계열: WMA·WMA_DEC·WMA_RULE → WMA, CEA_DESIG → CEA (법·시행령·시행규칙·고시를 한 묶음으로)."""
    return law.split("_")[0]


def allowed_families(q: Question) -> set[str] | None:
    """검색 결과로 받을 법 계열. None = 제한 없음 (J 처럼 법 전체를 찾아야 하는 질문, 지정 조문이 없는 질문).
    2026.11.18: G-3(운반 기준)에 자원재활용법·전자제품법 조문 11개가 붙어 토큰만 늘었음."""
    if q.search or not q.min_laws:
        return None
    return {law_family(m.law) for m in q.min_laws}


def _code_name(code: str | None) -> str | None:
    """분류번호 이름 (사실 데이터). 표가 없거나 환경변수가 없으면 None — 검색어에서 빠질 뿐."""
    if not code:
        return None
    try:
        from law_agent.tools.fact_tools import waste_code_name
        return waste_code_name(code)
    except Exception:                                   # noqa: BLE001 — 조회 실패는 검색어만 줄어든다
        return None


RX_PAREN = re.compile(r"\([^)]*\)")
STOP = ("이 폐기물은", "이 폐기물", "무엇인가", "무엇이고", "있는가", "인가", "는가", "맞는가", "?")


def keywords(text: str) -> str:
    """질문 문장에서 괄호 안 보기·물음 말투를 뺀 핵심어."""
    t = RX_PAREN.sub(" ", text or "")
    for w in STOP:
        t = t.replace(w, " ")
    return re.sub(r"\s+", " ", t).strip()


def build_queries(q: Question, case: dict, facts: dict, hint: list[str] | None = None) -> list[str]:
    """검색어 목록 (따로 찾아 순위를 합침). 같은 입력이면 같은 목록, 빈 검색어는 뺌."""
    values = (facts or {}).get("values", {})
    code = values.get("waste_code")
    qs = [keywords(q.text), f"{code or ''} {_code_name(code) or ''}"]
    if not q.generic:                                  # generic: 사례 이름을 빼서 사례끼리 같은 근거 → 캐시 공유
        qs.append(str(case.get("name") or ""))
    if q.component:
        comps = case.get("components") or []
        comp = comps[q.component - 1] if 0 < q.component <= len(comps) and isinstance(comps[q.component - 1], dict) else {}
        qs.append(f"{comp.get('material') or ''} {comp.get('waste_code') or ''} {_code_name(comp.get('waste_code')) or ''}")
    if hint:
        qs.append(" ".join(hint))
    out = []
    for x in qs:
        x = re.sub(r"\s+", " ", x).strip()
        if x and x not in out:
            out.append(x)
    return out


def build_query(q: Question, case: dict, facts: dict, hint: list[str] | None = None) -> str:
    """감사 기록용 한 줄 (검색어 목록을 ' | ' 로)."""
    return " | ".join(build_queries(q, case, facts, hint))


class _Run:
    """노드 한 번 실행 동안의 캐시 (부모·하위 질문이 같이 씀)."""

    def __init__(self, store: Any, cfg: RetrieverConfig) -> None:
        self.store, self.cfg = store, cfg
        self.min_cache: dict[tuple[str, str, str], dict | None] = {}
        self.cite_cache: dict[tuple[str, str], list[dict]] = {}

    def find(self, law: str, no: str, title: str) -> dict | None:
        k = (law, no, title)
        if k not in self.min_cache:
            self.min_cache[k] = law_tools.find_by_title(law, no, title, store=self.store)
        return self.min_cache[k]

    def cites(self, law: str, key: str) -> list[dict]:
        k = (law, key)
        if k not in self.cite_cache:
            c = self.store.get(law, key)
            self.cite_cache[k] = (law_tools.resolve_citations(c.text, law, key, store=self.store)[:CITATIONS_PER_SOURCE]
                                  if c is not None else [])
        return self.cite_cache[k]


def _search(run: _Run, queries: list[str], retries: int) -> list[Candidate]:
    """검색어마다 BM25 → RRF 로 합침 → 조문(또는 긴 별표 행)으로 올림."""
    cfg = run.cfg
    k = cfg.max_articles * SEARCH_POOL * (1 + retries)
    lists = []
    for qtext in queries:
        hits = law_tools.search_chunks(qtext, k=k, store=run.store)
        if hits:
            floor = hits[0]["score"] * REL_FLOOR             # 그 검색어 1위의 REL_FLOOR 미만은 우연히 걸린 것으로 봄
            lists.append([(h["law"], h["key"]) for h in hits if h["score"] >= floor])
    best: dict[tuple[str, str], Candidate] = {}
    for (law, key), score in rrf(lists):
        if score < cfg.min_score:
            continue
        unit = key if (":" in key and law_tools.is_long_annex(law, key, run.store)) else top_key(key)
        if unit == "전체":
            continue
        u = (law, unit)
        if u not in best or score > best[u].score:
            c = run.store.get(law, unit)
            best[u] = Candidate(law=law, key=unit, title=(c.title if c else ""), score=score, source="search")
    return list(best.values())


def _top(law_key: tuple[str, str]) -> tuple[str, str]:
    law, key = law_key
    return law, key.split(":")[0].split("/")[0]


def stale_candidates(qid: str, state: AgentState) -> set[tuple[str, str]]:
    """재검색 때 뺄 이전 후보: 검색·인용으로 들어왔는데 어느 모델도 인용하지 않은 조문 (조문 단위).
    지정 조문(min_laws)과 인용된 조문은 남긴다 — 새 근거와 같이 봐야 답이 나오므로. (2026.11.17 데모 1 F-1:
    재검색에도 같은 엉뚱한 조문이 한도를 차지해 같은 실패를 반복)"""
    prev = (state.get("candidates") or {}).get(qid) or []
    quoted = set()
    for a in (state.get("answers") or {}).get(qid) or []:
        for qt in a.quotes or []:
            p = parse_ref(qt.get("ref") or "")
            if p:
                quoted.add(_top((p["law"], p["key"])))
    keep = quoted | {_top((c.law, c.key)) for c in prev if c.source == "min_laws"}   # 지정 조문의 조각(별표 행)도 남김
    return {_top((c.law, c.key)) for c in prev
            if c.source in ("search", "citation") and _top((c.law, c.key)) not in keep}


def retrieve(q: Question, case: dict, facts: dict, run: _Run, hint: list[str] | None = None,
             retries: int = 0, wide: bool | None = None,
             exclude: set[tuple[str, str]] | None = None) -> tuple[list[Candidate], dict]:
    """질문 하나 → (후보 목록, 감사 요약). wide=None 이면 schedule.is_wide(q, retries).
    exclude: 재검색 때 뺄 이전 후보 (stale_candidates). 지정 조문에는 적용하지 않는다."""
    wide = is_wide(q, retries) if wide is None else wide
    exclude = exclude or set()
    found: dict[tuple[str, str], Candidate] = {}
    order: list[tuple[str, str]] = []                 # min_laws 순서
    missing, mismatched = [], []
    title_queries: list[str] = []                     # 검증 안 된·못 찾은 최소 조문의 제목 → 검색어로만

    # ① 지정 (검증된 것만 강제)
    for m in q.min_laws:
        r = run.find(m.law, m.no, m.title)
        if r is None:
            missing.append(f"{m.law} {m.no or ''} {m.title}".replace("  ", " ").strip())
            title_queries.append(m.title)
            continue
        if not r["title_match"]:
            mismatched.append(f"{m.law}:{r['key']} (체크리스트 '{m.title}' / 원문 '{r['title']}')")
            title_queries.append(m.title)
            continue                                  # B안: 검증 안 된 조문은 강제하지 않음
        k = (r["law"], r["key"])
        if k not in found:
            found[k] = Candidate(law=r["law"], key=r["key"], title=r["title"], score=1.0, source="min_laws",
                                 moved=bool(r["moved"]))
            order.append(k)

    # ② 검색
    queries = build_queries(q, case, facts, hint)
    queries += [t for t in title_queries if t and t not in queries]
    searched = sorted(_search(run, queries, retries), key=lambda c: (-c.score, c.law, c.key))
    excluded = [c for c in searched if _top((c.law, c.key)) in exclude and (c.law, c.key) not in found]
    searched = [c for c in searched if c not in excluded]
    fam = allowed_families(q) if run.cfg.same_family_search else None
    other_family = [c for c in searched if fam is not None and law_family(c.law) not in fam]
    searched = [c for c in searched if c not in other_family]
    if not wide:
        searched = [c for c in searched if (c.law, c.key) not in found][: run.cfg.narrow_search_top]
    for c in searched:
        k = (c.law, c.key)
        if k not in found or (found[k].source != "min_laws" and c.score > found[k].score):
            found[k] = c

    # ③ 인용 따라가기 — 최소 조문 출발은 citation_depth 단계, 검색 출발은 1단계
    def follow(frontier: list[tuple[str, str, float]], depth: int, same_family: bool = False) -> None:
        for _ in range(depth):
            nxt = []
            for law, key, score in sorted(frontier, key=lambda x: (-x[2], x[0], x[1])):
                for cit in run.cites(law, key):
                    k = (cit["law"], cit["key"])
                    if k in found or _top(k) in exclude:
                        continue
                    if same_family and law_tools.law_family(k[0]) != law_tools.law_family(law):
                        continue                      # 좁게: 다른 법 정의 조문 등이 끌려오지 않게 (다른 법은 검색으로)
                    s_ = round(score * CITATION_DECAY, 4)
                    found[k] = Candidate(law=k[0], key=k[1], title=cit["title"], score=s_, source="citation",
                                         via=f"{law}:{key}")
                    nxt.append((k[0], k[1], s_))
            frontier = nxt

    follow([(found[k].law, found[k].key, found[k].score) for k in order], run.cfg.citation_depth if wide else 1,
           same_family=not wide)
    if wide:
        follow([(c.law, c.key, c.score) for c in list(found.values()) if c.source == "search"], 1)
    # 합치기
    mins = [found[k] for k in order]
    rest = sorted((c for k, c in found.items() if k not in order), key=lambda c: (-c.score, c.law, c.key))
    cands = mins + rest[: max(0, run.cfg.max_articles - len(mins))]
    summary = {"stage": "wide" if wide else "narrow", "query": " | ".join(queries), "min_laws": len(mins), "search": sum(c.source == "search" for c in cands),
               "citation": sum(c.source == "citation" for c in cands), "dropped": len(found) - len(cands),
               "moved": [f"{c.law}:{c.key}" for c in mins if c.moved], "missing_min_laws": missing,
               "title_mismatch": mismatched, "excluded_previous": sorted(f"{l}:{k}" for l, k in exclude),
               "other_family_dropped": len(other_family)}
    return cands, summary


def node(state: AgentState, store: Any = None, cfg: RetrieverConfig | None = None) -> dict:
    """retriever 노드. 바뀐 칸: candidates, audit, errors."""
    cfg = cfg or RetrieverConfig.from_settings()
    run = _Run(store if store is not None else law_tools.get_store(), cfg)
    case = state.get("case") or {}
    facts = effective_facts(state)                    # 판단 누적: A-1 이 확정한 분류번호로 검색
    verdicts = state.get("verdicts") or {}
    retry_count = state.get("retry_count") or {}

    candidates: dict[str, list[Candidate]] = {}
    out_audit: dict[str, dict] = {}
    errors: list[dict] = []
    for q in ready_questions(state):
        v = verdicts.get(q.id)
        hint = v.retry_hint if v is not None and v.status == "retry" else None
        exclude = stale_candidates(q.id, state) if hint is not None else set()
        cands, summary = retrieve(q, case, facts, run, hint, retry_count.get(q.id, 0), exclude=exclude)
        candidates[q.id] = cands
        out_audit[q.id] = {**summary, "candidates": [f"{c.source}:{c.law}:{c.key}:{c.score}" for c in cands]}
        for m in summary["missing_min_laws"]:
            errors.append(error_record(NODE, f"최소 조문을 찾지 못함: {m}", q.id))
    audit = {"node": NODE, "summary": {"questions": len(candidates),
                                       "empty": [qid for qid, c in candidates.items() if not c]},
             "output": out_audit}
    out: dict = {"candidates": candidates, "audit": [audit]}
    if errors:
        out["errors"] = errors
    return out
