"""update.py — 법령 개정 확인부터 promote 직전까지 한 번에 (주 1회 자동 실행용).

    python3 update.py              # 개정 확인 → 바뀐 게 있으면 표 만들기·대조·비교·미리보기 테스트까지 (staged 까지만)
    python3 update.py --rebuild    # 바뀐 게 없어도 표 만들기부터 다시 (점검용)
    python3 update.py status       # 지금 상태 (마지막 확인일, 반영 대기 법령)
    python3 update.py applied      # 사람이 promote 한 뒤: 반영 대기 상태 지우기

하는 일 (자동)
  1. law_watch.py check     바뀐 법령은 원문 받기·발췌까지 law_watch 가 한다
  2. build_tables.py build  → staged/{시행일}/
  3. review_tables.py --confirm → staged/review_{날짜}/ → staged/{시행일}/ 로 복사
  4. build_tables.py diff
  5. route_agent doc_review/build_requirements.py --verify  (서류 검토 기준표 ↔ 새 원문)
  6. 미리보기 회귀 테스트: active 사본에 staged 표를 덮은 폴더로 route_agent pytest
  7. 보고서 data/law/reports/{날짜}_update.md, 상태 data/law/update_status.json
하지 않는 일 (사람)
  - build_tables.py promote (판정이 바뀌므로 사람이 보고서·diff 를 보고 승인)
  - draft_tables.py (LLM 표 초안, 비용·검토 필요). 원문이 바뀌어 대조에 실패한 LLM 표 행은 보고서에 뜬다

종료 코드: 0 변경 없음 / 10 반영 대기 (사람 검토 필요) / 1 실패
인증키(LAW_OC)는 환경변수로만 받는다. 로그·보고서에는 키 값을 *** 로 가린다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent                       # src/law_api
sys.path.insert(0, str(HERE))


def _data_root() -> Path:
    env = os.environ.get("WASTE_DATA_DIR")
    if env:
        p = Path(env)
        if p.name in ("active", "staged", "old") and p.parent.name == "tables":
            return p.parent.parent
        return p
    return HERE.parents[1] / "data"


def _law_root() -> Path:
    v = os.environ.get("WASTE_LAW_DIR")
    if v:
        p = Path(v).expanduser()
        return p.parent if p.name in ("chunks", "chunk_v2") else p
    return _data_root() / "law"


DATA = _data_root()
LAW = _law_root()
TABLES = DATA / "tables"
STAGED, ACTIVE = TABLES / "staged", TABLES / "active"
REPORTS = LAW / "reports"
STATUS = LAW / "update_status.json"
LOCK = LAW / ".update.lock"
ROUTE_SRC = HERE.parent / "route_agent" / "src"
PY = sys.executable
TODAY = date.today()


def mask(s: str) -> str:
    s = re.sub(r"(?<![A-Za-z])(OC=)[^&\s'\"<>]*", r"\1***", s or "", flags=re.I)
    for k in ("LAW_OC", "TIMELY_API_KEY", "ANTHROPIC_API_KEY"):
        v = os.environ.get(k)
        if v and len(v) >= 4:
            s = s.replace(v, "***")
    return s


class Log:
    def __init__(self):
        REPORTS.mkdir(parents=True, exist_ok=True)
        self.path = REPORTS / f"{TODAY}_update_steps.log"
        self.f = self.path.open("a", encoding="utf-8")

    def __call__(self, msg: str):
        line = f"[{datetime.now():%H:%M:%S}] {mask(msg)}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def run(log: Log, name: str, args: list[str], cwd: Path, env: dict | None = None, timeout: int = 1800) -> tuple[int, str]:
    log(f"▶ {name}: {' '.join(args)}")
    t = time.time()
    try:
        p = subprocess.run(args, cwd=cwd, env=env or os.environ.copy(), capture_output=True, text=True, timeout=timeout)
        out = mask((p.stdout or "") + (p.stderr or ""))
        code = p.returncode
    except subprocess.TimeoutExpired:
        out, code = f"시간 초과 ({timeout}초)", 124
    log.f.write(out + "\n")
    tail = "\n".join(out.strip().splitlines()[-3:])
    log(f"  {'✅' if code == 0 else '❌'} {name} (코드 {code}, {time.time() - t:.0f}초) {tail}")
    return code, out


# ── 상태 파일 ─────────────────────────────────────────────────────────
def load_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_status(st: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def registry_versions() -> dict:
    """{law_id: (시행일, 일련번호)} — registry.json 에서 버전만 (다른 값은 읽지 않음)"""
    try:
        reg = json.loads((LAW / "registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: (v.get("effective"), v.get("serial")) for k, v in reg.items() if isinstance(v, dict)}


def latest_staged() -> Path | None:
    cands = []
    for d in STAGED.iterdir() if STAGED.exists() else []:
        if d.is_dir() and not d.name.startswith("review_"):
            m = d / "manifest.json"
            built = ""
            if m.exists():
                try:
                    built = json.loads(m.read_text(encoding="utf-8")).get("built", "")
                except ValueError:
                    pass
            cands.append((built or datetime.fromtimestamp(d.stat().st_mtime).isoformat(), d))
    return max(cands)[1] if cands else None


def draft_counts(folder: Path) -> dict:
    """표마다 draft 로 남은 행 수 (상태 칸: 상태 / status)"""
    import csv
    out = {}
    for f in sorted(folder.glob("*.csv")):
        try:
            rows = list(csv.DictReader(f.open(encoding="utf-8-sig")))
        except (OSError, csv.Error):
            continue
        col = "상태" if rows and "상태" in rows[0] else "status" if rows and "status" in rows[0] else None
        if col:
            n = sum(1 for r in rows if (r.get(col) or "").strip() != "confirmed")
            if n:
                out[f.name] = f"{n}/{len(rows)}"
    return out


# ── 단계 ──────────────────────────────────────────────────────────────
def pipeline_steps(log: Log, report: list[str]) -> tuple[bool, Path | None]:
    """2~6단계. 반환: (모두 성공했는지, staged 폴더)"""
    ok = True
    code, out = run(log, "표 만들기", [PY, "build_tables.py", "build"], HERE)
    ok &= code == 0
    st = latest_staged()
    if not st:
        report.append("- ❌ staged 폴더를 찾지 못함 (build 실패)")
        return False, None
    report.append(f"- 새 표: `{st}` (생성 보고서 `{st / 'build_report.md'}`)")

    code, out = run(log, "원문 대조", [PY, "review_tables.py", "--confirm"], HERE)
    ok &= code == 0
    rv = STAGED / f"review_{TODAY:%Y%m%d}"
    if rv.exists():
        copied = []
        for f in rv.glob("*.csv"):
            shutil.copy2(f, st / f.name)
            copied.append(f.name)
        report.append(f"- 대조 결과 `{rv / 'review_report.md'}` → confirmed 판 {len(copied)}개를 staged로 복사")
    left = draft_counts(st)
    if left:
        report.append("- ⚠️ 확인 필요(draft)로 남은 행: " + ", ".join(f"{k} {v}" for k, v in left.items())
                      + " → 보고서를 보고, LLM 표면 `draft_tables.py draft {표}` 로 다시 만들지 판단")
    else:
        report.append("- 모든 법 기준 표 행 confirmed")

    code, out = run(log, "비교", [PY, "build_tables.py", "diff", st.name], HERE)
    ok &= code == 0
    report.append(f"- 비교 `{st / 'diff_report.md'}`")
    changed_lines = [l for l in out.splitlines() if re.search(r"(추가|삭제|변경)\s*\d", l)]
    report += [f"  - {l.strip()}" for l in changed_lines[:15]]

    if (ROUTE_SRC / "doc_review" / "build_requirements.py").exists():
        tmp_csv = Path(tempfile.mkdtemp()) / "doc_requirements.csv"
        code, out = run(log, "서류 기준표 대조", [PY, "doc_review/build_requirements.py", str(tmp_csv), "--verify"], ROUTE_SRC)
        fails = [l for l in out.splitlines() if l.startswith("실패")]
        summary = next((l for l in out.splitlines() if l.startswith("대조:")), "")
        report.append(f"- 서류 검토 기준표 {summary or '대조 결과 없음'}")
        report += [f"  - {l}" for l in fails[:15]]
        if fails:
            report.append("  → `route_agent/src/doc_review/build_requirements.py` 근거 키·인용 고친 뒤 `--confirm`")
        ok &= code == 0

    if ROUTE_SRC.exists():
        preview = Path(tempfile.mkdtemp(prefix="tables_preview_"))
        shutil.copytree(ACTIVE, preview, dirs_exist_ok=True)
        for f in st.glob("*.csv"):
            shutil.copy2(f, preview / f.name)
        env = {**os.environ, "WASTE_DATA_DIR": str(preview)}
        code, out = run(log, "미리보기 회귀 테스트",
                        [PY, "-m", "pytest", "-q", "--ignore=preliminary/test_preliminary_e2e.py", "-p", "no:cacheprovider"],
                        ROUTE_SRC, env=env, timeout=1800)
        last = next((l for l in reversed(out.splitlines()) if re.search(r"passed|failed|error", l)), "")
        report.append(f"- 미리보기 회귀 테스트 (active + 새 표): {last.strip() or '결과 없음'}")
        if code != 0:
            fails = [l for l in out.splitlines() if l.startswith(("FAILED", "ERROR"))][:10]
            report += [f"  - {l}" for l in fails]
            report.append("  → 판정이 바뀐 것. 법 개정 때문인지(테스트 기대값 갱신) 표 오류인지 사람이 확인")
        shutil.rmtree(preview, ignore_errors=True)
        ok &= code == 0
    return ok, st


def load_env_key() -> None:
    """LAW_OC 가 환경변수에 없으면 src/law_api/.env 의 LAW_OC= 줄만 읽는다 (파일을 실행하지 않음, 권한 600 필요)"""
    if os.environ.get("LAW_OC"):
        return
    p = HERE / ".env"
    if not p.exists():
        return
    if p.stat().st_mode & 0o077:
        print(f"{p} 권한이 너무 넓습니다: chmod 600 {p}", file=sys.stderr)
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(?:export\s+)?LAW_OC=(.*)$", line)
        if m:
            v = m.group(1).strip().strip("'\"").strip()
            if v:
                os.environ["LAW_OC"] = v
            return


def cmd_update(rebuild: bool = False) -> int:
    load_env_key()
    if not os.environ.get("LAW_OC"):
        print(f"LAW_OC가 없습니다 (환경변수 또는 {HERE / '.env'} 의 LAW_OC=…).", file=sys.stderr)
        return 1
    LAW.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
        print(f"다른 update 가 실행 중입니다 ({LOCK}). 멈춘 거면 지우고 다시.", file=sys.stderr)
        return 1
    LOCK.write_text(str(os.getpid()))
    log = Log()
    try:
        before = registry_versions()
        report = [f"# 법령 갱신 보고서 — {TODAY}", "",
                  f"- 데이터 `{DATA}` / 법령 `{LAW}`", f"- 단계 기록 `{log.path}`", ""]
        code, out = run(log, "개정 확인", [PY, "law_watch.py", "check"], HERE)
        after = registry_versions()
        changed = sorted(k for k in after if after[k] != before.get(k)) if before else []
        if not before:
            report.append("- ⚠️ 실행 전 기준선(registry.json)이 없었음 → `python3 law_watch.py baseline` 먼저")
        errors = [l.strip() for l in out.splitlines() if "❌" in l]
        check_report = REPORTS / f"{TODAY}_check.md"
        report.append(f"## 1. 개정 확인  (`{check_report}`)")
        if code != 0:
            report.append(f"- ❌ law_watch check 실패 (코드 {code}). 단계 기록 확인")
        report += [f"- ❌ {mask(e)}" for e in errors]
        report.append(f"- 바뀐 법령: {', '.join(f'{k} {before.get(k, (None,))[0]}→{after[k][0]}' for k in changed) or '없음'}")

        st = load_status()
        pending = sorted(set(st.get("pending") or []) | set(changed))
        result = 0 if code == 0 and not errors else 1
        staged = st.get("staged")
        if changed or rebuild:
            report += ["", "## 2. 표 만들기·대조·비교 (staged 까지, active 는 그대로)"]
            ok, sdir = pipeline_steps(log, report)
            staged = str(sdir) if sdir else staged
            result = 10 if ok or changed else 1
            report += ["", "## 3. 사람이 할 일",
                       "1. 위 보고서(check, build_report, review_report, diff_report)와 미리보기 테스트 결과 확인",
                       f"2. 맞으면 반영: `cd \"$ROOT/src/law_api\" && python3 build_tables.py promote {Path(staged).name if staged else '{시행일}'}`",
                       "3. 서류 기준표 실패가 있으면 고친 뒤: `cd \"$ROOT/src/route_agent/src\" && "
                       "python3 doc_review/build_requirements.py \"$WASTE_DATA_DIR/doc_requirements.csv\" --confirm`",
                       "4. `python3 -m pytest -q --ignore=preliminary/test_preliminary_e2e.py` (route_agent)",
                       "5. `python3 update.py applied` (반영 대기 지우기)"]
        else:
            report += ["", "변경 없음. 판정표 그대로." + (f" (반영 대기 중인 이전 개정: {', '.join(pending)})" if pending else "")]
            if pending:
                result = 10

        rp = REPORTS / f"{TODAY}_update.md"
        rp.write_text(mask("\n".join(report)) + "\n", encoding="utf-8")
        save_status({"last_check": datetime.now().isoformat(timespec="seconds"),
                     "check_ok": code == 0 and not errors,
                     "pending": pending, "staged": staged if pending else None,
                     "report": str(rp), "exit": result})
        log(f"보고서: {rp}  (종료 코드 {result}: {'변경 없음' if result == 0 else '반영 대기 — 사람 검토' if result == 10 else '실패'})")
        return result
    finally:
        LOCK.unlink(missing_ok=True)


def cmd_status() -> int:
    st = load_status()
    if not st:
        print("아직 update 를 실행한 적이 없습니다.")
        return 0
    days = (datetime.now() - datetime.fromisoformat(st["last_check"])).days
    print(f"마지막 확인 {st['last_check']} ({days}일 전){'' if st.get('check_ok') else '  ⚠️ 확인 중 오류'}")
    print(f"반영 대기: {', '.join(st.get('pending') or []) or '없음'}" + (f"  (staged: {st['staged']})" if st.get("staged") else ""))
    print(f"보고서: {st.get('report')}")
    return 10 if st.get("pending") else 0


def cmd_applied() -> int:
    st = load_status()
    st.update(pending=[], staged=None, applied_at=datetime.now().isoformat(timespec="seconds"))
    save_status(st)
    print("반영 대기를 지웠습니다.")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["status"]:
        sys.exit(cmd_status())
    if a[:1] == ["applied"]:
        sys.exit(cmd_applied())
    sys.exit(cmd_update(rebuild="--rebuild" in a))
