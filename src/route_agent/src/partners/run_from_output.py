"""
관문 결과 JSON(pipeline.py 출력)으로 업체 매칭만 돌려 본다.

사용 (src/경로_매칭_에이전트/src 에서):
  WASTE_DATA_DIR=<프로젝트>/data/tables/active \
  WASTE_REFERENCE_DIR=<프로젝트>/data/reference \
  $PY -m partners.run_from_output <관문 결과 폴더 또는 파일> \
      [--part1 <파트 1 입력 파일>]  [--sido 전북 --sigungu 전주시]  [--out <폴더>]

- 관문 결과 JSON에는 배출 지역이 없다. --part1 을 주면 case_id로 찾아 region을 쓰고,
  없으면 --sido / --sigungu 값을 쓴다.
- --out 을 주면 {case_id}_partners.json 으로 저장한다. (routes + matches)
- 현재 매칭되는 역할: recycler, preprocessor. 나머지 역할은 빈칸(다음 단계).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gates.models import RouteDraft, Status, Step
from preliminary.models import WasteCase

from .data import PartnerData
from .matcher import match_routes

STATUS = {s.label: s for s in Status}
ROUTE_FIELDS = {"gate", "tier", "route_type", "title", "waste_codes", "waste_status", "reasons",
                "questions", "conditions", "required_tests", "r_code_detail", "evidence_ids", "basis_status"}


def route_from_dict(d: dict) -> RouteDraft:
    r = RouteDraft(**{k: v for k, v in d.items() if k in ROUTE_FIELDS},
                   steps=[Step(**s) for s in d["steps"]],
                   regulatory=STATUS[d["regulatory"]])
    return r


def load_outputs(path: Path) -> list[dict]:
    files = (sorted(path.glob("*.json")) + sorted(path.glob("cases/*.json"))) if path.is_dir() else [path]
    out = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "routes" in d and "case_id" in d:     # gate2_*.json 같은 다른 파일은 건너뜀
            out.append(d)
    return out


def regions_from_part1(path: Path | None) -> dict[str, dict]:
    if not path:
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    return {c.get("case_id"): (c.get("region") or {}) for c in d.get("cases", [d])}


def main() -> None:
    ap = argparse.ArgumentParser(description="관문 결과 JSON → 업체 매칭")
    ap.add_argument("input", help="pipeline.py --out 폴더 또는 결과 파일 1개")
    ap.add_argument("--part1", help="파트 1 입력 파일 (지역을 case_id로 찾음)")
    ap.add_argument("--sido", default="")
    ap.add_argument("--sigungu", default="")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", help="결과 저장 폴더")
    args = ap.parse_args()

    data = PartnerData.load()
    regions = regions_from_part1(Path(args.part1) if args.part1 else None)
    outdir = Path(args.out) if args.out else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    results = load_outputs(Path(args.input))
    if not results:
        print(f"관문 결과 파일 없음: {args.input}\n"
              "  case_id와 routes가 있는 JSON(pipeline.py 출력)만 읽습니다. 사전검증만 저장한 파일은 건너뜁니다.")
        return
    for res in results:
        cid = res["case_id"]
        reg = regions.get(cid) or {"sido": args.sido, "sigungu": args.sigungu}
        case = WasteCase(waste_case_id=cid)
        case.region = {"sido": reg.get("sido", ""), "sigungu": reg.get("sigungu", ""), "address": reg.get("address")}
        routes = [route_from_dict(r) for r in res["routes"]]
        matches = match_routes(routes, case, None, data, top_n=args.top)

        print(f"\n## {cid}  배출지 {case.region['sido']} {case.region['sigungu']}")
        for r, m in zip(routes, matches):
            sec = {True: "확보", False: "미확보", None: "-"}[m.secured]
            print(f"  [관문{r.gate} {r.regulatory.label}] {r.title}   업체={sec}")
            if r.regulatory == Status.REJECTED:
                print("     (불가 경로 → 매칭 안 함)")
            for s in m.steps:
                if not s.candidates and s.questions and s.questions[0]["field"] == "not_matched_yet":
                    print(f"     {s.step_order}. {s.role:12} (다음 단계에서 연결)")
                    continue
                print(f"     {s.step_order}. {s.role:12} 찾음 {s.total_found}  뺌 {s.rejected_summary}")
                for c in s.candidates:
                    km = f"{c.location.distance_km:.1f}km" if c.location and c.location.distance_km is not None else "-"
                    what = (f"{','.join(c.treatment_codes)} {','.join(c.r_codes)}".strip()
                            or (c.scope_text or c.waste_group or "")[:40])
                    print(f"        - {c.name} | {c.sido} {c.sigungu} | {c.permit_type} {what} | {km} ({c.location.basis})")
                for q in s.questions:
                    print(f"        ? {q['text']}")

        if outdir:
            path = outdir / f"{cid}_partners.json"
            path.write_text(json.dumps({"case_id": cid, "region": case.region, "routes": res["routes"],
                                        "matches": [m.to_dict() for m in matches]},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  → {path}")


if __name__ == "__main__":
    main()
