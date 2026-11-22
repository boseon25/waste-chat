"""route_agent 결과와 law_agent 결과를 경로 단위로 나란히 비교 (같은 사례).

  PYTHONPATH=src python3 -m law_agent.eval.compare_routes <route_agent 결과 폴더> <law_agent 결과 폴더> [--case ID]

경로는 route_id 의 종류 부분(관문·tier·투입 방식)으로 맞춘다: {case}-G{관문}-{종류}-{투입 방식}
  양쪽 모두   : 상태(regulatory)·업체 확보·1순위 업체·조건 수를 비교
  한쪽에만    : 그 경로가 왜 없는지 skipped 이유를 같이 보여준다
위험 표시(◀): route_agent 가 rejected·needs_info·conditional 인데 law_agent 가 confirmed (설계 문서 9절 '위험 오류').
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ORDER = {"rejected": 0, "needs_info": 1, "conditional": 2, "confirmed": 3}


def _routes(res: dict) -> dict[str, dict]:
    """route_proposals 가 있으면 그것, 없으면(--no-partners) routes 에 route_id 를 매겨서."""
    out = {}
    if not res.get("route_proposals") and res.get("routes"):
        from law_agent.core.proposal import assign_route_ids
        for r, rid in zip(res["routes"], assign_route_ids(res.get("case_id") or "", res["routes"])):
            out[rid] = {"title": r["title"], "status": r["regulatory"], "secured": (r.get("partners") or {}).get("secured"),
                        "first": None, "n_cond": len(r.get("conditions") or []), "n_docs": 0}
        return out
    for p in res.get("route_proposals") or []:
        out[p["route_id"]] = {"title": p["title"], "status": p["checks"]["regulatory"], "secured": p.get("secured"),
                              "first": (p.get("partner_candidates") or [{}])[0].get("name"),
                              "n_cond": len(p.get("conditions") or []), "n_docs": len(p.get("document_triggers") or [])}
    return out


def compare(ra: dict, la: dict) -> list[dict]:
    a, b = _routes(ra), _routes(la)
    rows = []
    for rid in sorted(set(a) | set(b)):
        x, y = a.get(rid), b.get(rid)
        danger = bool(x and y and y["status"] == "confirmed" and x["status"] != "confirmed")
        rows.append({"route_id": rid, "route_agent": x, "law_agent": y, "danger": danger,
                     "same_status": bool(x and y and x["status"] == y["status"])})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("route_agent_dir")
    ap.add_argument("law_agent_dir")
    ap.add_argument("--case")
    a = ap.parse_args(argv)
    total = same = danger = 0
    for lp in sorted(Path(a.law_agent_dir).glob("*.json")):
        cid = lp.stem
        if a.case and cid not in a.case.split(","):
            continue
        ra_dir = Path(a.route_agent_dir)
        if not (ra_dir / lp.name).exists() and (ra_dir / "cases" / lp.name).exists():
            ra_dir = ra_dir / "cases"                        # route_agent pipeline 은 {out}/cases/ 에 저장
        rp = ra_dir / lp.name
        if not rp.exists():
            print(f"■ {cid}: route_agent 결과 없음 ({rp})")
            continue
        ra, la = json.loads(rp.read_text(encoding="utf-8")), json.loads(lp.read_text(encoding="utf-8"))
        print(f"\n■ {cid}")
        print(f"   {'경로':44} {'route_agent':24} {'law_agent':24}")
        for r in compare(ra, la):
            fmt = lambda v: f"{v['status']:<11} {'확보' if v['secured'] else '미확보' if v['secured'] is False else '-':4} " \
                            f"{(v['first'] or '')[:8]}" if v else "(없음)"                          # noqa: E731
            kind = r["route_id"].split(f"{cid}-", 1)[-1]
            print(f"   {kind:44} {fmt(r['route_agent']):24} {fmt(r['law_agent']):24}{'  ◀ 위험' if r['danger'] else ''}")
            total += 1
            same += r["same_status"]
            danger += r["danger"]
        for side, res in (("route_agent", ra), ("law_agent", la)):
            for g, s in (res.get("skipped") or {}).items():
                print(f"   [{side}] {g} {s.get('action')}: {s.get('reason')}")
    if total:
        print(f"\n경로 {total}개: 상태 같음 {same} ({same / total:.0%}), 위험 {danger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
