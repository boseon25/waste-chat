"""저장된 결과 JSON 보기 (LLM 안 씀, 다시 실행하지 않음).

  PYTHONPATH=src python3 -m law_agent.eval.show_result data/law_agent/out/DEMO-PART2-01.json            # 전체 요약
  PYTHONPATH=src python3 -m law_agent.eval.show_result data/law_agent/out/DEMO-PART2-01.json --q D-2,F-1  # 질문 자세히
  PYTHONPATH=src python3 -m law_agent.eval.show_result data/law_agent/out/DEMO-PART2-01.json --routes     # 경로·서류·업체 자세히

보여 주는 것
  [차례]   차례마다 질문 수·호출·토큰·시간·상태
  [판단]   질문마다 최종 상태·답 (--q 면: 근거 조문 목록, 모델별 답·이유·빠진 사실·근거 부족, 인용)
  [경로]   관문·상태·단계·조건·필요 시험·근거 법령 (--routes 면: 서류, 업체 후보, 탄소)
  [파트 1에 물을 것]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _short(v, n=60) -> str:
    if isinstance(v, dict) and "items" in v:
        v = v["items"]
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


def show_rounds(res: dict) -> None:
    run = res.get("run") or {}
    print(f"[차례] 호출 {run.get('calls')}회, 토큰 {(run.get('usage') or {}).get('total_tokens')}, {run.get('seconds')}초,"
          f" 오류 {run.get('errors')}, 판정표 예외 {len(run.get('exception_tables') or [])}회")
    for r in run.get("rounds") or []:
        print(f"   차례 {r['round']}: 질문 {r['ready']}개, 호출 {r['calls']} (캐시 {r['cached']}, 3번째 모델 {r['escalated']}),"
              f" {r['seconds']}초  {r.get('statuses')}")


STATUS_KO = {"agreed": "확정", "not_applicable": "해당 없음(안 물음)", "needs_info": "정보 필요(파트 1)",
             "needs_human": "사람 확인", "retry": "재검색", "pending": "대기"}
ANSWER_KO = {"yes": "예", "no": "아니오", "conditional": "조건부", "unknown": "-"}


def _law_name(law: str, key: str) -> str:
    from law_agent.core.lawstore import LAW_NAME
    from law_agent.output.proposal import article_label
    return f"{LAW_NAME.get(law, law)} {article_label(key)}".strip()


def show_review(res: dict, only: set[str] | None) -> None:
    from law_agent.question_titles import STAGES, label
    print("\n[판단]  (질문별 결론 — 자세히: --q 번호)")
    stage = None
    for x in res.get("law_review") or []:
        base = x["question_id"].split(".")[0]
        if only and x["question_id"] not in only and base not in only:
            continue
        if base[0] != stage:
            stage = base[0]
            print(f"\n  {stage}. {STAGES.get(stage, '')}")
        flags = f" {x['flags']}" if x.get("flags") else ""
        miss = f"  빠진 사실: {', '.join(x['missing_facts'])}" if x.get("missing_facts") else ""
        ans = ANSWER_KO.get(x.get("answer") or "", x.get("answer") or "")
        val = _short(x.get("value"), 60) if x.get("value") else ""
        print(f"   {label(x['question_id']):<30} {STATUS_KO.get(x['status'], x['status']):<12} {ans:<4} {val}{miss}{flags}")
        if not only:
            continue
        src_ko = {"min_laws": "지정 조문", "search": "검색", "citation": "인용 따라감", "table": "판정표"}
        print(f"      질문: {x.get('question')}")
        print(f"      근거 {len(x.get('evidence') or [])}개{' (잘림)' if x.get('evidence_truncated') else ''}:")
        for e in x.get("evidence") or []:
            print(f"        [{src_ko.get(e['source'], e['source'])}] {_law_name(e['law'], e['key'])} {e['title'][:40]} ({e['chars']}자)"
                  + (f"  ⚠ {e['note'][:60]}" if e.get("note") else ""))
        for m in x.get("models") or []:
            print(f"      ▷ {m['model']}: {m['answer']} {_short(m.get('value'), 80)}")
            print(f"          이유: {m.get('reason')}")
            if m.get("missing_facts"):
                print(f"          빠진 사실: {m['missing_facts']}")
            if m.get("evidence_gaps"):
                print(f"          근거 부족: {m['evidence_gaps']}")
        for q in x.get("quotes") or []:
            print(f"      인용 [{q['ref']}] {q['text'][:120]}")
        print(f"      재검색 {x.get('retries', 0)}회, 근거 해시 {x.get('pack_hash')}\n")


def show_routes(res: dict, detail: bool) -> None:
    print("\n[경로]")
    for g, s in (res.get("skipped") or {}).items():
        print(f"   ({g} 없음) {s.get('action')}: {s.get('reason')}")
    for p in res.get("route_proposals") or []:
        st = p["checks"]["regulatory"]
        sec = {True: "업체 확보", False: "업체 미확보", None: "-"}[p.get("secured")]
        print(f"   ■ 관문{p['gate']} {st:<11} {p['title']}  [{sec}]  {p['route_id']}")
        print("      단계: " + " → ".join(f"{s.get('role')}{'(' + str(s.get('treatment_code')) + ')' if s.get('treatment_code') else ''}"
                                        for s in p.get("steps") or []))
        for c in p.get("conditions") or []:
            print(f"      조건: {c.get('text', '')[:90]}")
        for t in p.get("required_tests") or []:
            print(f"      필요 시험: {_short(t, 90)}")
        from law_agent.question_titles import title
        laws = [f"{e['law']} {e.get('article') or ''} ← {', '.join(title(q) for q in e.get('used_for') or [])}"
                for e in p.get("evidences") or [] if e.get("law")]          # 업체 표 같은 데이터 근거는 law 가 없음
        if laws:
            print("      근거 법령:")
            for l in laws[:8]:
                print(f"        - {l}")
        if not detail:
            continue
        for t in p.get("document_triggers") or []:
            print(f"      서류: {t.get('trigger')} — {str(t.get('text') or '')[:70]}")
        from law_agent.core.proposal.route_proposal import partners_by_role
        for _role, role_ko, cands in partners_by_role(p):
          print(f"      [{role_ko} 순위]")
          for c in cands[:5]:
            carbon = c.get("carbon") or {}
            km = carbon.get("road_km")
            if km is None and carbon.get("basis"):
                import re
                m = re.search(r"직선 ([\d.]+)km × ([\d.]+)", carbon["basis"]) or re.search(r"최소 ([\d.]+)km", carbon["basis"])
                km = round(float(m.group(1)) * (float(m.group(2)) if m.lastindex == 2 else 1), 1) if m else None
            print(f"      {c.get('rank')}. {c.get('name')} ({c.get('sido')} {c.get('sigungu')})"
                  + (f" 도로 약 {km}km 운송 {carbon.get('transport_kgco2e')}kgCO2eq/월 ({carbon.get('vehicle')})" if carbon else ""))
        if p.get("reserve_candidates"):
            print(f"      예비 업체 {len(p['reserve_candidates'])}곳")
        for q in p.get("questions") or []:
            print(f"      질문: {_short(q.get('text') if isinstance(q, dict) else q, 90)}")


def show_requests(res: dict) -> None:
    reqs = (res.get("information_request") or {}).get("requests") or []
    print(f"\n[파트 1에 물을 것] {len(reqs)}개")
    for r in reqs:
        print(f"   {r.get('field')}: {r.get('text')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("--q", help="질문 id (쉼표로 여러 개, 부모 id 면 하위 질문까지)")
    ap.add_argument("--routes", action="store_true")
    a = ap.parse_args(argv)
    res = json.loads(Path(a.result).read_text(encoding="utf-8"))
    print(f"■ {res.get('case_id')}  engine {res.get('engine')}  입력 검사 {(res.get('input_check') or {}).get('status')}")
    pre = res.get("preliminary") or {}
    print(f"   분류번호 {(pre.get('classification') or {}).get('normalized_waste_code')}  지정 {pre.get('waste_status')}\n")
    only = set(a.q.split(",")) if a.q else None
    if not a.routes:
        show_rounds(res)
        show_review(res, only)
    if not only:
        show_routes(res, a.routes)
        show_requests(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
