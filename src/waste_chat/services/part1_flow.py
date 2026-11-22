"""Word 기초 입력서 → waste_agent 파트 1 절차 (law_agent 사용자 화면이 하던 일을 waste-chat 화면으로).

  read(me, 파일)                 db_intake ingest → 입력서의 분류번호 칸 기록 → 폐기물 건 목록
  item(me, sid, 건)              phase3 export → (law_agent part1_v2.convert) 판단 입력(1.0) → 화면 사실·근거·파트 1 질문
  answer / set_code / attach     phase2 answer · part1 classification · part1 attach-history
  engine_case(me, sid, 건)       시작할 때 다시 내보내 최신 답을 넣은 판단 입력

파트 1 명령은 별도 프로세스로 부르고(패키지 그대로), 접수 DB는 업체마다 data/part1/<업체>/ 에 둔다.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import intake_flow, intake_handoff, paths, store, tables

RX_ID = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
RX_QID = re.compile(r"질문 ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
HIST_KIND = {"declaration": "배출자 신고서·신고증명서", "transfer": "인계서(올바로)", "treatment": "처리실적·위탁 계약"}
FIELD_KO = {  # 파트 1 질문 칸 → 쉬운 이름(없으면 칸 이름 그대로)
    "quantity.measured_at": "처리량 측정일", "quantity.value": "이번 처리량", "hazard_declaration": "유해성 여부",
    "components": "구성 성분", "visible_contamination": "겉으로 보이는 오염", "mixture_type": "섞임 여부",
    "physical_state": "겉모양", "process_description": "발생 공정", "classification.reported_code": "분류번호",
}


class Part1Error(ValueError):
    pass


def _work(me_id):
    if not RX_ID.match(me_id or ""):
        raise Part1Error("잘못된 업체입니다.")
    d = store.DATA_DIR / "part1" / me_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db(me_id, sid):
    if not RX_ID.match(sid or ""):
        raise Part1Error("잘못된 접수 번호입니다.")
    db = _work(me_id) / "db" / f"{sid}.sqlite3"
    if not db.is_file():
        raise Part1Error("접수한 입력서를 찾지 못했습니다. 입력서를 다시 올려 주세요.")
    return db


def p1(script, *args):
    r = subprocess.run([sys.executable, str(paths.PART1 / script), *args], cwd=str(paths.PART1), text=True,
                       capture_output=True, timeout=120, env={**os.environ, "PYTHONUTF8": "1"})
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _last(out):
    lines = [ln for ln in (out or "").strip().splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "알 수 없는 오류"


def cases(me_id, sid):
    code, out = p1("phase3.py", "cases", "--db", str(_db(me_id, sid)))
    try:
        return json.loads(out[out.index("["):]) if code == 0 else []
    except ValueError:
        return []


def _case_ok(me_id, sid, case):
    if not any(c["case_id"] == case for c in cases(me_id, sid)):
        raise Part1Error("이 입력서에 없는 폐기물입니다.")


def questions(me_id, sid, case):
    """파트 1이 지금 묻는 질문(한 번에 3개까지)."""
    code, out = p1("phase2.py", "next", "--db", str(_db(me_id, sid)), "--case", case, "--limit", "3")
    qs, cur = [], None
    for line in out.splitlines():
        m = RX_QID.search(line)
        if m:
            parts = line.split(" / ")
            field = parts[1].strip() if len(parts) > 1 else ""
            cur = {"id": m.group(1), "field": field, "label": FIELD_KO.get(field, field), "text": "", "hint": ""}
            qs.append(cur)
        elif cur is not None and line.strip() and not line.startswith(("phase2.py", "현재 질문", "사용자 진술")):
            if not cur["text"]:
                cur["text"] = line.strip()
            elif line.startswith("현재 후보값"):
                hint = line.split(":", 1)[-1].strip()
                cur["hint"] = "" if hint in ("None", "null") else hint
    return qs


def revision(me_id, sid, case):
    code, out = p1("phase2.py", "status", "--db", str(_db(me_id, sid)), "--case", case)
    try:
        return int(json.loads(out[out.index("{"):]).get("draft_revision") or 0)
    except ValueError:
        return 0


def _convert(export_path):
    """law_agent 의 파트 1(2.0) → 판단 입력(1.0) 변환을 그대로 쓴다(엔진이 받는 것과 같은 결과)."""
    code = ("import json,sys; from law_agent.core import part1_v2; "
            "d=json.load(open(sys.argv[1],encoding='utf-8')); print(json.dumps(part1_v2.convert(d),ensure_ascii=False))")
    r = subprocess.run([sys.executable, "-c", code, str(export_path)], cwd=str(paths.SRC), text=True, capture_output=True,
                       timeout=60, env={**os.environ, "PYTHONPATH": str(paths.SRC), "PYTHONUTF8": "1"})
    if r.returncode != 0:
        raise Part1Error("파트 1 내보내기를 판단 입력으로 바꾸지 못했습니다: " + _last(r.stderr))
    return json.loads(r.stdout)["cases"][0]


def engine_case(me_id, sid, case):
    """phase3 export → 판단 입력(1.0) 한 건. 파트 1 답·분류번호·첨부가 모두 반영된 최신본."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "export.json"
        code, msg = p1("phase3.py", "export", "--db", str(_db(me_id, sid)), "--case", case, "--out", str(out))
        if code != 0 or not out.is_file():
            raise Part1Error("파트 1 내보내기 실패: " + _last(msg))
        return _convert(out)


