"""윈도우·리눅스·맥 공통 실행기. 파이썬 기본 기능만 쓴다 (law_agent 를 import 하지 않음).

  python src/law_agent/launch.py user  <명령…>    사용자용   (= waste.bat / waste.sh)
  python src/law_agent/launch.py admin <명령…>    관리자용   (= waste-admin.bat / waste-admin.sh)
  python src/law_agent/launch.py setup            가상환경(.venv) 만들고 패키지 설치, waste.env 만들기
  python src/law_agent/launch.py doctor           경로·패키지·키 있는지 점검 (키 값은 출력하지 않음)

하는 일 (예전 _common.sh 와 같음)
  1. ROOT = waste_agent 폴더 (이 파일 기준 두 단계 위)
  2. 설정 읽기: env.sh (export K=V 줄만) → waste.env (K=V) → src/law_agent/llm/.env (키)
     값 안의 ${ROOT} · $HOME 은 바꿔 넣는다. 뒤에 읽은 파일이 앞을 덮는다.
  3. 가상환경: WASTE_VENV → ROOT/.venv → ~/5.Agent_System/.venv-ai-agent 중 처음 있는 것의 파이썬
  4. PYTHONPATH=ROOT/src, PART1_DIR=ROOT/src/part1 (비어 있을 때), PYTHONUTF8=1 (윈도우 한글 깨짐 방지)
  5. ROOT 에서 python -m law_agent.cli <user|admin> … 실행
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IS_WIN = os.name == "nt"
ENV_FILES = ["env.sh", "waste.env"]
KEY_FILE = Path("src") / "law_agent" / "llm" / ".env"
PATH_VARS = ["WASTE_LAW_DIR", "WASTE_DATA_DIR", "WASTE_REFERENCE_DIR", "LAW_AGENT_DATA_DIR", "PART1_DIR"]
KEY_VARS = ["TIMELY_BASE_URL", "TIMELY_API_KEY", "KAKAO_REST_API_KEY", "LAW_OC"]
PACKAGES = ["httpx", "openai", "yaml", "langgraph"]
RX_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

HELP_USER = """사용자용 (배출업체 담당자)
  new 입력서.docx [--synthetic]   입력서 접수 (폐기물 목록)
  cases | use 번호                 목록 / 작업할 폐기물 고르기
  next                             파트 1이 더 묻는 질문
  answer 번호 "답"                 답하기
  code 51-03-01 "근거"             분류번호 기록
  send [-v]                        파트 2 판단까지 (결과 바로 표시)
  result                           결과 다시 보기"""
HELP_ADMIN = """관리자용
  start [-v]      파트 2 감시 켜기 (inbox → outbox)
  log             실시간 로그
  status          대기·완료·실패·결과 목록
  detail [--q B-1]  판단 과정·근거·모델 답
  p1 part1.py …   파트 1 명령 그대로
  questions | test | eval | models | cache"""


# ── 설정 파일 ────────────────────────────────────────────────

def _unquote(v: str) -> str:
    v = v.strip()
    if " #" in v and not v.startswith(("'", '"')):
        v = v.split(" #", 1)[0].strip()            # 줄 끝 주석
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def read_env_file(path: Path, env: dict) -> list[str]:
    """K=V · export K=V 줄만 읽어 env 에 넣는다. 셸 문법(if, $(…), 함수)은 건너뛴다. 읽은 이름 목록."""
    names = []
    if not path.is_file():
        return names
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RX_LINE.match(raw)
        if not m or raw.lstrip().startswith("#"):
            continue
        k, v = m.group(1), _unquote(m.group(2))
        if "$(" in v or "`" in v:
            continue                                    # 명령 실행 결과는 지원 안 함
        v = expand(v, env)
        env[k] = v
        names.append(k)
    return names


def expand(v: str, env: dict) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if name == "HOME":
            return env.get("HOME") or str(Path.home())
        return env.get(name, m.group(0))
    v = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", sub, v)
    if v.startswith("~"):
        v = str(Path.home()) + v[1:]
    return v


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if IS_WIN else "bin/python")


def find_venv(env: dict) -> Path | None:
    cands = [env.get("WASTE_VENV"), ROOT / ".venv", Path.home() / "5.Agent_System" / ".venv-ai-agent"]
    for c in cands:
        if c and venv_python(Path(c)).is_file():
            return Path(c)
    return None


def build_env() -> tuple[dict, list[str]]:
    env = dict(os.environ)
    env["ROOT"] = str(ROOT)
    env.setdefault("HOME", str(Path.home()))
    loaded = []
    for name in ENV_FILES:
        if read_env_file(ROOT / name, env):
            loaded.append(name)
    if read_env_file(ROOT / KEY_FILE, env):
        loaded.append(str(KEY_FILE))
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PART1_DIR", str(ROOT / "src" / "part1"))
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env, loaded


def python_for(env: dict) -> str:
    if env.get("VIRTUAL_ENV") and venv_python(Path(env["VIRTUAL_ENV"])).is_file():
        return str(venv_python(Path(env["VIRTUAL_ENV"])))
    v = find_venv(env)
    return str(venv_python(v)) if v else sys.executable


# ── 명령 ─────────────────────────────────────────────────────

def run_cli(role: str, args: list[str]) -> int:
    if not args or args[0] in ("-h", "help", "--help"):
        print(HELP_USER if role == "user" else HELP_ADMIN)
        return 0
    env, _ = build_env()
    return subprocess.call([python_for(env), "-m", "law_agent.cli", role, *args], cwd=ROOT, env=env)


def setup(args: list[str]) -> int:
    venv = ROOT / ".venv"
    if not venv_python(venv).is_file():
        print(f"가상환경 만들기: {venv}")
        rc = subprocess.call([sys.executable, "-m", "venv", str(venv)])
        if rc:
            return rc
    py = str(venv_python(venv))
    reqs = [HERE / "requirements.txt", ROOT / "src" / "part1" / "requirements.txt"]
    subprocess.call([py, "-m", "pip", "install", "--upgrade", "pip"])
    for r in reqs:
        if r.is_file():
            print(f"패키지 설치: {r.relative_to(ROOT)}")
            rc = subprocess.call([py, "-m", "pip", "install", "-r", str(r)])
            if rc:
                return rc
    import shutil
    for b in ("waste.bat", "waste-admin.bat"):
        shutil.copy(HERE / "bin" / b, ROOT / b)
    print("실행 파일: waste.bat, waste-admin.bat (waste_agent 폴더)")
    we = ROOT / "waste.env"
    if not we.exists():
        we.write_text((HERE / "bin" / "waste.env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print("waste.env 를 만들었습니다 — 데이터 경로를 확인하세요.")
    key = ROOT / KEY_FILE
    if not key.exists():
        key.write_text("TIMELY_BASE_URL=\nTIMELY_API_KEY=\nKAKAO_REST_API_KEY=\n", encoding="utf-8")
        print(f"{KEY_FILE} 를 만들었습니다 — 각자 받은 키를 넣으세요.")
    print("\n다음: doctor 로 점검")
    return 0


def doctor(args: list[str]) -> int:
    env, loaded = build_env()
    py = python_for(env)
    bad = 0
    print(f"waste_agent 폴더 : {ROOT}")
    print(f"운영체제         : {'윈도우' if IS_WIN else sys.platform}")
    print(f"파이썬           : {py}")
    print(f"읽은 설정 파일   : {', '.join(loaded) or '없음'}")
    print("\n[경로]")
    for k in PATH_VARS:
        v = env.get(k)
        ok = bool(v) and Path(v).exists()
        if k == "WASTE_REFERENCE_DIR" and not v:
            print(f"  -   {k:20} (비어 있음 — 생략 가능)")
            continue
        bad += not ok
        print(f"  {'OK ' if ok else '없음'} {k:20} {v or '(비어 있음)'}")
    print("\n[키] (값은 보여주지 않음)")
    for k in KEY_VARS:
        print(f"  {'있음' if env.get(k) else '없음'} {k}")
    for w in key_warnings():
        bad += 1
        print(f"  주의 {w}")
    print("\n[패키지]")
    for p in PACKAGES:
        r = subprocess.run([py, "-c", f"import {p}"], capture_output=True, env=env)
        bad += r.returncode != 0
        print(f"  {'OK ' if r.returncode == 0 else '없음'} {p}")
    r = subprocess.run([py, "-c", "import law_agent.cli"], capture_output=True, env=env, cwd=ROOT)
    bad += r.returncode != 0
    print(f"  {'OK ' if r.returncode == 0 else '실패'} law_agent 불러오기")
    print("\n" + ("문제 없음." if not bad else f"확인할 것 {bad}개. 다른폴더_사용시_바꿀것.txt 참고."))
    return 1 if bad else 0


def key_warnings() -> list[str]:
    """키 파일 점검 — 값은 보지 않고 줄 수만 (waste-chat 결합 때 옮김)"""
    f = ROOT / KEY_FILE
    if not f.is_file():
        return [f"{KEY_FILE} 가 없습니다 — setup 또는 직접 만들기"]
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for k in KEY_VARS:
        n = sum(1 for ln in lines if ln.strip().startswith(k + "=") or ln.strip().startswith("export " + k + "="))
        if n > 1:
            out.append(f"{k} 줄이 {n}개입니다 — 하나만 남기세요 (마지막 줄 값이 쓰임)")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("user", "admin"):
        return run_cli(cmd, rest)
    if cmd == "setup":
        return setup(rest)
    if cmd == "doctor":
        return doctor(rest)
    print(f"알 수 없는 명령: {cmd}  (user · admin · setup · doctor)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
