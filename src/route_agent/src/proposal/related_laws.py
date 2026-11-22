"""관련 조문 붙이기 (related_laws) — 경로마다 지켜야 할 법 조문을 원문과 함께 파트 3에 넘긴다.

판정(가능·불가, 업체 거르기)은 바꾸지 않는다. 모두 "확인 필요" 표시로만 붙인다.

어떻게 고르나
  1) 규칙 (source = "rule", status = "needs_review"): 경로 성격으로 정해지는 조문. 아래 RULES 표.
     조문은 번호 + 제목으로 적는다. 그 번호의 제목이 다르면 같은 법에서 제목으로 다시 찾는다
     (2026.11.12 시행규칙 개정: 위탁·수탁 기준 제16조의12 → 제16조의13 이동 사례).
  2) 검색 (source = "search", status = "search_candidate"): 폐기물 이름·경로 제목으로 RAG 검색 → 규칙에 없던 조문 최대 SEARCH_K개.
     "검색 후보" 표시. 사람이 확인하기 전에는 근거로 쓰지 않는다.
가져오는 법: 조문은 통째로 (lawstore.article), 항·호 개수 검증 결과(complete, problems)를 같이 넘긴다.
            별표는 길면(EXCERPT_LIMIT 초과) 원문 대신 호 목록과 ref만 넘긴다.
"""
from __future__ import annotations

from lawstore import clean, top_key

SEARCH_K = 2
SEARCH_LAWS = ["WMA", "WMA_DEC", "WMA_RULE", "GUIDE_DECL", "ALLBARO_NOTICE"]
EXCERPT_LIMIT = 6000
SEARCH_SKIP_TITLES = ("정의", "목적", "벌칙", "과태료", "수수료", "권한의 위임", "규제의 재검토")
STATUS_RULE = "needs_review"
STATUS_SEARCH = "search_candidate"

# (단계, 법령, 키, 제목, 왜, 조건 함수 이름)
RULES = [
    ("처리 기준", "WMA_DEC", "7", "폐기물의 처리기준 등",
     "폐기물을 처리(재활용·처분)할 때 지켜야 하는 기준", "waste"),
    ("배출자 신고", "WMA_RULE", "18", "사업장폐기물배출자의 신고",
     "일정량 이상 배출하는 사업장은 배출자 신고 대상", "waste"),
    ("배출자 확인", "WMA_RULE", "17의2", "사업장폐기물배출자의 확인 등",
     "위탁 전에 유해물질 함유 여부 등을 확인해야 하는 경우", "waste"),
    ("위탁·계약", "WMA_RULE", "16의13", "위탁ㆍ수탁의 기준 및 절차",
     "수탁처리능력 확인서, 적법 수탁자 확인, 서면 위탁계약, 3년 보관", "entrust"),
    ("운반", "WMA_RULE", "9", "폐기물 수집ㆍ운반업자 등의 운반기준",
     "운반 업체가 지켜야 하는 운반 기준", "transport"),
    ("인계 (전자정보처리)", "WMA", "45", "폐기물 인계ㆍ인수 내용 등의 전산 처리",
     "올바로시스템 전자인계", "handover"),
    ("인계 (전자정보처리)", "WMA_RULE", "20", "사업장폐기물의 인계ㆍ인수",
     "인계·인수 내용 입력 기한과 방법", "handover"),
    ("지정폐기물 처리계획", "WMA_RULE", "18의2", "지정폐기물 처리계획의 확인",
     "지정폐기물 배출자의 처리계획 확인 대상·서류·기한", "designated"),
    ("지정폐기물 분석", "WMA", "17의2", "폐기물분석전문기관의 지정",
     "지정폐기물 여부 분석은 폐기물분석전문기관에서", "designated_check"),
    ("재활용 기준", "WMA_RULE", "B5의3", "폐기물의 재활용 기준",
     "재활용 유형별 기준", "recycle"),
    ("재활용 준수사항", "WMA_RULE", "B5의4", "폐기물을 재활용하는 자의 준수사항",
     "재활용 업체가 지켜야 하는 사항", "recycle"),
    ("에너지 회수", "WMA_RULE", "3", "에너지 회수기준 등",
     "저위발열량·회수효율 등 에너지 회수로 인정되는 기준", "energy"),
    ("신고 업체", "WMA_RULE", "B17", "폐기물처리 신고자가 갖추어야 할 보관시설 및 재활용시설",
     "추천 업체 중 폐기물처리 신고 업체가 있을 때", "report_partner"),
    ("신고 업체", "WMA_RULE", "B17의2", "폐기물처리 신고자의 준수사항",
     "추천 업체 중 폐기물처리 신고 업체가 있을 때", "report_partner"),
]


# ── 조건 ─────────────────────────────────────────────────────────────
def _roles(route: dict) -> set[str]:
    return {s["role"] for s in route.get("steps") or []}


def _designated(route: dict) -> bool:
    if route.get("waste_status") == "designated":
        return True
    for c in route.get("waste_codes") or []:
        try:
            if int(c.split("-")[0]) < 50:
                return True
        except ValueError:
            pass
    return False


