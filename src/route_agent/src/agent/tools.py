"""에이전트 도구 — 업체를 못 찾은 경로의 대안을 찾을 때 LLM이 고르는 도구들.

모든 도구는 규칙 코드(판정표·업체 매칭·법령 저장소)를 그대로 부른다. LLM은 어떤 도구를 어떤 값으로 부를지만 고른다.
- 처리방법 코드는 판정표가 허용한 것(allowed_methods)만 다시 매칭할 수 있다 → 법적으로 안 되는 방법은 시도조차 못 함
- finish로 낸 대안은 도구 결과에 실제로 나온 업체·경로·코드만 남기고 나머지는 버린다 (지어낸 업체 차단)
"""
from __future__ import annotations

import copy
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_SHOW = 3


@dataclass
class Ctx:
    """도구가 쓰는 실행 문맥 (경로 하나)"""
    index: int                      # route_dicts 안 순번
    routes: list                    # RouteDraft 목록 (규칙 판정 결과)
    route_dicts: list[dict]
    case: object
    pre: object
    partner_data: object
    data_dir: str
    law_store: object = None
    top_n: int = 5
    seen_partners: dict = field(default_factory=dict)     # 업체명 → {codes, distance}
    tried: list = field(default_factory=list)              # 다시 매칭해 본 코드 묶음
    laws_seen: set = field(default_factory=set)

    @property
    def route(self) -> dict:
        return self.route_dicts[self.index]


# ── 허용 처리방법 ────────────────────────────────────────────────────
def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _hit(code: str, sel: str) -> bool:
    return any(p and (code == p or code.startswith(p + "-")) for p in (sel or "").split(";"))


def allowed(ctx: Ctx) -> dict[str, dict]:
    """이 경로의 폐기물에 판정표가 허용한 처리방법 코드 → {이름, 근거}"""
    r = ctx.route
    names = {x["code"]: f'{x["처리분류"]} {x["세분류"]}'.strip()
             for x in _rows(Path(ctx.data_dir) / "treatment_code_map.csv")}
    out: dict[str, dict] = {}
    codes = r.get("waste_codes") or []
    if r["gate"] == 4:
        for row in _rows(Path(ctx.data_dir) / "disposal_methods_5.csv"):
            if row.get("action") not in ("REQUIRE", "ALLOW") or not row.get("처리방법_코드"):
                continue
            if row.get("적용_분류번호") and not any(_hit(c, row["적용_분류번호"]) for c in codes):
                continue
            if not row.get("적용_분류번호"):
                continue                                   # 분류번호 없는 일반 원칙 행은 제외
            for tc in row["처리방법_코드"].split(";"):
                out.setdefault(tc, {"name": names.get(tc, tc), "rule": row["rule_id"],
                                    "basis": row.get("근거_청크"), "text": row.get("처리방법_원문")})
    else:
        tops = {rc.rsplit("-", 1)[0] if rc.count("-") == 2 else rc
                for s in r.get("steps") or [] for rc in (s.get("r_codes") or [])}
        for x in _rows(Path(ctx.data_dir) / "treatment_code_map.csv"):
            if x["code"].startswith("2") and x.get("r_code") in tops:
                out[x["code"]] = {"name": names.get(x["code"]), "rule": f'R코드 {x["r_code"]}',
                                  "basis": x.get("근거"), "text": x.get("세분류")}
    return out


# ── 도구 ────────────────────────────────────────────────────────────
def route_info(ctx: Ctx) -> dict:
    r = ctx.route
    steps = []
    for s, ps in zip(r["steps"], (r.get("partners") or {}).get("steps") or [{}] * len(r["steps"])):
        steps.append({"order": s["order"], "role": s["role"], "process": s["process"],
                      "treatment_codes": s.get("treatment_codes") or [], "found": len(ps.get("candidates") or []),
                      "rejected": ps.get("rejected_summary") or {}})
    return {"title": r["title"], "gate": r["gate"], "waste_codes": r.get("waste_codes"),
            "waste_status": r.get("waste_status"), "regulatory": r.get("regulatory"), "steps": steps,
            "secured": (r.get("partners") or {}).get("secured")}


def allowed_methods(ctx: Ctx) -> dict:
    a = allowed(ctx)
    return {"allowed": [{"code": k, **v} for k, v in sorted(a.items())],
            "note": "rematch에는 이 목록의 코드만 쓸 수 있다 (판정표 기준)."}


def rematch(ctx: Ctx, treatment_codes: list[str]) -> dict:
    """주 처리 단계의 처리방법 코드를 바꿔 업체를 다시 찾는다."""
    from partners.matcher import match_routes
    a = allowed(ctx)
    bad = [c for c in treatment_codes if c not in a]
    if not treatment_codes or bad:
        return {"error": f"판정표가 허용하지 않은 코드: {bad or '없음'}. allowed_methods 목록에서 고르세요."}
    route = copy.deepcopy(ctx.routes[ctx.index])
    main = next((s for s in route.steps if s.role in ("treatment", "recycler", "preprocessor")), None)
    if main is None:
        return {"error": "처리 단계가 없는 경로"}
    main.treatment_codes = list(treatment_codes)
    m = match_routes([route], ctx.case, ctx.pre, ctx.partner_data, ctx.top_n)[0].to_dict()
    sm = next(s for s in m["steps"] if s["step_order"] == main.order)
    ctx.tried.append(list(treatment_codes))
    top = []
    for c in sm["candidates"][:MAX_SHOW]:
        dist = ((c.get("location") or {}).get("distance_km"))
        ctx.seen_partners[c["name"]] = {"codes": list(treatment_codes), "distance_km": dist,
                                        "company_id": c["company_id"], "permit_type": c.get("permit_type")}
        top.append({"name": c["name"], "distance_km": dist, "permit_type": c.get("permit_type"),
                    "treatment_codes": c.get("treatment_codes")})
    return {"treatment_codes": treatment_codes, "found": sm["total_found"], "top": top,
            "rejected": sm.get("rejected_summary") or {}}


