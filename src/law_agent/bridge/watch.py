"""공유 폴더 감시 — 파트 1 → 파트 2 → (파트 1·파트 3) 자동 연동.

  PYTHONPATH=src python3 -m law_agent.bridge.watch --inbox data/bridge/inbox --outbox data/bridge/outbox
  옵션: --once (지금 있는 파일만 처리하고 끝) · --interval 5 (초) · --gate-only · --no-partners · --dry (가짜 LLM)
        -v (노드마다 실시간 로그) · --log 파일 (화면 출력을 파일에도, 기본 outbox/live.log)
  다른 터미널에서 실시간 보기: tail -f data/bridge/outbox/live.log

inbox 에 들어오는 파일 (파트 1)
  - phase3.py export (case_document_review.json), db_intake.py export, Handoff, 또는 1.0 계약 JSON
  - 다 쓴 뒤 넣어야 한다 (쓰는 중인 파일을 읽지 않게, 크기가 2번 연속 같을 때만 읽음)
outbox 에 쓰는 파일 (사례마다, v = case_version)
  - {사례}_v{v}_proposals.json : 파트 3 RouteProposal 목록
  - {사례}_v{v}_requests.json  : 파트 1 InformationRequest 목록 (파트 1 스키마 그대로)
  - {사례}_v{v}_result.json    : 전체 결과 (기록·show_result 용)
  - {사례}_v{v}_cards.json / _cards.txt / _cards.html : 사용자용 법률 카드 (결론 · 해야 할 일 · 확인할 것)
처리한 입력은 inbox/done/ 으로 옮김. 실패하면 inbox/failed/ + outbox/{파일}_error.txt
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from law_agent.bridge.part1_requests import to_part1_requests


def _ready(path: Path, sizes: dict[Path, int]) -> bool:
    """쓰는 중인 파일 거르기: 지난번 본 크기와 같으면 다 쓴 것으로 본다."""
    size = path.stat().st_size
    prev = sizes.get(path)
    sizes[path] = size
    return prev == size and size > 0


class Tee:
    """화면 + 로그 파일에 같이 쓰기 (줄마다 바로 flush → tail -f 로 실시간)."""

    def __init__(self, stream, path: Path):
        self.stream, self.f = stream, open(path, "a", encoding="utf-8", buffering=1)

    def write(self, s):
        self.stream.write(s)
        self.f.write(s)
        return len(s)

    def flush(self):
        self.stream.flush()
        self.f.flush()


def process_file(path: Path, outbox: Path, partner_data, factors, top: int = 5, verbose: bool = False) -> list[Path]:
    from law_agent.core import part1_v2
    from law_agent.run import run_case, to_jsonable
    original = json.loads(path.read_text(encoding="utf-8"))
    data = part1_v2.convert(original) if part1_v2.is_v2(original) else original
    cases = data.get("cases") if isinstance(data, dict) and "cases" in data else [data]
    written = []
    for raw in cases:
        res = run_case(raw, partner_data, factors, top, verbose=verbose, audit=True)
        cid, ver = res["case_id"], res.get("case_version") or 0
        part1_id = (raw.get("part1_v2") or {}).get("case_id") or cid
        files = {
            "proposals": res.get("route_proposals") or [],
            "requests": {"case_id": part1_id, "based_on_case_version": ver, "requested_by": "part2",
                         "requests": to_part1_requests(res, part1_id, ver)},
            "result": res,
            "input": original,          # 파트 1이 보낸 그대로 (관리자 화면의 '파트 1' 부분)
            "case": raw,                # 파트 2가 판단에 쓴 값 (변환 결과)
            "cards": res.get("law_cards"),   # 사용자용 법률 카드 (결론 · 해야 할 일 · 확인할 것)
        }
        for kind, body in files.items():
            f = outbox / f"{cid}_v{ver}_{kind}.json"
            f.write_text(json.dumps(to_jsonable(body), ensure_ascii=False, indent=1), encoding="utf-8")
            written.append(f)
        if res.get("law_cards"):
            from law_agent.output.plain import show_cards
            f = outbox / f"{cid}_v{ver}_cards.txt"
            f.write_text(show_cards(res["law_cards"]) + "\n", encoding="utf-8")
            written.append(f)
            from law_agent.output.cards_html import render
            f = outbox / f"{cid}_v{ver}_cards.html"
            f.write_text(render(res["law_cards"], f"{cid} v{ver}"), encoding="utf-8")
            written.append(f)
        n_req = len(files["requests"]["requests"])
        sts = ", ".join(f"관문{p.get('gate')} {p['checks']['regulatory']}" for p in files["proposals"]) or "경로 없음"
        print(f"  ✔ {cid} v{ver}: {sts} · 파트 1 질문 {n_req}개 · 호출 {(res.get('run') or {}).get('calls', 0)}회", flush=True)
    return written


def _claim(path: Path) -> Path | None:
    """처리할 파일을 먼저 집는다 (이름을 .working 으로 바꾸기 — 한 번에 한 프로세스만 성공).
    감시를 두 개 켰거나 send 와 감시가 겹쳐도 같은 파일을 두 번 처리하지 않는다 (2026.11.18 서버 오류)."""
    work = path.with_name(path.name + ".working")
    try:
        os.rename(path, work)
    except (FileNotFoundError, FileExistsError, PermissionError):
        return None     # 다른 프로세스가 이미 집어 감 (윈도우: 아직 쓰는 중이면 PermissionError → 다음 회차에)
    return work


def _move(src: Path, dst_dir: Path, name: str) -> None:
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / name
    try:
        if dst.exists():
            dst.unlink()                                # 윈도우는 같은 이름이 있으면 옮기기 실패
        shutil.move(str(src), dst)
    except FileNotFoundError:
        pass


def scan(inbox: Path, outbox: Path, partner_data, factors, sizes: dict, once: bool, verbose: bool = False) -> int:
    done, failed = inbox / "done", inbox / "failed"
    n = 0
    for path in sorted(inbox.glob("*.json")):
        if not once and not _ready(path, sizes):
            continue
        sizes.pop(path, None)
        work = _claim(path)
        if work is None:
            continue
        print(f"■ {time.strftime('%H:%M:%S')} {path.name}", flush=True)
        try:
            process_file(work, outbox, partner_data, factors, verbose=verbose)
            _move(work, done, path.name)
        except Exception as e:                          # noqa: BLE001 — 한 파일 실패가 감시를 멈추지 않게
            (outbox / f"{path.stem}_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            _move(work, failed, path.name)
            print(f"  ✘ 실패: {type(e).__name__}: {e} → inbox/failed/", flush=True)
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", default="data/bridge/inbox")
    ap.add_argument("--outbox", default="data/bridge/outbox")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--gate-only", action="store_true")
    ap.add_argument("--no-partners", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("-v", "--verbose", action="count", default=0, help="실시간 로그 (-v 쉬운 말, -vv 노드·시간·호출 수)")
    ap.add_argument("--log", help="로그 파일 (기본 outbox/live.log, 끄려면 --log none)")
    a = ap.parse_args(argv)
    import os
    if a.gate_only:
        os.environ["LAW_AGENT_GATE_ONLY"] = "1"
    from law_agent.run import load_partners, use_dry_llm
    if a.dry:
        use_dry_llm()
    inbox, outbox = Path(a.inbox), Path(a.outbox)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    if (a.log or "").lower() != "none":
        log = Path(a.log) if a.log else outbox / "live.log"
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)
    partner_data, factors = load_partners(a.no_partners)
    sizes: dict[Path, int] = {}
    print(f"감시 시작: {inbox} → {outbox}" + ("" if a.once else f" ({a.interval:g}초 간격, 멈추려면 Ctrl+C)"), flush=True)
    try:
        while True:
            scan(inbox, outbox, partner_data, factors, sizes, a.once, a.verbose)
            if a.once:
                return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("감시 종료", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
