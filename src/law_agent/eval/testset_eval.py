"""사전검증 시험세트 24건 평가 — 분류(A-1·A-2)와 재활용 금지(B-1)만 판단해 기대값과 비교한다.

  source env.sh   (그리고 타임리 키)
  PYTHONPATH=src python3 -m law_agent.eval.testset_eval [--dry] [--cases PRE-01,PRE-03] [--with-b2] [--json 결과.json]

기대값: 프로젝트 문서 '사전검증_시험세트.md' (2026.11.12, 규칙표 시스템 기준). 사람이 법을 해석해 만든 기준이므로
  다른 결과가 나오면 '틀림' 이 아니라 '사람 확인' 대상이다. 단 기준보다 느슨한 결과(위험)는 0건이어야 한다.

판정 (A-2: 일반 < 의심 < 지정,  B-1: 아님 < 검토 < 금지)
  일치       : 기대값과 같음 (기대가 의심·검토이고 결과가 보류(정보 필요·사람 검토)면 일치로 봄)
  조건부     : 기대는 지정·금지인데 결과가 조건부(의심·검토) — 확정이 아니라 경로에서 confirmed 가 될 수 없고,
               빠진 사실(예: 석면 함량)을 파트 1에 묻는다. 안전한 차이로 센다.
  더 엄격     : 기대보다 엄격 — 안전한 차이, 사람 확인
  덜 엄격     : 기대보다 느슨한 확정 결론 (예: 기대 지정 → 결과 일반) — 위험 오류
  보류       : 기대는 확정인데 결과가 정보 필요·사람 검토
입력 오류 건(PRE-17~21)은 judge 를 부르지 않고 input_check 결과를 기대값과 비교한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from law_agent.eval import smoke_judge
from law_agent.input_check import check_input
from law_agent.state import to_jsonable

TESTSET = Path(__file__).resolve().parent / "data" / "preliminary_testset.json"

# case_id: (지정 판정, 재활용 금지, 입력 상태)   — '사전검증_시험세트.md' 표 그대로
EXPECTED: dict[str, tuple[str | None, str | None, str]] = {
    "PRE-01": ("일반", "아님", "ok"), "PRE-02": ("지정", "아님", "ok"), "PRE-03": ("지정", "금지", "ok"),
    "PRE-04": ("지정", "금지", "ok"), "PRE-05": ("지정", "아님", "ok"), "PRE-06": ("지정", "검토", "ok"),
    "PRE-07": ("지정", "검토", "ok"), "PRE-08": ("의심", "아님", "ok"), "PRE-09": ("의심", "아님", "ok"),
    "PRE-10": ("일반", "아님", "ok"), "PRE-11": ("일반", "아님", "ok"), "PRE-12": ("일반", "아님", "ok"),
    "PRE-13": ("일반", "아님", "ok"), "PRE-14": ("일반", "아님", "ok"), "PRE-15": ("지정", "아님", "ok"),
    "PRE-16": ("지정", "금지", "ok"), "PRE-17": (None, None, "needs_info"), "PRE-18": (None, None, "needs_info"),
    "PRE-19": (None, None, "needs_info"), "PRE-20": (None, None, "needs_review"),
    "PRE-21": (None, None, "needs_info"), "PRE-22": ("일반", "아님", "ok"), "PRE-23": ("일반", "검토", "ok"),
    "PRE-24": ("일반", "아님", "ok"),
}
EXPECTED_B2 = {"PRE-05": ["R-4-10"]}           # 재활용 제한: 허용 유형

RANK_A2 = {"일반": 0, "의심": 1, "지정": 2}
RANK_B1 = {"아님": 0, "검토": 1, "금지": 2}
HOLD = "보류"


def label(v, kind: str) -> str:
    """Verdict → 판정 이름."""
    if v is None:
        return "없음"
    if v.status in ("needs_info", "needs_human") or v.answer == "unknown":
        return HOLD
    if kind == "A-2":
        return {"yes": "지정", "no": "일반", "conditional": "의심"}.get(v.answer, HOLD)
    return {"yes": "금지", "no": "아님", "conditional": "검토"}.get(v.answer, HOLD)


def compare(expected: str, got: str, rank: dict) -> str:
    if got == "없음":
        return "없음"
    if got == HOLD:
        return "일치" if rank[expected] == 1 else HOLD
    if got == expected:
        return "일치"
    if rank[got] == 1 and rank[expected] == 2:
        return "조건부"                              # 지정→의심, 금지→검토: 확정이 아니라 묻는 것
    return "더 엄격" if rank[got] > rank[expected] else "덜 엄격"


def evaluate_case(case: dict, mode: str | None, with_b2: bool) -> dict:
    cid = case["case_id"]
    exp_a2, exp_b1, exp_in = EXPECTED.get(cid, (None, None, "ok"))
    chk = check_input(case)
    row = {"case_id": cid, "name": case.get("name", ""), "input": chk["status"], "input_expected": exp_in,
           "input_match": chk["status"] == exp_in, "warnings": chk["warnings"], "issues": chk["issues"]}
    if chk["status"] != "ok":
        return row
    only = ["A-1", "A-2", "B-1"] + (["B-2"] if with_b2 else [])
    t0 = time.time()
    st, rounds = smoke_judge.run_case(case, mode, only)
    vs = st["verdicts"]
    a1, a2, b1 = vs.get("A-1"), vs.get("A-2"), vs.get("B-1")
    row.update({
        "A-1": f"{a1.status}/{a1.answer}" if a1 else "없음",
        "A-2": label(a2, "A-2"), "A-2_expected": exp_a2, "A-2_result": compare(exp_a2, label(a2, "A-2"), RANK_A2),
        "B-1": label(b1, "B-1"), "B-1_expected": exp_b1, "B-1_result": compare(exp_b1, label(b1, "B-1"), RANK_B1),
        "calls": sum(r["calls"] for r in rounds), "seconds": round(time.time() - t0, 1),
        "usage": dict(sum((Counter(r["usage"]) for r in rounds), Counter())),
        "reasons": {k: (v.flags, [x["model"].split("/")[-1] + ":" + (x["answer"] or "실패") for x in v.votes])
                    for k, v in vs.items()},
        "missing_facts": {k: v.missing_facts for k, v in vs.items() if v.missing_facts},
    })
    if with_b2:
        b2 = vs.get("B-2")
        row["B-2"] = (b2.value or {}).get("items") if b2 and b2.status == "agreed" else (b2.status if b2 else "없음")
        row["B-2_expected"] = EXPECTED_B2.get(cid)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(TESTSET))
    ap.add_argument("--cases")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--mode", choices=["single", "escalate", "all"])
    ap.add_argument("--with-b2", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    cases = json.loads(Path(a.path).read_text(encoding="utf-8"))["cases"]
    if a.cases:
        want = set(a.cases.split(","))
        cases = [c for c in cases if c["case_id"] in want]
    if a.dry:
        import os
        os.environ["LAW_AGENT_NO_CACHE"] = "1"   # 가짜 답을 캐시에 쓰지 않게 (2026.11.18 dry 답이 실제 실행에 쓰였음)
        from law_agent.llm import client
        client.chat_json = smoke_judge.dry_llm

    rows = []
    print(f"{'사례':7} {'이름':22} {'입력':12} {'A-2 기대→결과':16} {'판정':6} {'B-1 기대→결과':16} {'판정':6} {'호출':>4}")
    for case in cases:
        r = evaluate_case(case, a.mode, a.with_b2)
        rows.append(r)
        if r["input"] != "ok":
            mark = "일치" if r["input_match"] else f"다름(기대 {r['input_expected']})"
            print(f"{r['case_id']:7} {r['name'][:20]:22} {r['input']:12} 입력 검사에서 멈춤 — {mark}")
            continue
        a2 = f"{r['A-2_expected']}→{r['A-2']}"
        b1 = f"{r['B-1_expected']}→{r['B-1']}"
        print(f"{r['case_id']:7} {r['name'][:20]:22} {r['input']:12} {a2:16} {r['A-2_result']:6} {b1:16} "
              f"{r['B-1_result']:6} {r['calls']:>4}" + (f"  B-2 {r.get('B-2')}" if a.with_b2 else ""))

    judged = [r for r in rows if r["input"] == "ok"]
    print("\n── 요약")
    for k in ("A-2", "B-1"):
        c = Counter(r[f"{k}_result"] for r in judged)
        n = len(judged) or 1
        print(f"{k}: 일치 {c['일치']}/{len(judged)} ({c['일치'] / n:.0%}), 조건부 {c['조건부']}, "
              f"더 엄격 {c['더 엄격']}, 덜 엄격(위험) {c['덜 엄격']}, 보류 {c[HOLD]}")
    inp = [r for r in rows if r["input_expected"] != "ok" or r["input"] != "ok"]
    print(f"입력 검사: 일치 {sum(r['input_match'] for r in inp)}/{len(inp)}")
    usage = sum((Counter(r.get("usage", {})) for r in judged), Counter())
    print(f"호출 {sum(r.get('calls', 0) for r in judged)}회, 토큰 {dict(usage)}")
    danger = [(r["case_id"], k) for r in judged for k in ("A-2", "B-1") if r[f"{k}_result"] == "덜 엄격"]
    if danger:
        print(f"⚠ 위험(덜 엄격): {danger} — 근거와 모델 답을 --json 으로 확인")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(to_jsonable(rows), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"자세한 결과: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
