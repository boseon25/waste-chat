"""case_id → 그 사례의 최신 판단 결과(RouteProposal)와 사례 값.

law_agent 가 사례를 처리하면 outbox 에 남기는 파일을 읽는다 (bridge/watch.py):
  {case_id}_v{ver}_proposals.json   RouteProposal 목록
  {case_id}_v{ver}_case.json        판단에 쓴 사례 값 (1.0)
  {case_id}_v{ver}_result.json      전체 결과 (law_review: 질문별 판단 — 서류 필요 여부에 씀)
파트 1 사례 번호(part1_v2.case_id)로 물어도 찾는다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def outbox() -> Path:
    root = Path(os.environ.get("ROOT") or Path.cwd())
    return Path(os.environ.get("WASTE_OUTBOX") or root / "data" / "bridge" / "outbox")


def _ver(p: Path) -> int:
    m = re.search(r"_v(\d+)_[a-z]+\.json$", p.name)
    return int(m.group(1)) if m else -1


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def find(case_id: str, box: Path | None = None) -> dict | None:
    """{case_id, case_version, proposals, case} 또는 None"""
    box = box or outbox()
    if not case_id or not box.is_dir() or not re.fullmatch(r"[\w\-.:가-힣]+", case_id):
        return None
    files = sorted(box.glob(f"{case_id}_v*_case.json"), key=lambda p: (_ver(p), p.stat().st_mtime))
    if not files:                                       # 파트 1 사례 번호로 물은 경우
        for p in sorted(box.glob("*_case.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            c = _load(p) or {}
            if (c.get("part1_v2") or {}).get("case_id") == case_id:
                files = [p]
                break
    if not files:
        return None
    case_file = files[-1]
    stem = case_file.name[: -len("_case.json")]
    case = _load(case_file) or {}
    proposals = _load(case_file.with_name(stem + "_proposals.json")) or []
    result = _load(case_file.with_name(stem + "_result.json")) or {}
    return {"case_id": case.get("case_id") or case_id, "case_version": _ver(case_file),
            "proposals": proposals if isinstance(proposals, list) else [], "case": case,
            "law_review": result.get("law_review") or []}


def answer(found: dict, qid: str) -> tuple[str | None, str | None]:
    """법률 판단 질문 하나의 (답, 상태). 없으면 (None, None)"""
    for e in found.get("law_review") or []:
        if e.get("question_id") == qid:
            return e.get("answer"), e.get("status")
    return None, None


def pick(proposals: list[dict], route_id: str | None = None) -> dict:
    """요청한 경로, 없으면 첫 경로 (범위가 중간 가공 하나라 보통 1개). 없으면 빈 dict"""
    if route_id:
        for p in proposals:
            if p.get("route_id") == route_id:
                return p
    return proposals[0] if proposals else {}


def recent(box: Path | None = None, limit: int = 20) -> list[dict]:
    """최근 처리한 사례 목록 (새것부터): case_id, 이름, 버전, 파트 1 번호, 처리 시각"""
    box = box or outbox()
    if not box.is_dir():
        return []
    out, seen = [], set()
    for p in sorted(box.glob("*_case.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        c = _load(p) or {}
        cid = c.get("case_id") or p.name.split("_v")[0]
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"case_id": cid, "name": c.get("name"), "case_version": _ver(p),
                    "part1_case_id": (c.get("part1_v2") or {}).get("case_id"), "updated": int(p.stat().st_mtime)})
        if len(out) >= limit:
            break
    return out
