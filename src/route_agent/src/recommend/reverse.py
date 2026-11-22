"""가공업체 → 배출업체 역방향 추천 (3번 페이지 "가공업체 화면").

방법: 등록된 배출업체 폐기물(파트 1 case)마다 정방향 판정·매칭을 상위 제한 없이 돌리고,
가공·처분·구매 업체 기준으로 뒤집는다. 정방향과 같은 법령 규칙을 쓰므로
"A 업체가 B 폐기물을 받을 수 있다"는 결과가 양쪽에서 항상 같다.

순위: 거리(= 톤당 운송 탄소) 가까운 순 → 법적 상태(가능 → 조건부 → 확인 필요) → 월 물량 많은 순
  운송 탄소 합계로 정렬하면 물량이 많은 폐기물이 뒤로 밀리므로, 폐기물끼리는 톤당 기준(거리)으로 비교한다.
운송업체는 협력 확정 후 정하므로 색인하지 않는다.

  idx = ReverseIndex(pipeline)          # pipeline.Pipeline (업체 매칭 켠 상태)
  idx.add_cases(raw_cases)              # 파트 1 case 목록
  idx.recommend("엘림이엔|전주시 완산구")  # → 배출업체 폐기물 목록

CLI (src/route_agent/src 에서, pipeline.py가 pipline/ 폴더에 있으면 PYTHONPATH에 추가):
  PYTHONPATH=.:pipline $PY -m recommend.reverse <파트 1 파일> [<파트 1 파일> …] --name 엘림이엔 [--top 10]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from carbon import monthly_ton, transport, vehicle
from gates.models import Status

from . import PROCESSOR_ROLES, ROLE, STATUS, _kind

STATUS_ORDER = {"confirmed": 0, "conditional": 1, "needs_info": 2}


class ReverseIndex:
    def __init__(self, pipeline) -> None:
        if pipeline.partner_data is None:
            raise ValueError("업체 매칭을 켠 Pipeline이 필요합니다 (partners=True).")
        self.pipe = pipeline
        self.index: dict[str, list[dict]] = defaultdict(list)    # company_id → 배출업체 폐기물 항목
        self.companies: dict[str, dict] = {}                     # company_id → 업체 정보

    def add_cases(self, raws: list[dict]) -> None:
        from partners.matcher import match_routes
        for raw in raws:
            case, pre, routes, _ = self.pipe.judge(raw)
            ton, ton_basis = monthly_ton(case)
            veh = vehicle(case)
            region = raw.get("region") or {}
            for r, m in zip(routes, match_routes(routes, case, pre, self.pipe.partner_data, top_n=0)):
                if r.regulatory == Status.REJECTED:
                    continue
                for sm in m.steps:
                    if sm.role not in PROCESSOR_ROLES or sm.optional:
                        continue
                    for c in sm.candidates:
                        dist = c.location.distance_km if c.location else None
                        carbon = transport(dist, ton, veh, self.pipe.carbon_factors)
                        self.companies.setdefault(c.company_id, {
                            "company_id": c.company_id, "업체명": c.name, "지역": f"{c.sido} {c.sigungu}".strip(),
                            "주소": c.address, "연락처": c.phone})
                        self.index[c.company_id].append({
                            "case_id": case.waste_case_id,
                            "폐기물": raw.get("name"),
                            "분류번호": ", ".join(r.waste_codes),
                            "배출지역": f'{region.get("sido", "")} {region.get("sigungu", "")}'.strip(),
                            "배출업체": raw.get("emitter"),
                            "월물량_톤": ton if ton_basis.startswith("월 평균") else None,
                            "형태": raw.get("material_form"),
                            "처리": r.title,
                            "구분": _kind({"gate": r.gate, "tier": r.tier}),
                            "역할": ROLE.get(sm.role, sm.role),
                            "법적상태": STATUS.get(r.regulatory.label, r.regulatory.label),
                            "_status": STATUS_ORDER.get(r.regulatory.label, 9),
                            "거리_km": carbon["road_km"] if carbon else None,
                            "운송탄소_kgCO2eq_월": carbon["transport_kgco2e"] if carbon else None,
                            "확인할_것": [q["text"] for q in sm.questions],
                        })

    def find(self, name: str) -> list[dict]:
        """업체명 일부로 찾기 (색인에 있는 업체만)"""
        return [info for cid, info in self.companies.items() if name in info["업체명"] or name in cid]

    def recommend(self, company_id: str, top_n: int = 10) -> dict:
        best: dict[str, dict] = {}
        for e in self.index.get(company_id, []):          # 폐기물 하나에 경로가 여러 개면 법적 상태가 가장 좋은 것
            cur = best.get(e["case_id"])
            if cur is None or e["_status"] < cur["_status"]:
                best[e["case_id"]] = e
        ordered = sorted(best.values(), key=lambda e: (
            e["거리_km"] if e["거리_km"] is not None else float("inf"),
            e["_status"],
            -(e["월물량_톤"] or 0),
            e["case_id"]))
        items = [{"순위": i + 1, **{k: v for k, v in e.items() if not k.startswith("_")}}
                 for i, e in enumerate(ordered[:top_n] if top_n else ordered)]
        return {"가공업체": self.companies.get(company_id), "받을_수_있는_폐기물_수": len(best), "추천": items}


def _load_cases(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        out += d.get("cases", [d])
    return out


def main() -> None:
    from pipeline import Pipeline                         # PYTHONPATH에 pipeline.py 폴더 필요

    ap = argparse.ArgumentParser(description="가공업체 → 받을 수 있는 배출업체 폐기물 추천")
    ap.add_argument("inputs", nargs="+", help="파트 1 파일 (등록된 배출업체 폐기물)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--company", help="업체_키 (예: 엘림이엔|전주시 완산구)")
    g.add_argument("--name", help="업체명 일부")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    idx = ReverseIndex(Pipeline())
    idx.add_cases(_load_cases(args.inputs))
    ids = [args.company] if args.company else [c["company_id"] for c in idx.find(args.name)]
    if not ids:
        print(f"해당 업체가 받을 수 있는 폐기물이 없거나 업체를 찾지 못했습니다: {args.company or args.name}")
        return
    for cid in ids:
        rec = idx.recommend(cid, args.top)
        info = rec["가공업체"] or {"업체명": cid, "지역": ""}
        print(f"\n## {info['업체명']} ({info['지역']})  받을 수 있는 폐기물 {rec['받을_수_있는_폐기물_수']}건")
        for e in rec["추천"]:
            print(f"  {e['순위']}. {e['case_id']} {e['폐기물'] or ''} [{e['분류번호']}] {e['배출지역']} "
                  f"| {e['구분']} {e['법적상태']} | {e['거리_km']}km 운송 {e['운송탄소_kgCO2eq_월']}kgCO2eq/월"
                  + (f" | 월 {e['월물량_톤']}톤" if e["월물량_톤"] else ""))


if __name__ == "__main__":
    main()
