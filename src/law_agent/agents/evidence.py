"""③ 근거 확보 — 후보 조문을 원문 그대로 근거 묶음(EvidencePack)으로. 판단하지 않는다. 요약하지 않는다.

대상: schedule.ready_questions(state). 질문마다 묶음을 반드시 만든다 (후보가 없으면 빈 묶음 → verifier 가 low_retrieval).

후보 하나 → 근거 하나
  - 긴 별표의 행 조각("B4의3:2")       : 그 조각 원문                          (excerpt=True)
  - 긴 별표 본체("B4의3", 8천 자 초과) : to_items 의 세 단계 (코드 행 → 코드가 든 조각 → 관련 조각) (excerpt=True)
        코드 = 사례 분류번호와 그 상위 번호(51-03-01 → 51-03) + 성분 분류번호 + 앞 판단의 R코드(D-1·B-2)
        셋 다 못 찾으면 넣지 않고 truncated — 별표 앞부분을 잘라 넣으면 엉뚱한 근거가 되므로
  - 그 밖의 조문·짧은 별표·지침         : 조문 통째 (law_tools.full_article) + 호 개수 검증 (complete, problems)
  - 번호 이동(moved)이면 problems 에 기록

크기 제한: 1회차(좁게) narrow_max_chars_per_question, 재검색·J 질문(넓게) max_chars_per_question (schedule.is_wide).
  최소 조문 → 나머지 순서(retriever 순서 = 우선순위)로 채우고, 넘치는 근거는 빼고 truncated=True.
  최소 조문 하나가 혼자 한도를 넘으면(통째 지침 등) 그것도 빼고 truncated — 그 법령의 관련 조각은 검색이 채운다.
모든 근거 글은 law_tools.clean_law_text 로 정리한다 (개정 이력·머리글·괘선 줄 제거, 법 내용은 그대로).
마지막에 state.make_pack 이 (law, key) 순 정렬 + 해시.
"""
from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Any

from law_agent.agents.context import effective_facts
from law_agent.agents.schedule import is_wide, ready_questions
from law_agent.settings import load_settings
from law_agent.state import AgentState, Candidate, EvidenceItem, EvidencePack, Question, make_pack, make_ref
from law_agent.tools import law_tools
from law_agent.tools.law_tools import clean_law_text

NODE = "evidence"
RCODE_SOURCES = ("D-1", "B-2")      # 앞 판단에서 R코드를 가져올 질문 (부모·하위 질문 모두)
PART_BUDGET = 12000                 # 긴 별표 하나에서 조각으로 넣을 글자 합계
PART_MAX = 4                        # 긴 별표 하나에서 넣을 조각 수


@dataclass
class EvidenceConfig:
    max_chars: int = 30000                  # 넓게 (재검색·J 질문)
    narrow_max_chars: int = 6000            # 좁게 (1회차)

    @classmethod
    def from_settings(cls, settings: dict | None = None) -> "EvidenceConfig":
        e = (settings if settings is not None else load_settings()).get("evidence") or {}
        return cls(max_chars=int(e.get("max_chars_per_question", 30000)),
                   narrow_max_chars=int(e.get("narrow_max_chars_per_question", 6000)))


def code_ancestors(code: str) -> list[str]:
    """51-03-01 → [51-03-01, 51-03]. 대분류(51)는 넣지 않는다 (두 자리 숫자는 표 어디에나 걸림).
    끝이 -00 인 번호(04-02-00)는 그 자체가 중분류 묶음이라 04-02 도 같이."""
    parts = (code or "").strip().split("-")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return [code] if code else []
    out = []
    for n in range(len(parts), 1, -1):
        out.append("-".join(parts[:n]))
    return list(dict.fromkeys(out))