def _report_partner(route: dict) -> bool:
    for s in (route.get("partners") or {}).get("steps") or []:
        if any(c.get("permit_type") == "신고" for c in s.get("candidates") or []):
            return True
    return False


def _exempt_handover(route: dict, exempt_codes: set[str]) -> bool:
    codes = route.get("waste_codes") or []
    return bool(codes) and not _designated(route) and all(c in exempt_codes for c in codes)


def applies(cond: str, route: dict, exempt_codes: set[str] = frozenset()) -> bool:
    waste = route.get("gate") != 1                      # 관문 1 순환자원은 폐기물이 아님
    roles = _roles(route)
    return {
        "waste": waste,
        "entrust": waste and bool(roles & {"recycler", "preprocessor", "treatment"}),
        "transport": waste and "transporter" in roles,
        "handover": waste and not _exempt_handover(route, exempt_codes),
        "designated": waste and _designated(route),
        "designated_check": waste and (_designated(route) or "designated_analysis" in str(route.get("conditions"))),
        "recycle": waste and route.get("gate") == 2 and route.get("tier") != "energy_recovery",
        "energy": waste and route.get("tier") == "energy_recovery",
        "report_partner": waste and _report_partner(route),
    }.get(cond, False)


# ── 조문 찾기: 번호 + 제목 ────────────────────────────────────────────
def _norm_title(t: str) -> str:
    return "".join((t or "").split()).split("(")[0]


def locate(store, law: str, key: str, title: str) -> tuple[str | None, str | None]:
    """(실제 키, 메모). 번호의 제목이 다르면 같은 법에서 같은 제목 조문을 찾는다."""
    want = _norm_title(title)
    c = store.get(law, key)
    if c is not None and _norm_title(c.title).startswith(want):
        return key, None
    is_annex = key.startswith("B")
    for (l, k), ch in store.chunks.items():
        if l != law or ch.type != ("annex" if is_annex else "article"):
            continue
        if _norm_title(ch.title).startswith(want):
            return k, f"번호 이동: {key} → {k} (제목 '{title}' 기준)"
    if c is not None:
        return None, f"{key}의 제목이 '{c.title}'로 바뀜, 같은 제목 조문 없음"
    return None, "조문 없음"


def _entry(store, law: str, key: str, **meta) -> dict:
    a = store.article(law, key)
    if a is None:
        return {**meta, "law": law, "key": key, "ref": None, "name": None, "title": None,
                "text": None, "text_basis": "조문 없음", "complete": False, "problems": ["조문 없음"]}
    text, basis = clean(a["text"]), "조문 전체"
    out = {**meta, "law": law, "key": key, "ref": a["ref"], "name": a["name"], "title": a["title"],
           "effective": a["effective"], "complete": a["complete"], "problems": list(a["problems"])}
    if "parts" in a:                                    # 별표
        out["parts"] = [p["key"] for p in a["parts"]]
        if len(text) > EXCERPT_LIMIT:
            text, basis = None, f"별표가 길어 원문 생략 ({len(a['text'])}자) — ref로 조회"
    return {**out, "text": text, "text_basis": basis}


def related_laws(route: dict, store, waste_name: str = "", exempt_codes: set[str] = frozenset(),
                 evidence_refs: set[str] = frozenset(), search: bool = False) -> list[dict]:
    """경로 하나의 관련 조문 목록. store가 없으면 빈 목록.
    search=False (기본, 파트 3 출력용): 규칙으로 붙는 조문만.
    search=True: RAG 검색 후보만 (source = "search"). 잡음이 섞여 파트 3에는 보내지 않고 파트 2 내부 점검용으로 둔다."""
    if store is None:
        return []
    out, seen = [], set()
    for stage, law, key, title, why, cond in RULES:
        if not applies(cond, route, exempt_codes):
            continue
        real, note = locate(store, law, key, title)
        e = _entry(store, law, real or key, stage=stage, why=why, source="rule", status=STATUS_RULE)
        if note:
            e["problems"] = [note] + e["problems"]
            if real is None:
                e["complete"] = False
        if (law, real or key) in seen:
            continue
        seen.add((law, real or key))
        out.append(e)

    if not search:
        return out
    rule_keys = seen
    out = []
    seen = set(rule_keys)
    query = " ".join(x for x in (waste_name, route.get("title")) if x).strip()
    if query and route.get("gate") != 1:
        skip = set(seen) | {(r.split(":", 1)[0], top_key(r.split(":", 1)[1].split("@", 1)[0]))
                       for r in evidence_refs if ":" in r}
        for hit in store.find_articles(query, k=SEARCH_K + len(skip) + 5, laws=SEARCH_LAWS):
            if (hit["law"], hit["key"]) in skip or any(w in (hit["title"] or "") for w in SEARCH_SKIP_TITLES):
                continue
            e = _entry(store, hit["law"], hit["key"], stage="검색", source="search", status=STATUS_SEARCH,
                       why=f"'{query}' 검색 (점수 {hit['score']})")
            out.append(e)
            skip.add((hit["law"], hit["key"]))
            if sum(1 for x in out if x["source"] == "search") >= SEARCH_K:
                break
    return out


__all__ = ["related_laws", "RULES", "applies", "locate"]
