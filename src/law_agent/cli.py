"""파트 1 → 파트 2 전체를 사용자용·관리자용 명령으로 (waste.sh / waste-admin.sh 가 부른다).

사용자 (배출업체 담당자)
  new 입력서.docx [--synthetic]   파트 1 입력서 접수 → 폐기물 목록 (첫 건을 작업 중 사례로)
  cases / use 번호|case_id        폐기물 목록 / 작업할 사례 고르기
  next                            파트 1이 더 묻는 질문
  answer 질문ID 답                 답 넣기 (판 번호 자동)
  code 분류번호 [근거]             분류번호를 근거와 함께 기록 (별표 4 대조)
  send [-v]                       파트 1 내보내기 → 파트 2 판단 → 결과 (바로 실행. --queue 면 inbox 에만 넣음)
  result                          경로·업체 순위·서류·되묻는 질문

관리자
  start [-v] / log / status       파트 2 감시 켜기 / 실시간 로그 / 대기·완료·실패
  detail [--q 질문]               판단 과정·근거·모델 답 (작업 중 사례의 최신 결과)
  p1 스크립트 인자…                파트 1 명령 그대로 (--db·--case 자동). 예: p1 part1.py status
  test / eval / models / cache    테스트 / 24건 평가 / 모델 확인 / dry 캐시 정리

작업 상태: data/waste/state.json (db, case). 파트 1 위치: PART1_DIR (기본 src/part1).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ROOT") or Path.cwd())
PART1 = Path(os.environ.get("PART1_DIR") or ROOT / "src" / "part1")
WORK = ROOT / "data" / "waste"
STATE = WORK / "state.json"
INBOX, OUTBOX = ROOT / "data" / "bridge" / "inbox", ROOT / "data" / "bridge" / "outbox"
STATUS_KO = {"confirmed": "가능", "conditional": "조건부", "needs_info": "정보 필요", "rejected": "불가"}


# ── 상태 ─────────────────────────────────────────────────────

def state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**kw) -> dict:
    st = {**state(), **kw}
    WORK.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return st


def need(key: str) -> str:
    v = state().get(key)
    if not v:
        sys.exit("먼저 입력서를 접수하세요: waste.sh new 입력서.docx" if key == "db" else
                 "작업할 사례가 없습니다: waste.sh cases 후 waste.sh use 번호")
    return v


# ── 파트 1 호출 ─────────────────────────────────────────────

def p1(script: str, *args: str, capture: bool = False) -> str:
    cmd = [sys.executable, str(PART1 / script), *args]
    if not (PART1 / script).is_file():
        sys.exit(f"파트 1 스크립트가 없습니다: {PART1 / script} (PART1_DIR 확인)")
    r = subprocess.run(cmd, cwd=PART1, text=True, capture_output=capture)
    if r.returncode != 0:
        if capture:
            sys.stderr.write((r.stdout or "") + (r.stderr or ""))
        sys.exit(f"파트 1 명령 실패: {script} {' '.join(args[:2])}")
    return r.stdout if capture else ""


def p1_cases(db: str) -> list[dict]:
    return json.loads(p1("phase3.py", "cases", "--db", db, capture=True) or "[]")


def revision(db: str, case: str) -> int:
    return int(json.loads(p1("phase2.py", "status", "--db", db, "--case", case, capture=True)).get("draft_revision") or 0)


# ── 사용자 ──────────────────────────────────────────────────

def u_new(a) -> None:
    src = Path(a.docx).resolve()
    if not src.is_file():
        sys.exit(f"입력서를 찾을 수 없습니다: {src}")
    db = WORK / "db" / f"{src.stem}_{dt.datetime.now():%Y%m%d_%H%M%S}.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    print(f"① 입력서 접수: {src.name}")
    p1("db_intake.py", "ingest", str(src), "--db", str(db), *(["--synthetic"] if a.synthetic else []))
    save_state(db=str(db), case=None, questions=[])
    from law_agent.bridge.docx_codes import docx_codes
    codes = docx_codes(src)
    for c in p1_cases(str(db)) if codes else []:
        code = codes.get(c.get("table_index"))
        if code:
            p1("part1.py", "classification", "--db", str(db), "--case", c["case_id"], "supported", "--code", code,
               "--reference", "입력서 기재 분류번호 (가상, 시연용)",
               "--notes", "입력서 '분류번호 (가상)' 칸 값. 실제 별표 4 대조·기관 확인 아님.", capture=True)
            print(f"   분류번호 기록: {c.get('name')} → {code} (입력서 기재, 가상)")
    cases = u_cases(None)
    if cases:
        save_state(case=cases[0]["case_id"])
        print(f"\n작업 중 사례: 1번 {cases[0].get('name')}  (바꾸려면 waste.sh use 번호)")
        print("다음: waste.sh next  (파트 1이 더 묻는 질문)")


def u_cases(a) -> list[dict]:
    db = need("db")
    cases = p1_cases(db)
    cur = state().get("case")
    print("\n폐기물 목록")
    for i, c in enumerate(cases, 1):
        mark = "▶" if c["case_id"] == cur else " "
        print(f" {mark} {i}. {c.get('name')}  ({c.get('quantity_raw') or '양 미기재'}, {c.get('physical_state') or ''})")
    return cases


def u_use(a) -> None:
    cases = p1_cases(need("db"))
    pick = next((c for i, c in enumerate(cases, 1) if a.which in (str(i), c["case_id"])), None)
    if not pick:
        sys.exit("그 번호의 사례가 없습니다. waste.sh cases 로 확인하세요.")
    save_state(case=pick["case_id"], questions=[])
    print(f"작업 중 사례: {pick.get('name')}")


RX_QID = __import__("re").compile(r"질문 ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def u_next(a) -> None:
    out = p1("phase2.py", "next", "--db", need("db"), "--case", need("case"), "--limit", str(min(max(a.limit, 1), 3)),
             capture=True)
    ids = RX_QID.findall(out)
    save_state(questions=ids)
    n = 0
    for line in out.splitlines():
        if RX_QID.search(line):
            n += 1
            print(f"\n[{n}] " + line.split(" / ")[1] if " / " in line else f"\n[{n}] {line}")
        elif line.startswith("phase2.py answer"):
            continue
        else:
            print("    " + line if n else line)
    if ids:
        print(f"\n답하기: waste.sh answer 1 \"답\"   (번호 1~{len(ids)} 또는 질문ID)"
              "\n분류번호는: waste.sh code 51-03-01 \"근거\"")
    else:
        print("\n더 물을 질문이 없습니다. 다음: waste.sh send")


def u_answer(a) -> None:
    db, case = need("db"), need("case")
    qs = state().get("questions") or []
    req = a.request
    if req.isdigit():
        if not 1 <= int(req) <= len(qs):
            sys.exit("그 번호의 질문이 없습니다. 먼저 waste.sh next")
        req = qs[int(req) - 1]
    p1("phase2.py", "answer", "--db", db, "--case", case, "--request", req,
       "--revision", str(revision(db, case)), a.value)
    print("저장했습니다. 남은 질문: waste.sh next")


def u_code(a) -> None:
    p1("part1.py", "classification", "--db", need("db"), "--case", need("case"), "supported",
       "--code", a.code, "--reference", a.reference or f"시행규칙 별표 4 {a.code} 대조")
    print(f"분류번호 {a.code} 기록 (파트 2가 법령으로 다시 확인합니다). 다음: waste.sh send")


def u_send(a) -> None:
    db, case = need("db"), need("case")
    INBOX.mkdir(parents=True, exist_ok=True)
    out = INBOX / f"{case}_{dt.datetime.now():%H%M%S}.json"
    print("② 파트 1 내보내기 → 파트 2")
    p1("phase3.py", "export", "--db", db, "--case", case, "--out", str(out), capture=True)
    if a.queue:
        print(f"inbox 에 넣었습니다: {out.name} (감시 중인 파트 2가 처리)")
        return
    print("③ 파트 2 판단 중 (법령 → 경로 → 업체 순위)…", flush=True)
    from law_agent.bridge.watch import scan
    from law_agent.run import load_partners, use_dry_llm
    OUTBOX.mkdir(parents=True, exist_ok=True)
    if os.environ.get("WASTE_DRY") == "1":            # 시험용: 가짜 LLM
        use_dry_llm()
    pd, fa = load_partners(os.environ.get("WASTE_NO_PARTNERS") == "1")
    scan(INBOX, OUTBOX, pd, fa, {}, once=True, verbose=a.verbose)
    u_result(a)


def latest_result(case: str | None = None) -> dict:
    case = case or need("case")
    files = sorted(OUTBOX.glob(f"*_result.json"), key=lambda p: p.stat().st_mtime)
    for f in reversed(files):
        res = json.loads(f.read_text(encoding="utf-8"))
        if (res.get("case_id") == case or (res.get("route_proposals") or [{}])[0].get("case_id") == case
                or f.name.startswith(case)):
            return {"_file": str(f), **res}
    sys.exit("결과가 아직 없습니다: waste.sh send")


def u_result(a) -> None:
    res = latest_result()
    print(f"\n━━ 결과: {res.get('case_id')} ━━")
    chk = res.get("input_check") or {}
    if chk.get("status") not in (None, "ok"):
        for it in chk.get("issues") or []:
            print(f" ✘ 판단 전 확인 필요: {it.get('text')}")
        print("   → waste.sh next 로 답하거나 waste.sh code 로 분류번호를 기록한 뒤 다시 send")
        return
    props = res.get("route_proposals") or []
    if not props:
        print(" 재활용(중간 가공) 경로를 만들 수 없습니다.")
        for g, s in (res.get("skipped") or {}).items():
            print(f"   - {s.get('reason')}")
    for p in props:
        st = p["checks"]["regulatory"]
        print(f"\n ■ {p['title']}  →  {STATUS_KO.get(st, st)}")
        for t in p.get("required_tests") or []:
            print(f"   필요 시험: {t.get('test') if isinstance(t, dict) else t}")
        from law_agent.core.proposal.route_proposal import partners_by_role, road_text
        for role, role_ko, cands in partners_by_role(p):
            print(f"   {role_ko} 순위")
            for c in cands[:5]:
                print(f"     {c.get('rank')}. {c.get('name')} ({c.get('sido')} {c.get('sigungu')})  {road_text(c)}" + (f"  ☎ {c['phone']}" if c.get("phone") else ""))
        docs = [t.get("trigger") for t in p.get("document_triggers") or []]
        if docs:
            print("   필요한 서류: " + ", ".join(docs))
        laws = sorted({f"{e['law']} {e.get('article') or ''}".strip() for e in p.get("evidences") or [] if e.get("law")})
        if laws:
            print("   근거 법령: " + ", ".join(laws[:5]))
    reqs = (res.get("information_request") or {}).get("requests") or []
    if reqs:
        print(f"\n 더 알려 주세요 ({len(reqs)}개)")
        for r in reqs:
            print(f"   - {r.get('text')}")
    print(f"\n자세히(관리자): waste-admin.sh detail")


# ── 관리자 ──────────────────────────────────────────────────

def m_start(a) -> None:
    from law_agent.bridge import watch
    watch.main(["--inbox", str(INBOX), "--outbox", str(OUTBOX)] + (["-" + "v" * a.verbose] if a.verbose else []))


def m_log(a) -> None:
    log = OUTBOX / "live.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch()
    follow(log)


def follow(path: Path, last: int = 50, interval: float = 0.5) -> None:
    """tail -n 50 -f 대신 (윈도우에도 됨). Ctrl+C 로 끝."""
    import time
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-last:]:
            print(line, end="")
        print(f"--- {path} 실시간 보기 (끝내려면 Ctrl+C) ---", flush=True)
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(interval)
        except KeyboardInterrupt:
            print()


def m_status(a) -> None:
    for name, d in [("대기", INBOX), ("완료", INBOX / "done"), ("실패", INBOX / "failed")]:
        files = sorted(d.glob("*.json")) if d.is_dir() else []
        print(f"{name} {len(files)}개" + "".join(f"\n   {f.name}" for f in files[-5:]))
    res = sorted(OUTBOX.glob("*_result.json"), key=lambda p: p.stat().st_mtime) if OUTBOX.is_dir() else []
    print(f"결과 {len(res)}개" + "".join(f"\n   {f.name}" for f in res[-5:]))
    st = state()
    print(f"작업 중: db={st.get('db')}\n        case={st.get('case')}")


def m_detail(a) -> None:
    from law_agent.eval import show_result
    f = latest_result(a.case)["_file"]
    show_result.main([f] + (["--q", a.q] if a.q else []))


def m_p1(a) -> None:
    args = list(a.args)
    st = state()
    if "--db" not in args and st.get("db"):
        args += ["--db", st["db"]]
    if "--case" not in args and st.get("case") and a.script not in ("db_intake.py",):
        args += ["--case", st["case"]]
    p1(a.script, *args)


def m_questions(a) -> None:
    """질문 번호표: 번호 · 짧은 이름 · 전체 질문."""
    from law_agent.question_titles import STAGES, title
    from law_agent.state import load_checklist
    stage = None
    for q in load_checklist(str(ROOT / "src" / "law_agent" / "config" / "checklist.yaml")):
        if q.id[0] != stage:
            stage = q.id[0]
            print(f"\n{stage}. {STAGES.get(stage, '')}")
        print(f"  {q.id:<4} {title(q.id)}\n       {q.text.strip().splitlines()[0]}")


def m_test(a) -> None:
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "src" / "law_agent" / "tests")]))


def m_eval(a) -> None:
    from law_agent.eval import testset_eval
    testset_eval.main(["--json", str(ROOT / "data" / "law_agent" / "testset_24_latest.json")])


def m_models(a) -> None:
    from law_agent.eval import list_models
    list_models.main()


def m_cache(a) -> None:
    from law_agent.store.cache import purge_dry
    print(f"dry 가짜 답 {purge_dry()}개 지움")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="waste")
    role = ap.add_subparsers(dest="role", required=True)

    u = role.add_parser("user").add_subparsers(dest="cmd", required=True)
    x = u.add_parser("new"); x.add_argument("docx"); x.add_argument("--synthetic", action="store_true"); x.set_defaults(f=u_new)
    u.add_parser("cases").set_defaults(f=u_cases)
    x = u.add_parser("use"); x.add_argument("which"); x.set_defaults(f=u_use)
    x = u.add_parser("next"); x.add_argument("--limit", type=int, default=3); x.set_defaults(f=u_next)
    x = u.add_parser("answer"); x.add_argument("request"); x.add_argument("value"); x.set_defaults(f=u_answer)
    x = u.add_parser("code"); x.add_argument("code"); x.add_argument("reference", nargs="?"); x.set_defaults(f=u_code)
    x = u.add_parser("send"); x.add_argument("-v", "--verbose", action="count", default=0)
    x.add_argument("--queue", action="store_true"); x.set_defaults(f=u_send)
    u.add_parser("result").set_defaults(f=u_result)

    m = role.add_parser("admin").add_subparsers(dest="cmd", required=True)
    x = m.add_parser("start"); x.add_argument("-v", "--verbose", action="count", default=0); x.set_defaults(f=m_start)
    m.add_parser("log").set_defaults(f=m_log)
    m.add_parser("status").set_defaults(f=m_status)
    x = m.add_parser("detail"); x.add_argument("--case"); x.add_argument("--q"); x.set_defaults(f=m_detail)
    x = m.add_parser("p1"); x.add_argument("script"); x.add_argument("args", nargs=argparse.REMAINDER); x.set_defaults(f=m_p1)
    m.add_parser("questions").set_defaults(f=m_questions)
    m.add_parser("test").set_defaults(f=m_test)
    m.add_parser("eval").set_defaults(f=m_eval)
    m.add_parser("models").set_defaults(f=m_models)
    m.add_parser("cache").set_defaults(f=m_cache)

    a = ap.parse_args(argv)
    a.f(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