def _codes_of(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [v for k, v in value.items() if k in ("code", "waste_code") and isinstance(v, str)]
    return []


def excerpt_codes(q: Question, state: AgentState) -> list[str]:
    """긴 별표에서 꺼낼 행의 코드. 순서 고정."""
    case = state.get("case") or {}
    eff = effective_facts(state)                      # 판단 누적: A-1 이 확정한 분류번호를 먼저
    values = eff.get("values", {})
    waste: list[str] = []
    comps = [c for c in case.get("components") or [] if isinstance(c, dict)]
    if q.component and 0 < q.component <= len(comps) and comps[q.component - 1].get("waste_code"):
        waste.append(comps[q.component - 1]["waste_code"])            # 하위 질문: 그 성분 번호 먼저
    if values.get("waste_code"):
        waste.append(values["waste_code"])
    orig = ((state.get("facts") or {}).get("values") or {}).get("waste_code")
    if orig and orig != values.get("waste_code"):
        waste.append(orig)                            # 파트 1 번호도 뒤에 (A-1 이 고쳤어도 비교할 수 있게)
    for c in case.get("waste_code_candidates") or []:
        waste += _codes_of(c)
    if not q.component:
        waste += [c["waste_code"] for c in comps if c.get("waste_code")]
    codes = [a for w in waste for a in code_ancestors(w)]
    verdicts = state.get("verdicts") or {}
    for base in RCODE_SOURCES:                                         # 앞 판단의 R코드 (채택된 것만)
        for qid, v in sorted(verdicts.items()):
            if (qid == base or qid.startswith(base + ".c")) and v.status == "agreed":
                codes += [x for x in (v.value or {}).get("items") or [] if x.startswith("R-")]
    from law_agent.scope import in_scope_rcodes
    rc = in_scope_rcodes([c for c in codes if c.startswith("R-")])      # 별표에서 범위 안 R코드 부분만 꺼냄
    return list(dict.fromkeys([c for c in codes if not c.startswith("R-")] + rc))


def _part_item(ch: Any, c: Candidate, note: str = "") -> EvidenceItem:
    return EvidenceItem(ref=make_ref(ch.law, ch.key, ch.effective, ch.hash), law=ch.law, key=ch.key, title=ch.title,
                        effective=ch.effective, text=clean_law_text(ch.text), problems=[note] if note else [],
                        source=c.source, excerpt=True)


RX_COMMON = re.compile(r"^\s*\d{1,2}\.\s*공통\s*기준")


def _rtype_items(c: Candidate, codes: list[str], store: Any) -> list[EvidenceItem]:
    """'R코드 유형의 재활용 기준'으로 나뉜 긴 별표(5의3): 공통기준 + 사례 R코드 유형 부분만.
    (2026.11.20 세척액 R-10: 조각이 4만 자이고 본문은 R-10-1 이라 코드 검색·크기 한도에서 빠졌음)"""
    parts = sorted(store.children(c.law, c.key), key=lambda p: p.key)
    picked: list[EvidenceItem] = []
    for p in parts:                                   # 조각 하나에 유형 여럿이면 한 근거로 (ref 가 같아 묶음에서 겹침 제거됨)
        bs = law_tools.rtype_blocks(p.text, codes)
        if not bs:
            continue
        cut = any(b["cut"] for b in bs)
        it = _part_item(p, c, f"{', '.join(b['code'] for b in bs)} 유형 기준만" + (" (길어서 앞부분만)" if cut else ""))
        it.text = clean_law_text("\n\n".join(b["text"] for b in bs))
        it.complete = not cut
        picked.append(it)
    if not picked:
        return []
    common = [p for p in parts if RX_COMMON.match(p.text.lstrip())]
    return [_part_item(p, c, "공통기준") for p in common] + picked


R_PART_MIN_CHARS = 3000            # 이보다 긴 별표는 R코드가 든 호만 (별표 4의2 R코드 정의 6천 자)


def _section_items(c: Candidate, spec: Any, kinds: list[str], terms: list[str], budget: int,
                   store: Any, weak: list[str] | None = None) -> list[EvidenceItem]:
    """체크리스트가 호·목을 정한 긴 별표 (별표 5): 폐기물 구분별 호 › 목 › 관련 항목."""
    out = []
    for kind in kinds:
        kw = (spec.section or {}).get(kind)
        if not kw:
            continue
        r = law_tools.section_excerpt(c.law, c.key, kw, spec.subsection, terms, budget // max(1, len(kinds)),
                                      store=store, weak=weak)
        if r is None:
            continue
        it = _part_item(r["part"], c, f"호·목 발췌: {r['note']}")
        it.text = clean_law_text(r["text"])
        out.append(it)
    return out


def _rcode_parts(c: Candidate, codes: list[str], store: Any) -> list:
    """R코드 정의 별표(4의2)처럼 호마다 R코드가 나뉜 별표에서 사례 R코드(또는 그 상위)가 든 호만."""
    rcs = [x for x in codes if x.startswith("R-")]
    if not rcs:
        return []
    whole = store.get(c.law, c.key)
    if whole is None or len(whole.text) <= R_PART_MIN_CHARS:
        return []
    wanted = set(rcs) | {"-".join(r.split("-")[:2]) for r in rcs}
    return sorted((p for p in store.children(c.law, c.key)
                   if any(law_tools._code_rx(w).search(p.text) for w in wanted)), key=lambda p: p.key)


STOP_TERMS = {"무엇인가", "무엇", "있는가", "인가", "이", "그", "것", "폐기물", "방법", "허용된", "허용되는", "해당", "예", "원문",
              "적힌", "이름", "그대로", "쓴다", "items", "answer", "코드", "처리방법", "사실", "데이터", "법", "없으므로"}


def question_terms(q: Question) -> list[str]:
    """질문 문장의 말 (호·목 안 항목 고르기에서 약한 신호: 소각·매립·중간처분·파쇄·수분 …)."""
    words = re.findall(r"[가-힣]{2,}", q.text)
    return [w for w in dict.fromkeys(words) if w not in STOP_TERMS][:20]


def section_kinds(state: AgentState) -> list[str]:
    """별표 5 같은 '폐기물 구분별 호' 중 넣을 것. 모르면 일반·지정 둘 다 (덜 엄격한 쪽으로 빠지지 않게)."""
    values = effective_facts(state).get("values", {})
    code = str(values.get("waste_code") or "")
    status = values.get("waste_status")
    if code.startswith("10-"):
        return ["medical"]
    if status in ("designated", "designated_suspect", "suspect"):
        return ["designated"]
    if status == "general":
        return ["general"]
    return ["general", "designated"]


def section_terms(state: AgentState, codes: list[str]) -> list[str]:
    """항목 고르기 말: 분류번호 이름(폐합성수지류 → 합성수지)·폐기물 이름·성상. 질문 문장 말은 넣지 않는다 (모든 항목에 걸림)."""
    from law_agent.tools import fact_tools
    case = state.get("case") or {}
    words: list[str] = []
    for code in codes:
        if code.startswith("R-") or code.count("-") < 2:
            continue
        try:
            name = fact_tools.waste_code_name(code) or ""
        except Exception:                                          # 사실 표 없음 (테스트 등)
            name = ""
        words += re.findall(r"[가-힣A-Za-z]{2,}", re.sub(r"\([^)]*\)", "", name))
    words += re.findall(r"[가-힣A-Za-z]{2,}", str(case.get("name") or ""))
    out = []
    for w in words:
        out.append(w)
        core = re.sub(r"류$", "", re.sub(r"^폐", "", w))
        if len(core) >= 2 and core != w:
            out.append(core)
    return list(dict.fromkeys(out))


def to_items(c: Candidate, codes: list[str], queries: list[str], store: Any, spec: Any = None,
             kinds: list[str] | None = None, terms: list[str] | None = None,
             budget: int = 12000, weak: list[str] | None = None) -> tuple[list[EvidenceItem], str]:
    """후보 → (근거 목록, 메모). 넣을 수 없으면 ([], 이유).

    긴 별표 본체는 세 단계로:
      1. 괘선 표의 코드 행 (law_tools.excerpt_rows)                         → 별표 키 하나
      2. 코드가 든 조각 (괘선 표가 아닌 별표)                                → 조각마다 하나
      3. 사례와 관련도가 높은 조각 (분류번호 행이 없는 별표: 별표 5 처리 기준 등) → 조각마다 하나
    고시·지침 통째 문서("전체")가 8천 자를 넘으면 같은 법령의 조문·장 중 관련도가 높은 것만 (law_tools.relevant_sections).
    """
    if spec is not None and getattr(spec, "section", None) and law_tools.is_long_annex(c.law, c.key, store):
        items = _section_items(c, spec, kinds or ["general", "designated"], terms or [], budget, store, weak)
        if items:
            return items, ""
    if ":" in c.key and c.key.startswith("B"):                         # 검색에 걸린 긴 별표의 행 조각
        ch = store.get(c.law, c.key)
        return ([_part_item(ch, c)], "") if ch is not None else ([], f"{c.law}:{c.key} 조각 없음")
    if law_tools.is_long_annex(c.law, c.key, store):
        r = law_tools.excerpt_rows(c.law, c.key, codes, store=store)
        if r is None:
            return [], f"{c.law}:{c.key} 별표 없음"
        if r["text"]:
            problems = [f"이 별표에 행이 없는 코드: {r['missing']}"] if r["missing"] else []
            return [EvidenceItem(ref=r["ref"], law=c.law, key=c.key, title=r["title"], effective=r["effective"],
                                 text=clean_law_text(r["text"]), problems=problems, source=c.source, excerpt=True)], ""
        rtype = _rtype_items(c, codes, store)
        if rtype:
            return rtype, ""
        parts = sorted((p for p in store.children(c.law, c.key)
                        if any(law_tools._code_rx(code).search(p.text) for code in codes)), key=lambda p: p.key)
        if parts:
            items = []
            for p in parts[:PART_MAX]:
                # 줄 목록 별표(별표 4)면 코드가 속한 묶음 줄만 — 조각 통째(수천 자) 대신
                groups = [g for g in (law_tools.code_group_lines(p.text, code) for code in codes) if g]
                if groups:
                    text = "\n".join(dict.fromkeys("\n".join(groups).split("\n")))
                    it = _part_item(p, c, "코드가 속한 분류 묶음 줄만")
                    it.text = clean_law_text(text)
                    items.append(it)
                else:
                    items.append(_part_item(p, c, "코드가 든 조각"))
            return items, ""
        rel = law_tools.relevant_parts(c.law, c.key, queries, PART_BUDGET, PART_MAX, store=store)
        if rel:
            return [_part_item(p, c, "분류번호 행이 없는 별표 — 사례와 관련도가 높은 조각") for p in rel], ""
        return [], f"{c.law}:{c.key} 긴 별표에서 코드 {codes} 의 행·관련 조각을 찾지 못함"
    if c.key == "전체":                                                # 고시·지침 통째 문서
        whole = store.get(c.law, c.key)
        if whole is not None and len(whole.text) > law_tools.LONG_ANNEX_CHARS:
            secs = law_tools.relevant_sections(c.law, queries, PART_BUDGET, PART_MAX, store=store)
            if secs:
                return [_part_item(x, c, "통째 문서가 길어 사례와 관련도가 높은 조문·장만") for x in secs], ""
            return [], f"{c.law}:전체 통째 문서가 길고 관련 조문을 찾지 못함"
    rparts = _rcode_parts(c, codes, store) if c.key.startswith("B") and ":" not in c.key else []
    if rparts:
        return [_part_item(p, c, "사례 R코드가 든 호만") for p in rparts], ""
    a = law_tools.full_article(c.law, c.key, store=store)
    if a is None:
        return [], f"{c.law}:{c.key} 조문 없음"
    problems = list(a.get("problems") or [])
    if c.moved:
        problems.append("체크리스트 번호와 달라 제목으로 다시 찾은 조문")
    return [EvidenceItem(ref=a["ref"], law=c.law, key=a["key"], title=a.get("title") or c.title,
                         effective=str(a.get("effective") or ""), text=clean_law_text(a.get("text") or ""),
                         complete=bool(a.get("complete", True)), problems=problems, source=c.source)], ""


def to_item(c: Candidate, codes: list[str], store: Any, queries: list[str] | None = None) -> tuple[EvidenceItem | None, str]:
    """to_items 의 첫 근거만 (예전 함수 모양 — 테스트·점검용)."""
    items, note = to_items(c, codes, queries or [], store)
    return (items[0] if items else None), note


def evidence_queries(q: Question, state: AgentState) -> list[str]:
    """관련 조각을 고를 검색어: retriever 와 같은 검색어 + 폐기물 구분(지정/사업장일반)."""
    from law_agent.agents.retriever import build_queries
    eff = effective_facts(state)
    qs = build_queries(q, state.get("case") or {}, eff)
    status = (eff.get("values") or {}).get("waste_status")
    qs.append("지정폐기물" if status in ("designated", "designated_suspect") else "사업장일반폐기물")
    return qs


def build_pack(q: Question, cands: list[Candidate], codes: list[str], store: Any,
               cfg: EvidenceConfig, queries: list[str] | None = None, wide: bool = True,
               kinds: list[str] | None = None, terms: list[str] | None = None) -> tuple[EvidencePack, dict]:
    """근거 묶음. wide=False(1회차)면 한도 narrow_max_chars — 이때 검색·인용 근거를 한도로 빼는 것은
    설계상 의도이므로 truncated 로 표시하지 않는다 (최소 조문이 빠질 때만 truncated)."""
    limit = cfg.max_chars if wide else cfg.narrow_max_chars
    items: list[EvidenceItem] = []
    used = 0
    truncated = False
    notes: list[str] = []
    seen: set[str] = set()
    ordered = [c for c in cands if c.source == "min_laws"] + [c for c in cands if c.source != "min_laws"]
    specs = {(m.law, m.no): m for m in q.min_laws if m.section}
    sec_budget = min(int(limit * 0.85), PART_BUDGET)
    weak = question_terms(q)
    for c in ordered:
        spec = specs.get((c.law, c.key)) if c.source == "min_laws" else None
        got, note = to_items(c, codes, queries or [], store, spec, kinds, terms, sec_budget, weak)
        if not got:
            if wide or c.source == "min_laws":        # 좁게에서 인용·검색 근거를 못 넣은 건 잘림이 아님
                truncated = True
            notes.append(note)
            continue
        for item in got:
            if item.ref in seen:
                continue
            if used + len(item.text) > limit:
                if wide or c.source == "min_laws":
                    truncated = True
                notes.append(f"{item.law}:{item.key} 크기 한도로 뺌 ({len(item.text)}자)")
                continue
            seen.add(item.ref)
            items.append(item)
            used += len(item.text)
    pack = make_pack(q.id, items, truncated)
    summary = {"stage": "wide" if wide else "narrow", "limit": limit, "items": len(items), "chars": used,
               "truncated": truncated, "codes": codes, "notes": notes,
               "incomplete": [f"{i.law}:{i.key}" for i in items if not i.complete], "pack_hash": pack.pack_hash}
    return pack, summary


def node(state: AgentState, store: Any = None, cfg: EvidenceConfig | None = None) -> dict:
    """evidence 노드. 바뀐 칸: packs, audit."""
    cfg = cfg or EvidenceConfig.from_settings()
    store = store if store is not None else law_tools.get_store()
    cands = state.get("candidates") or {}
    retry_count = state.get("retry_count") or {}
    packs: dict[str, EvidencePack] = {}
    out_audit: dict[str, dict] = {}
    for q in ready_questions(state):
        codes = excerpt_codes(q, state)
        pack, summary = build_pack(q, cands.get(q.id, []), codes, store, cfg,
                                   evidence_queries(q, state), wide=is_wide(q, retry_count.get(q.id, 0)),
                                   kinds=section_kinds(state), terms=section_terms(state, codes))
        packs[q.id] = pack
        out_audit[q.id] = summary
    audit = {"node": NODE, "summary": {"questions": len(packs),
                                       "truncated": [k for k, p in packs.items() if p.truncated],
                                       "empty": [k for k, p in packs.items() if not p.items]},
             "output": out_audit}
    return {"packs": packs, "audit": [audit]}
