"""
파트 2 실행: 파트 1 case → 사전검증 → 관문 1·2·4 → 경로 목록 → 업체 매칭 → 근거 법령.

사용 (src/경로_매칭_에이전트/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<시군구 중심점·탄소 계수 파일이 있는 폴더> \
  WASTE_LAW_DIR=<data/law/chunks>   (선택: 근거 원문 발췌) \
  $PY pipeline.py <파트 1 입력 JSON> --out <결과 폴더>

옵션:
  --no-partners   업체 매칭·탄소 없이 관문까지만 (WASTE_REFERENCE_DIR 필요 없음)
  --top N         단계마다 업체 상위 N개 (기본 5)
  --feedback F    업체 회신 JSON 파일 (PartnerFeedback 목록 또는 {"feedbacks": [...]})

출력: 결과 폴더(기본 route_agent/results/{실행시각}, results/latest = 최근)
  cases/{case_id}.json   case별 전체
  route_proposals.json   파트 3에 보낼 것 / information_requests.json  파트 1에 보낼 것
  recommendations.json   추천 카드 / run_info.json  실행 정보 (입력, 옵션, 판정표·법령 폴더)
  case별 JSON 모양:
  {
    "case_id", "case_version",
    "preliminary": 사전검증 결과,
    "routes": [ 관문 1·2·4 경로 (법 순서: 관문 1 → 2 → 4)
      { ...경로...,
        "partners": {"secured": true/false/null, "steps": [단계별 업체 후보, 뺀 이유, 질문]},
        "laws": [{"law", "article", "effective_date", "key", "used_for": [...], "text"}],
        "carbon_reference": 처리 탄소 (참고용, 경로끼리 비교하지 않음) }
      업체 후보마다 "carbon": 배출지 → 업체 운송 탄소 (직선 × 1.3 × 트럭/탱크로리 계수)
    ],
    "skipped": 실행하지 않은 관문과 이유
  }
  secured: true = 필수 단계마다 업체 있음 / false = 업체 없는 단계 있음 / null = 판단 안 함
관문 3(순환자원 개별 인정)은 쓰지 않는다 (2026-11-12 팀 결정: 활용처를 억지로 찾지 않음).
gates/gate3.py는 남겨 두었고, 다시 쓰려면 GATES에 넣으면 된다.
"recommendation": 화면용 추천 카드 (recommend/__init__.py). 처리 방법(경로)마다 추천 업체 순위.
"route_proposals": 파트 3에 보낼 RouteProposal 목록 (계약 v1.1, proposal/). 경로마다 1개
"information_request": 배출업체에 되물을 것 (파트 1 방향)
--feedback <회신 JSON>: 업체 회신(PartnerFeedback 목록)을 반영한 route_proposals
법령 원문 발췌(laws[].text)는 다음 단계.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from gates import gate1, gate2, gate4
from carbon import Factors, attach_carbon
from laws import attach_laws
from law_tables import designated_check, energy
from proposal import apply_feedback, assign_route_ids, build_information_request, to_route_proposals
from recommend import build_recommendation
from gates.tables import Tables
from preliminary.models import GateAction
from preliminary.preliminary_integrator import PreliminaryIntegrator

GATES = [("gate1", gate1), ("gate2", gate2), ("gate4", gate4)]   # 출력 순서 = 법 순서. 관문 3은 제외


class Pipeline:
    """명세서(파트 2 개발 명세 v1.0)의 함수 4개 단위로 나눈다. LangGraph를 쓰면 각 메서드가 노드가 된다.
      search_routes  → validate_routes → find_partners → compare_routes → 출력(proposal 등)
    """

    def __init__(self, data_dir: str | Path | None = None, partners: bool = True, top_n: int = 5,
                 agent: bool = False) -> None:
        data_dir = data_dir or os.environ["WASTE_DATA_DIR"]
        self.agent = agent                                # 업체 미확보 경로 대안 찾기 (LLM 에이전트). 기본 꺼짐
        self.data_dir = str(data_dir)
        self.integrator = PreliminaryIntegrator(data_dir)
        self.tables = Tables.load(data_dir)
        self.top_n = top_n
        self.partner_data = None
        if partners:
            from partners import PartnerData          # 매칭을 끌 때는 업체 표·중심점 파일을 읽지 않는다
            self.partner_data = PartnerData.load(tables_dir=data_dir)
            self.carbon_factors = Factors.load()          # 탄소 계수 파일 (WASTE_REFERENCE_DIR)
        self.law_store = None                             # 법령 조문 (WASTE_LAW_DIR = data/law/chunks). 있으면 근거 원문 발췌
        if os.environ.get("WASTE_LAW_DIR"):
            from lawstore import LawStore
            self.law_store = LawStore.load(os.environ["WASTE_LAW_DIR"])

    # ── 1. search_routes: 사전검증 + 관문 1·2·4 ──
    def search_routes(self, raw_case: dict):
        """파트 1 case → (WasteCase, 사전검증 결과, 경로 목록, 실행 안 한 관문). 역방향 추천도 이걸 쓴다."""
        case = self.integrator.input_adapter.adapt(raw_case)
        pre = self.integrator.run(case)

        routes, skipped = [], {}
        for name, gate in GATES:                                 # RUN인 관문만
            item = pre.gate_plan[name]
            if item.action == GateAction.RUN:
                found = gate.run(case, pre, self.tables)
                routes += found
                if not found:
                    skipped[name] = {"action": "RUN", "reason": "실행했지만 해당 경로 없음"}
            else:
                skipped[name] = {"action": item.action.value, "reason": item.reason}
        return case, pre, routes, skipped

    judge = search_routes                                        # 예전 이름 (역방향 추천에서 사용)

    # ── 2. validate_routes: 근거 법령 (checks 세 칸은 proposal 변환에서) ──
    def validate_routes(self, routes) -> list[dict]:
        return [attach_laws(r.to_dict()) for r in routes]

    # ── 3. find_partners: 역할별 업체 매칭 (상위 N + 예비 RESERVE_N) ──
    RESERVE_N = 5

    def find_partners(self, routes, route_dicts: list[dict], case, pre,
                      exclude: dict[str, set[str]] | None = None) -> None:
        """exclude: {route_id: 뺄 업체_키} — 회신에서 거절한 업체를 빼고 다시 매칭"""
        from partners.matcher import match_routes
        ids = assign_route_ids(case.waste_case_id, route_dicts)
        ex_idx = {i: exclude[rid] for i, rid in enumerate(ids) if exclude and rid in exclude}
        matches = match_routes(routes, case, pre, self.partner_data, self.top_n + self.RESERVE_N, ex_idx)
        for d, m in zip(route_dicts, matches):
            m_dict = m.to_dict()
            for s in m_dict["steps"]:
                s["reserve"] = s["candidates"][self.top_n:]
                s["candidates"] = s["candidates"][:self.top_n]
            d["partners"] = {"secured": m_dict["secured"], "steps": m_dict["steps"]}

    # ── 4. compare_routes: 운송 탄소 (처리 탄소는 참고), 비용은 회신 후 ──
    def compare_routes(self, route_dicts: list[dict], case) -> None:
        for d in route_dicts:
            attach_carbon(d, case, self.carbon_factors)

    # ── 한 번에 돌리기 ──
    def run_case(self, raw_case: dict, feedbacks: list[dict] | None = None) -> dict:
        case, pre, routes, skipped = self.search_routes(raw_case)
        route_dicts = self.validate_routes(routes)
        er_rows = energy.load(self.data_dir)                 # 에너지 회수기준 (시행규칙 제3조) → 요건·질문
        for d in route_dicts:
            d["requirements"] = energy.requirements(d, case, er_rows)
            if d["requirements"]:
                d["questions"] = (d.get("questions") or []) + energy.emitter_questions(d["requirements"])
        exclude: dict[str, set[str]] = {}
        for fb in feedbacks or []:                       # 거절한 업체는 매칭에서 빼고 다음 업체로 채움
            if fb.get("acceptance") == "rejected" and fb.get("route_id") and fb.get("company_id"):
                exclude.setdefault(fb["route_id"], set()).add(fb["company_id"])
        if self.partner_data is not None:
            self.find_partners(routes, route_dicts, case, pre, exclude)
            self.compare_routes(route_dicts, case)
            if self.agent:                                # 5. 빈칸 채우기: 미확보 경로 대안 (검색 후보, 판정은 그대로)
                from agent import find_alternatives
                find_alternatives(self, case, pre, routes, route_dicts)

        result = {
            "case_id": case.waste_case_id,
            "case_version": case.case_version,
            "preliminary": pre.to_dict(),
            "routes": route_dicts,
            "skipped": skipped,
        }
        if self.partner_data is not None:
            proposals = to_route_proposals(result, raw_case, case, self.data_dir, self.law_store)   # → 파트 3
            if feedbacks:
                proposals = apply_feedback(proposals, feedbacks)
            result["route_proposals"] = proposals
            ir = build_information_request(result, proposals)                                  # → 파트 1
            dc = designated_check.request(case, route_dicts, designated_check.load(self.data_dir),
                                          [p["route_id"] for p in proposals])
            if dc and not any(r["field"] == "designated_check" for r in ir["requests"]):
                ir["requests"].append({"request_id": f"IR-{len(ir['requests']) + 1}", **dc})
                ir["fields"].append("designated_check")
            result["information_request"] = ir
            result["recommendation"] = build_recommendation(result, raw_case, case)  # → 화면
        return result


SECURED = {True: "업체 확보", False: "업체 미확보", None: "-"}


def print_result(result: dict, path: Path) -> None:
    print(f"{result['case_id']}  사전검증 {result['preliminary']['status']}  → {path}")
    for r in result["routes"]:
        p = r.get("partners")
        tail = ""
        if p is not None:
            counts = [f"{s['role']} {len(s['candidates'])}/{s['total_found']}"
                      for s in p["steps"] if s["candidates"] or s["total_found"]]
            tail = f"   [{SECURED[p['secured']]}] " + ", ".join(counts)
            firsts = [s["candidates"][0] for s in p["steps"] if s["candidates"] and s["candidates"][0].get("carbon")]
            if firsts:
                c = firsts[-1]
                tail += f" | 1순위 {c['name'][:12]} {c['carbon']['road_km']}km 운송 {c['carbon']['transport_kgco2e']}kgCO2eq/월"
        laws = ", ".join(f"{l['law'].replace('폐기물관리법', '폐관법')} {l['article']}" for l in r.get("laws", []) if l["law"])
        print(f"   관문{r['gate']}  {r['regulatory']:<11} {r['title'][:40]}{tail}")
        if laws:
            print(f"            법령: {laws}")
    for g, s in result["skipped"].items():
        print(f"   {g} {s['action']}: {s['reason']}")


def law_status(max_days: int = 14) -> dict:
    """law_api update.py 가 남긴 상태(data/law/update_status.json)로 판정표가 최신 법인지 확인.
    반환: {"status": ok | pending | stale | unknown, "warning": 문장 또는 None, ...}"""
    from datetime import datetime
    law_dir = os.environ.get("WASTE_LAW_DIR")
    if not law_dir:
        return {"status": "unknown", "warning": None}
    p = Path(law_dir)
    p = (p.parent if p.name in ("chunks", "chunk_v2") else p) / "update_status.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        days = (datetime.now() - datetime.fromisoformat(st["last_check"])).days
    except (OSError, ValueError, KeyError):
        return {"status": "unknown", "warning": "법령 개정 확인 기록이 없습니다 (law_api update.py 를 실행하세요)"}
    out = {"last_check": st["last_check"], "days_since_check": days, "pending": st.get("pending") or []}
    if out["pending"]:
        return {**out, "status": "pending",
                "warning": f"법령 개정이 판정표에 아직 반영되지 않았습니다: {', '.join(out['pending'])} "
                           f"(보고서 {st.get('report')}). 지금 결과는 개정 전 법 기준입니다"}
    if days > max_days or not st.get("check_ok", True):
        return {**out, "status": "stale",
                "warning": f"법령 개정 확인이 {days}일 전입니다" + ("" if st.get("check_ok", True) else " (마지막 확인에 오류)")}
    return {**out, "status": "ok", "warning": None}


# ── 결과 폴더 ────────────────────────────────────────────────────────
def default_out_dir() -> Path:
    """WASTE_RESULTS_DIR → 없으면 route_agent/results (이 파일 기준) / {YYYYMMDD_HHMMSS}"""
    from datetime import datetime
    base = os.environ.get("WASTE_RESULTS_DIR")
    if not base:
        here = Path(__file__).resolve()
        ra = next((p for p in here.parents if p.name == "route_agent"), None)
        base = str(ra / "results") if ra else "out"
    return Path(base) / datetime.now().strftime("%Y%m%d_%H%M%S")


def write_bundle(out: Path, results: list[dict], args) -> None:
    """받는 곳별로 나눠 저장 + 실행 정보. results/latest 가 가장 최근 실행을 가리킨다."""
    from datetime import datetime
    def dump(name, obj):
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    dump("route_proposals.json", [p for r in results for p in r.get("route_proposals") or []])     # → 파트 3
    dump("information_requests.json", [r.get("information_request") for r in results])           # → 파트 1
    dump("recommendations.json", [r.get("recommendation") for r in results])                      # 화면 추천 카드
    dump("run_info.json", {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(Path(args.input).resolve()),
        "feedback": args.feedback, "agent": bool(args.agent), "partners": not args.no_partners, "top": args.top,
        "data_dir": args.data_dir or os.environ.get("WASTE_DATA_DIR"),
        "law_dir": os.environ.get("WASTE_LAW_DIR"),
        "law_status": law_status(),
        "cases": [{"case_id": r["case_id"], "case_version": r.get("case_version"),
                   "routes": len(r.get("route_proposals") or [])} for r in results],
    })
    latest = out.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(out.name)
    except OSError:
        pass
    print(f"\n결과 폴더: {out}  (파트 3: route_proposals.json / 파트 1: information_requests.json)")


def main() -> None:
    ap = argparse.ArgumentParser(description="파트 1 case 파일 → 사전검증 → 관문 1·2·4 → 업체 매칭 → 근거 법령")
    ap.add_argument("input", help="파트 1 파일 (cases 배열이 있는 JSON 또는 case 1건 JSON)")
    ap.add_argument("--out", default=None,
                    help="결과 폴더 (기본: route_agent/results/{실행시각}, WASTE_RESULTS_DIR 로 바꿀 수 있음)")
    ap.add_argument("--data-dir", default=None, help="판정표 폴더 (기본: WASTE_DATA_DIR)")
    ap.add_argument("--no-partners", action="store_true", help="업체 매칭 없이 관문까지만")
    ap.add_argument("--top", type=int, default=5, help="단계마다 업체 상위 N개")
    ap.add_argument("--feedback", default=None, help="업체 회신 JSON (PartnerFeedback 목록)")
    ap.add_argument("--agent", action="store_true",
                    help="업체 미확보 경로를 LLM 에이전트로 대안 조사 (TIMELY_API_KEY·TIMELY_BASE_URL, 테스트는 AGENT_MOCK=1)")
    args = ap.parse_args()

    feedbacks = None
    if args.feedback:
        fb = json.loads(Path(args.feedback).read_text(encoding="utf-8"))
        feedbacks = fb.get("feedbacks", []) if isinstance(fb, dict) else fb

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    cases = data.get("cases", [data])
    out = Path(args.out) if args.out else default_out_dir()
    (out / "cases").mkdir(parents=True, exist_ok=True)

    ls = law_status()
    if ls.get("warning"):
        print(f"⚠️  {ls['warning']}\n")
    pipe = Pipeline(args.data_dir, partners=not args.no_partners, top_n=args.top, agent=args.agent)
    results = []
    for raw in cases:
        result = pipe.run_case(raw, feedbacks)
        path = out / "cases" / f"{result['case_id']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print_result(result, path)
        results.append(result)
    write_bundle(out, results, args)


if __name__ == "__main__":
    main()