def item(me, sid, row, index):
    me_id = me["id"]
    c1 = engine_case(me_id, sid, row["case_id"])
    facts = intake_handoff.facts_of(c1)
    code = facts.get("code", "")
    note = ""
    if code:
        good, msg = tables.check_code(code)
        note = f"입력서·파트 1에 기록된 분류번호 {code}" + ("" if good else f" — {msg}")
        if not good:
            facts.pop("code")
    view = intake_flow.item_view({"index": index, "facts": facts, "sources": intake_handoff.sources_of(c1)}, me["profile"])
    cl = c1.get("classification") or {}
    view.update({"handoff": c1, "code_fixed": bool(facts.get("code")), "code_label": "", "code_basis": cl.get("status", ""),
                 "code_conditions": "", "code_note": note or "입력서에 분류번호가 없습니다. 알고 있으면 아래에 근거와 함께 적어 주세요.",
                 "part1": {"sid": sid, "case": row["case_id"]}, "p1q": questions(me_id, sid, row["case_id"]),
                 "history_kinds": HIST_KIND})
    return view


def read(me, blob, filename):
    """입력서 접수 → 폐기물 건마다 화면 항목."""
    me_id = me["id"]
    sid = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{os.urandom(3).hex()}"
    up = _work(me_id) / "uploads" / sid
    up.mkdir(parents=True, exist_ok=True)
    src = up / (re.sub(r"[^\w.\-가-힣]", "_", Path(filename or "intake.docx").name)[:80] or "intake.docx")
    if src.suffix.lower() != ".docx":
        src = src.with_suffix(".docx")
    src.write_bytes(blob)
    db = _work(me_id) / "db" / f"{sid}.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    code, out = p1("db_intake.py", "ingest", str(src), "--db", str(db))
    if code != 0:
        raise Part1Error("입력서를 읽지 못했습니다: " + _last(out))
    _record_codes(me_id, sid, src)
    rows = cases(me_id, sid)
    if not rows:
        raise Part1Error("입력서에 작성된 폐기물이 없습니다.")
    company = {}
    try:
        from . import intake_docx
        company = intake_docx.read(blob).get("company") or {}
    except ValueError:
        pass
    return {"sid": sid, "company": company, "items": [item(me, sid, r, i) for i, r in enumerate(rows, 1)]}


def _record_codes(me_id, sid, src):
    """입력서의 '분류번호' 칸 값(있으면)을 파트 1에 근거 있음으로 기록 — law_agent 사용자 화면과 같은 방식."""
    sys.path.insert(0, str(paths.SRC)) if str(paths.SRC) not in sys.path else None
    from law_agent.bridge.docx_codes import docx_codes
    codes = docx_codes(src)
    for c in cases(me_id, sid) if codes else []:
        code = codes.get(c.get("table_index"))
        if code:
            p1("part1.py", "classification", "--db", str(_db(me_id, sid)), "--case", c["case_id"], "supported",
               "--code", code, "--reference", "입력서 기재 분류번호")


def refreshed(me, sid, case):
    rows = cases(me["id"], sid)
    for i, r in enumerate(rows, 1):
        if r["case_id"] == case:
            return item(me, sid, r, i)
    raise Part1Error("이 입력서에 없는 폐기물입니다.")


def answer(me, sid, case, qid, value):
    _case_ok(me["id"], sid, case)
    if not RX_QID.search(f"질문 {qid}"):
        raise Part1Error("잘못된 질문입니다.")
    value = str(value or "").strip()[:400]
    if not value:
        raise Part1Error("답을 적거나 ‘모름’을 누르세요.")
    code, out = p1("phase2.py", "answer", "--db", str(_db(me["id"], sid)), "--case", case, "--request", qid,
                   "--revision", str(revision(me["id"], sid, case)), value)
    if code != 0:
        raise Part1Error("저장하지 못했습니다: " + _last(out))
    return refreshed(me, sid, case)


def set_code(me, sid, case, code, reference):
    _case_ok(me["id"], sid, case)
    good, msg = tables.check_code(code)
    if not good:
        raise Part1Error(msg)
    ref = str(reference or "").strip()[:200] or "사용자 입력"
    rc, out = p1("part1.py", "classification", "--db", str(_db(me["id"], sid)), "--case", case, "supported",
                 "--code", code, "--reference", ref)
    if rc != 0:
        raise Part1Error("분류번호를 기록하지 못했습니다: " + _last(out))
    return refreshed(me, sid, case)


def attach(me, sid, case, kind, issuer, blob, filename):
    _case_ok(me["id"], sid, case)
    if kind not in HIST_KIND:
        raise Part1Error("문서 종류를 고르세요.")
    d = _work(me["id"]) / "uploads" / sid / "attach"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / (re.sub(r"[^\w.\-가-힣]", "_", Path(filename or "doc").name)[:80] or "doc")
    dst.write_bytes(blob)
    rc, out = p1("part1.py", "attach-history", "--db", str(_db(me["id"], sid)), "--case", case, "--kind", kind,
                 "--issuer", str(issuer or "").strip()[:80] or "미기재", str(dst))
    if rc != 0:
        raise Part1Error("첨부하지 못했습니다: " + _last(out))
    return refreshed(me, sid, case)
