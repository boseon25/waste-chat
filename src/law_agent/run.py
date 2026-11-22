"""law_agent 실행: 파트 1 case → 입력 검사 → 판단(법률 매칭) → 경로 → 업체 매칭·순위·탄소 → RouteProposal.

  source env.sh   (그리고 타임리 키: set -a; . src/law_agent/llm/.env; set +a)
  PYTHONPATH=src python3 -m law_agent.run <파트 1 파일> --out <결과 폴더> [--case ID] [--no-partners] [--top 5]
                                         [--mode single|escalate|all] [--dry]

  --no-partners : 업체 매칭·탄소 없이 경로까지 (업체 표·중심점·탄소 계수 파일이 필요 없음)
  --dry         : LLM 을 부르지 않는다 (모든 판단이 '모름' → 흐름만 확인)

출력: --out 폴더에 case 마다 {case_id}.json
  case_id, case_version, engine
  input_check          입력 형식 검사 (형식이 틀리면 판단하지 않고 information_request 만)
  preliminary          판단 요약 (분류번호·지정 여부 — route_agent 결과와 같은 자리, 판정표가 아니라 A-1·A-2 판단)
  routes               관문 1·2·4 경로 + partners(업체 후보) + carbon_reference + laws(인용 조문) + law_questions
  skipped              만들지 않은 관문과 이유
  route_proposals      파트 3 RouteProposal v1.1 (+ engine, law_questions)
  information_request  파트 1에 되물을 것
  recommendation       화면용 추천 카드
  law_review           질문별 판단 (모델별 답·인용·상태)
  evidence_pack_ids    근거 묶음 해시 (재현용)
  run                  차례별 요약, 호출 수, 토큰, 시간, 판정표 예외 사용 기록
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from law_agent.agents.composer import compose
from law_agent.agents.context import effective_facts
from law_agent.graph import run_law          # settings llm.law_engine: langgraph(기본) | loop
from law_agent.core.input_adapter import InputAdapter
from law_agent.input_check import check_input
from law_agent.output import plain
from law_agent.output.proposal import (
    ENGINE, evidences, information_request, law_review, match_partners, summary_preliminary, to_route_proposals,
)
from law_agent.state import to_jsonable


def _bad_input_result(raw: dict, chk: dict) -> dict:
    reqs = [{"request_id": f"IR-{i + 1}", "field": it["field"], "text": it["text"], "to": "배출업체",
             "affected_routes": []} for i, it in enumerate(chk["issues"])]
    return {"case_id": raw.get("case_id"), "case_version": raw.get("case_version"), "engine": ENGINE,
            "input_check": chk, "routes": [], "skipped": {"all": {"action": chk["status"].upper(),
                                                                   "reason": "입력 형식 검사에서 멈춤"}},
            "route_proposals": [],
            "information_request": {"schema_version": "1.1", "case_id": raw.get("case_id"),
                                    "case_version": raw.get("case_version"), "requests": reqs,
                                    "fields": [r["field"] for r in reqs], "part2_internal": []},
            "law_review": [], "law_summary": [], "law_cards": None, "evidence_pack_ids": [], "run": {"calls": 0}}


def _say(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, flush=True)


def _round_printer(verbose: bool):
    if not verbose:
        return None

    def show(r: dict, st: Any) -> None:
        vs = st.get("verdicts") or {}
        tok = (r.get("usage") or {}).get("total_tokens", 0)
        print(f"   ─ 차례 {r['round']}: 질문 {r['ready']}개 (표 답 {r.get('table', 0)}), 호출 {r['calls']}회 (캐시 {r['cached']}, 3번째 모델 {r['escalated']}),"
              f" 토큰 {tok}, {r['seconds']}초", flush=True)
        for qid in r["ids"]:
            v = vs.get(qid)
            if v is None:
                continue
            val = v.value.get("items") if isinstance(v.value, dict) and v.value.get("items") else ""
            extra = f" 빠진 사실 {v.missing_facts}" if v.missing_facts else ""
            extra += f" 재검색 대기 ({'; '.join(v.retry_hint[:2])[:60]})" if v.status == "retry" else ""
            print(f"       {qid:8} {v.status:<14} {v.answer or '':<12} {str(val)[:50]}{extra}", flush=True)
    return show


def scope() -> dict:
    from law_agent.settings import load_settings
    return load_settings().get("scope") or {}


def apply_route_scope(composed: Any) -> Any:
    """settings scope.route_tiers 에 없는 경로는 빼고 skipped 에 이유를 남긴다 (비우면 그대로)."""
    tiers = scope().get("route_tiers") or []
    if not tiers:
        return composed
    keep_r, keep_u, out = [], [], []
    for r, u in zip(composed.routes, composed.used):
        if (r.tier or ("intermediate" if r.gate == 2 else None)) in tiers:   # 중간 가공(R-10) 경로는 tier 가 None
            keep_r.append(r)
            keep_u.append(u)
        else:
            out.append(f"관문{r.gate} {r.title}")
    if out:
        composed.skipped["scope"] = {"action": "SKIP", "reason": f"서비스 범위 밖 (중간재활용업만): {', '.join(out)}"}
    composed.routes, composed.used = keep_r, keep_u
    return composed


def write_audit(res: dict, st: Any) -> str | None:
    """노드 기록(st["audit"]) + 질문별 최종 판단 + 경로 요약을 감사 파일 한 개로 ({데이터}/audit/{case}/{시각}.jsonl).
    저장 위치가 없으면(LAW_AGENT_DATA_DIR 안 잡힘) 건너뛴다."""
    from law_agent.store.audit import AuditLog
    try:
        log = AuditLog(str(res.get("case_id")))
    except (KeyError, RuntimeError):
        return None
    for a in st.get("audit") or []:
        log.record(a.get("node", "?"), a.get("summary") or {}, a.get("output"), a.get("seconds"), a.get("question_id"))
    for x in res.get("law_review") or []:
        log.record("verdict", {k: x.get(k) for k in ("status", "answer", "missing_facts", "flags", "retries")},
                   output=x, question_id=x["question_id"])
    log.record("composer", {"routes": len(res.get("routes") or []), "skipped": res.get("skipped")},
               output=[{k: r.get(k) for k in ("gate", "tier", "title", "regulatory", "law_questions")}
                       for r in res.get("routes") or []])
    log.record("output", {"proposals": len(res.get("route_proposals") or []),
                          "requests": len((res.get("information_request") or {}).get("requests") or [])},
               output=res.get("run"))
    return str(log.path) if log.path else None


def load_partners(no_partners: bool = False) -> tuple[Any, Any]:
    """업체 표·탄소 계수 (범위의 등록형태만). no_partners 면 (None, None)."""
    if no_partners:
        return None, None
    from law_agent.core.carbon import Factors
    from law_agent.core.partners import PartnerData
    return PartnerData.load().only_registration(scope().get("recycler_registration") or []), Factors.load()


def use_dry_llm() -> None:
    """가짜 LLM (비용 없음, 캐시 안 씀). 배관 시험용."""
    import os
    os.environ["LAW_AGENT_NO_CACHE"] = "1"
    from law_agent.eval.smoke_judge import dry_llm
    from law_agent.llm import client
    client.chat_json = dry_llm


def run_case(raw: dict, partner_data: Any = None, factors: Any = None, top_n: int = 5,
             mode: str | None = None, verbose: bool = False, audit: bool = False) -> dict:
    """사례 하나 → 결과 dict. partner_data 가 None 이면 업체 매칭 없이 경로까지. verbose 면 진행 과정을 찍는다."""
    from law_agent.core.recommend import build_recommendation
    from law_agent.tools import fact_tools
    t0 = time.time()
    _say(verbose, f"\n▶ {raw.get('case_id')}  {raw.get('waste', {}).get('name') or raw.get('name') or ''}")
    chk = check_input(raw)
    _say(verbose, f" [1] 입력 검사: {chk['status']}" + (f" 경고 {len(chk['warnings'])}개" if chk.get("warnings") else ""))
    if chk["status"] != "ok":
        return _bad_input_result(raw, chk)
    fact_tools.EXCEPTION_USES.clear()
    fact_tools.TABLE_USES.clear()

    _say(verbose, " [2] 법률 매칭 (차례마다: 조문 찾기 → 근거 묶음 → 모델 판단 → 검증)")
    trace = None
    if verbose:                                                    # 노드마다 실시간. verbose 1 = 쉬운 말, 2 = 숫자
        def trace(line: str) -> None:
            print(line, flush=True)
        trace.friendly = int(verbose) < 2
    st, rounds = run_law(raw, mode, progress=_round_printer(int(verbose) >= 2), trace=trace)   # ① 법률 매칭 (판단)
    vs = st.get("verdicts") or {}
    wcase = InputAdapter().adapt(raw)
    eff = effective_facts(st)
    composed = apply_route_scope(compose(st, wcase))                  # ② 경로 (범위: 중간재활용업만)
    route_dicts = []
    for r, used in zip(composed.routes, composed.used):
        d = r.to_dict()
        d["law_questions"] = used
        d["laws"] = [{"law": e["law"], "article": e["article"], "key": e["ref"], "effective_date": e["effective_date"],
                      "used_for": e["used_for"]} for e in evidences(d, used, vs)]
        route_dicts.append(d)
    _say(verbose, f" [3] 경로 {len(route_dicts)}개, 만들지 않은 관문 {list(composed.skipped)}")
    if partner_data is not None:                                       # ③ 업체 매칭·순위·운송 탄소
        _say(verbose, " [4] 업체 매칭·순위·운송 탄소")
        match_partners(composed, route_dicts, wcase, partner_data, factors, top_n)

    result = {"case_id": wcase.waste_case_id, "case_version": wcase.case_version, "engine": ENGINE,
              "input_check": chk, "preliminary": summary_preliminary(st, eff), "routes": route_dicts,
              "skipped": composed.skipped}
    proposals = to_route_proposals(result, raw, wcase, vs, composed.used)     # ④ 파트 3
    result["route_proposals"] = proposals
    qtext = {q.id: q.text for q in st.get("questions") or []}
    result["information_request"] = information_request(result, proposals, vs, qtext)   # → 파트 1
    result["recommendation"] = build_recommendation(result, raw, wcase)               # → 화면
    result["law_review"] = law_review(st)
    result["law_summary"] = plain.attach(result["law_review"], eff, scope().get("r_codes") or [])          # 질문별 쉬운 요약
    result["law_cards"] = plain.cards(result["law_review"], eff, scope().get("r_codes") or [],
                                      plain.partner_count(route_dicts))                             # 사용자용 카드 3묶음
    result["evidence_pack_ids"] = sorted({p.pack_hash for p in (st.get("packs") or {}).values() if p.pack_hash})
    usage = sum((Counter(r["usage"]) for r in rounds), Counter())
    result["run"] = {"rounds": [{k: v for k, v in r.items() if k != "ids"} for r in rounds],
                     "calls": sum(r["calls"] for r in rounds), "usage": dict(usage),
                     "seconds": round(time.time() - t0, 1), "partners": partner_data is not None,
                     "exception_tables": list(fact_tools.EXCEPTION_USES),
                     "answer_tables": list(fact_tools.TABLE_USES),
                     "errors": len(st.get("errors") or [])}
    if audit:
        result["run"]["audit_file"] = write_audit(result, st)
        _say(verbose, f" 감사 기록: {result['run']['audit_file']}")
    return result


SECURED = {True: "업체 확보", False: "업체 미확보", None: "-"}


def print_result(res: dict, path: Path) -> None:
    try:
        _print_result(res, path)
    finally:
        sys.stdout.flush()                                  # | tee 로 받을 때도 바로 보이게


def _print_result(res: dict, path: Path) -> None:
    run = res.get("run") or {}
    print(f"\n■ {res['case_id']}  → {path}  (호출 {run.get('calls', 0)}회, 토큰 {run.get('usage', {}).get('total_tokens', 0)},"
          f" {run.get('seconds', 0)}초)")
    if res.get("input_check", {}).get("status") not in (None, "ok"):
        for it in res["input_check"]["issues"]:
            print(f"   입력 검사 {it['status']}: {it['text']}")
        return
    pre = res.get("preliminary") or {}
    print(f"   분류번호 {pre.get('classification', {}).get('normalized_waste_code')}  지정 {pre.get('waste_status')}")
    for r in res["routes"]:
        p = r.get("partners")
        tail = ""
        if p is not None:
            counts = [f"{s['role']} {len(s['candidates'])}/{s['total_found']}" for s in p["steps"]
                      if s["candidates"] or s["total_found"]]
            tail = f"   [{SECURED[p['secured']]}] " + ", ".join(counts)
            firsts = [s["candidates"][0] for s in p["steps"] if s["candidates"] and s["candidates"][0].get("carbon")]
            if firsts:
                c = firsts[-1]
                tail += f" | 1순위 {c['name'][:12]} {c['carbon']['road_km']}km 운송 {c['carbon']['transport_kgco2e']}kgCO2eq/월"
        print(f"   관문{r['gate']}  {r['regulatory']:<11} {r['title'][:40]}{tail}")
        if r.get("laws"):
            print("            법령: " + ", ".join(f"{l['law'].replace('폐기물관리법', '폐관법')} {l.get('article') or ''}"
                                               for l in r["laws"][:5] if l.get("law")))
    for g, s in (res.get("skipped") or {}).items():
        print(f"   {g} {s['action']}: {s['reason']}")
    ir = res.get("information_request") or {}
    if ir.get("requests"):
        print(f"   파트 1에 물을 것 {len(ir['requests'])}개: " + " / ".join(r["text"][:40] for r in ir["requests"][:4]))
    statuses = Counter(x["status"] for x in res.get("law_review") or [])
    print(f"   판단: {dict(statuses)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="law_agent: 파트 1 case → 법률 매칭 → 경로 → 업체 매칭 → RouteProposal")
    ap.add_argument("input")
    ap.add_argument("--out", default="data/law_agent/out")
    ap.add_argument("--case", help="이 case_id 만 (쉼표로 여러 개)")
    ap.add_argument("--no-partners", action="store_true")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--mode", choices=["single", "escalate", "all"])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--gate-only", action="store_true",
                    help="경로 상태에 쓰이는 질문만 (서류 안내 G-2·H-1~H-3 건너뜀). 평가·비교·비용 측정용")
    ap.add_argument("-v", "--verbose", action="count", default=0, help="실시간 로그 (-v 쉬운 말, -vv 노드·시간·호출 수)")
    a = ap.parse_args(argv)
    if a.gate_only:
        import os
        os.environ["LAW_AGENT_GATE_ONLY"] = "1"

    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    from law_agent.core import part1_v2
    if part1_v2.is_v2(data):                 # 파트 1 패키지(WasteCase 2.0) 출력이면 1.0 계약으로 바꿔서 판단
        data = part1_v2.convert(data)
        print(f"파트 1 2.0 입력 → 1.0 변환: {len(data['cases'])}건 (변환 결과는 --out 폴더 part1_converted.json)", flush=True)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "part1_converted.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    cases = data.get("cases") if isinstance(data, dict) and "cases" in data else [data]
    if a.case:
        want = set(a.case.split(","))
        cases = [c for c in cases if c.get("case_id") in want]
    if a.dry:
        use_dry_llm()                            # 가짜 답을 캐시에 쓰지 않게 (2026.11.18 dry 답이 실제 실행에 쓰였음)
    partner_data, factors = load_partners(a.no_partners)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for raw in cases:
        res = run_case(raw, partner_data, factors, a.top, a.mode, a.verbose, audit=True)
        path = out_dir / f"{res['case_id']}.json"
        path.write_text(json.dumps(to_jsonable(res), ensure_ascii=False, indent=1), encoding="utf-8")
        print_result(res, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
