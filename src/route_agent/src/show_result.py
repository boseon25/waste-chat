"""pipeline 결과를 단계별로 보기.

    python3 show_result.py                      # 가장 최근 결과 (../results/latest), 모든 사례
    python3 show_result.py ../results/20261117_112500
    python3 show_result.py --case DEMO-PART2-02   # 사례 하나
    python3 show_result.py --route DEMO-PART2-02-G4-incineration-alternative_treatment   # 경로 하나 자세히 (법 원문 포함)
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(folder: Path):
    files = sorted((folder / "cases").glob("*.json")) or sorted(folder.glob("*.json"))
    out = []
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(r, dict) and "case_id" in r and "route_proposals" in r:
            out.append(r)
    return out


def head(t):
    print(f"\n  [{t}]")


def show_case(r):
    pre = r.get("preliminary") or {}
    cls = pre.get("classification") or {}
    print("=" * 100)
    print(f"■ {r['case_id']} (v{r.get('case_version')})  {cls.get('normalized_waste_code')} {cls.get('waste_name') or ''}")

    head("1. 사전검증")
    print(f"   상태 {pre.get('status')}  /  지정폐기물 {(pre.get('hazardous') or {}).get('status')}"
          f"  /  재활용 금지·제한 {(pre.get('restriction') or {}).get('status')}"
          f"  /  혼합 {(pre.get('mixture') or {}).get('status')}")
    for g, p in (pre.get("gate_plan") or {}).items():
        print(f"   {g}: {p.get('action'):<5} {p.get('reason')}")
    for w in (pre.get("warnings") or [])[:5]:
        print(f"   · {w}")

    head("2. 관문 판정 → 경로")
    for p in r.get("route_proposals") or []:
        chk = p.get("checks") or {}
        print(f"   관문{p['gate']}  {chk.get('regulatory', ''):<12} {p['title'][:60]}")
        print(f"            {p['route_id']}")
    for g, s in (r.get("skipped") or {}).items():
        print(f"   {g} {s.get('action')}: {s.get('reason')}")

    head("3. 업체 매칭·탄소 (경로별 상위 3)")
    for p in r.get("route_proposals") or []:
        cands = p.get("partner_candidates") or []
        main = p.get("main_step_order")
        top = [c for c in cands if c.get("step_order") == main][:3] or cands[:3]
        mark = {True: "확보", False: "미확보", None: "-"}[p.get("secured")]
        print(f"   {p['title'][:40]:<40} [{mark}] 예비 {len(p.get('reserve_candidates') or [])}곳")
        for c in top:
            d = (c.get("distance_km") or {}).get("value")
            co = (c.get("carbon") or {}).get("transport_kgco2e")
            print(f"      {c.get('rank')}. {c.get('name')} ({c.get('permit_type')}) {d}km  운송탄소 {co} kgCO2eq/월")
        if p.get("alternatives"):
            a = p["alternatives"]
            print(f"      대안({a.get('status')}): {len(a.get('items') or [])}건 — {(a.get('summary') or '')[:80]}")

    head("4. 서류·법정 기준")
    seen = set()
    for p in r.get("route_proposals") or []:
        for t in p.get("document_triggers") or []:
            key = t.get("trigger")
            pc = t.get("plan_check")
            if key in seen and not pc:
                continue
            seen.add(key)
            extra = f" → {pc.get('decision')} ({pc.get('reason')})" if pc else ""
            print(f"   서류: {key}{extra}")
        for q in p.get("requirements") or []:
            print(f"   기준 [{p['title'][:16]}]: {q.get('item')} {q.get('decision')} ({q.get('reason')})")

    head("5. 법 근거 (경로별 개수)")
    for p in r.get("route_proposals") or []:
        rl = p.get("related_laws") or []
        bad = [l for l in rl if l.get("complete") is False]
        print(f"   {p['title'][:40]:<40} 관련 조문 {len(rl)}개, 판정 근거 {len(p.get('evidences') or [])}개"
              + (f", ⚠ 원문 빠짐 {len(bad)}개" if bad else ""))

    head("6. 파트 1에 되물을 것 (InformationRequest)")
    ir = r.get("information_request") or {}
    reqs = ir.get("requests") or []
    if not reqs:
        print("   없음")
    for q in reqs:
        print(f"   {q.get('request_id')} [{q.get('field')}] {(q.get('text') or '')[:90]}")


def show_route(r, route_id):
    p = next((x for x in r.get("route_proposals") or [] if x["route_id"] == route_id), None)
    if not p:
        return False
    print("=" * 100)
    print(f"■ {p['route_id']}\n  {p['title']}  관문{p['gate']}  checks={p.get('checks')}")
    for k, v in (p.get("check_reasons") or {}).items():
        print(f"    {k}: {v}")
    head("단계")
    for s in p.get("steps") or []:
        print(f"   {s['order']}. {s['process']} ({s['role']}) 코드 {s.get('treatment_codes')} {s.get('r_codes')}"
              + (" [선택]" if s.get("optional") else ""))
    head("조건")
    for c in p.get("conditions") or []:
        print(f"   · {c.get('text')}  ← {c.get('basis')}")
    head("업체에 물을 것")
    for q in p.get("questions") or []:
        print(f"   {q['question_id']} [{q['field']}] {q['text']}")
    head("판정 근거 (evidences)")
    for e in p.get("evidences") or []:
        print(f"   · {e.get('source')}  {e.get('ref')}")
        if e.get("text"):
            print(f"       {e['text'][:160].replace(chr(10), ' ')}")
    head("관련 조문 (related_laws)")
    for l in p.get("related_laws") or []:
        print(f"   [{l.get('stage')}] {l.get('name')} {l.get('title')}  {'완전' if l.get('complete') else '⚠ ' + str(l.get('problems'))}")
        print(f"       이유: {l.get('why')}")
        if l.get("text"):
            print(f"       원문: {l['text'][:200].replace(chr(10), ' ')} …")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=str(HERE.parent / "results" / "latest"))
    ap.add_argument("--case")
    ap.add_argument("--route")
    a = ap.parse_args()
    folder = Path(a.folder)
    info = folder / "run_info.json"
    if info.exists():
        ri = json.loads(info.read_text(encoding="utf-8"))
        ls = ri.get("law_status") or {}
        print(f"결과 폴더 {folder.resolve()}  실행 {ri.get('run_at')}  입력 {ri.get('input')}")
        print(f"옵션: agent={ri.get('agent')} feedback={ri.get('feedback')}  법령 상태: {ls.get('status')}"
              + (f"  ⚠ {ls['warning']}" if ls.get("warning") else ""))
    results = load(folder)
    if not results:
        print(f"결과가 없습니다: {folder}")
        return
    if a.route:
        if not any(show_route(r, a.route) for r in results):
            print(f"경로 없음: {a.route}")
        return
    for r in results:
        if not a.case or r["case_id"] == a.case:
            show_case(r)


if __name__ == "__main__":
    main()