def other_routes(ctx: Ctx) -> dict:
    out = []
    for i, r in enumerate(ctx.route_dicts):
        if i == ctx.index:
            continue
        p = r.get("partners") or {}
        main = next((s for s in p.get("steps") or [] if s["role"] != "transporter" and s.get("candidates")), None)
        out.append({"title": r["title"], "gate": r["gate"], "regulatory": r.get("regulatory"),
                    "secured": p.get("secured"),
                    "top_partner": main["candidates"][0]["name"] if main else None})
    return {"routes": out}


def search_law(ctx: Ctx, query: str) -> dict:
    if ctx.law_store is None:
        return {"error": "법령 저장소 없음 (WASTE_LAW_DIR)"}
    hits = ctx.law_store.find_articles(query, k=MAX_SHOW, laws=["WMA", "WMA_DEC", "WMA_RULE", "GUIDE_DECL"])
    out = []
    for h in hits:
        ctx.laws_seen.add(h["ref"])
        out.append({"ref": h["ref"], "name": h["name"], "title": h["title"], "text": (h["text"] or "")[:400]})
    return {"results": out}


# ── 도구 정의 (OpenAI 함수 형식) ─────────────────────────────────────
def _fn(name, desc, props=None, req=None):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []}}}


ALT = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["change_method", "other_route", "need_info"]},
    "treatment_codes": {"type": "array", "items": {"type": "string"}},
    "partners": {"type": "array", "items": {"type": "string"}, "description": "rematch 결과에 나온 업체명만"},
    "route_title": {"type": "string", "description": "other_routes에 나온 경로 제목만"},
    "law_refs": {"type": "array", "items": {"type": "string"}, "description": "search_law 결과의 ref만"},
    "reason": {"type": "string"}}, "required": ["kind", "reason"]}

SPECS = [
    _fn("route_info", "업체를 못 찾은 이 경로의 단계·처리방법 코드·업체를 뺀 이유를 본다."),
    _fn("allowed_methods", "이 폐기물에 판정표가 허용한 처리방법 코드 목록과 근거를 본다."),
    _fn("rematch", "주 처리 단계의 처리방법 코드를 바꿔 업체를 다시 찾는다. allowed_methods 코드만 가능.",
        {"treatment_codes": {"type": "array", "items": {"type": "string"}}}, ["treatment_codes"]),
    _fn("other_routes", "같은 폐기물의 다른 경로와 업체 확보 여부를 본다."),
    _fn("search_law", "법령 조문을 검색한다 (처리 방법이 법적으로 되는지 근거 확인).",
        {"query": {"type": "string"}}, ["query"]),
    _fn("finish", "조사를 끝내고 대안을 제출한다. 대안이 없으면 alternatives를 빈 배열로.",
        {"summary": {"type": "string"}, "alternatives": {"type": "array", "items": ALT}}, ["summary", "alternatives"]),
]

RUN = {"route_info": route_info, "allowed_methods": allowed_methods, "rematch": rematch,
       "other_routes": other_routes, "search_law": search_law}


def call(ctx: Ctx, name: str, args: dict) -> dict:
    fn = RUN.get(name)
    if fn is None:
        return {"error": f"없는 도구 {name}"}
    try:
        return fn(ctx, **(args or {}))
    except TypeError as e:
        return {"error": f"인자 오류: {e}"}


def verify(ctx: Ctx, alternatives: list[dict]) -> tuple[list[dict], list[str]]:
    """finish 결과 검증: 도구 결과에 실제로 나온 것만 남긴다."""
    titles = {r["title"] for i, r in enumerate(ctx.route_dicts) if i != ctx.index}
    allowed_codes = set(allowed(ctx))
    kept, dropped = [], []
    for a in alternatives or []:
        bad = []
        if any(p not in ctx.seen_partners for p in a.get("partners") or []):
            bad.append("rematch 결과에 없는 업체")
        if a.get("route_title") and a["route_title"] not in titles:
            bad.append("없는 경로")
        if any(c not in allowed_codes for c in a.get("treatment_codes") or []):
            bad.append("판정표가 허용하지 않은 코드")
        if any(r not in ctx.laws_seen for r in a.get("law_refs") or []):
            bad.append("검색하지 않은 조문")
        if bad:
            dropped.append(f'{a.get("kind")}: {", ".join(bad)}')
            continue
        a = dict(a)
        a["partners"] = [{"name": p, **ctx.seen_partners[p]} for p in a.get("partners") or []]
        kept.append(a)
    return kept, dropped


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:6000]
