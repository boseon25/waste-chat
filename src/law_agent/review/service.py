"""서류 검토 서비스 — waste-chat 이 보낸 서류 칸 값을 법 기준표로 검토 (2026.11.19).

  result = review_form({"case_id": "...", "doc": "contract", "version": 1,
                        "values": {...waste-chat 칸...}, "attachments": ["form5_processor"]})

1. config/review_fields/{doc}.yaml 로 칸 이름을 검토 칸으로 바꾼다 (field_map)
2. case_id 로 이 사례의 최신 판단 결과(RouteProposal)와 사례 값을 찾는다 (context)
3. 기준표(doc_requirements.csv)로 판정한다 (rules — LLM 없음, 같은 입력이면 같은 결과)
4. 결과의 칸 위치를 waste-chat 칸 이름으로 되돌린다. 서식에 없는 칸이면 form_field = null
기준표: WASTE_DATA_DIR/doc_requirements.csv (서버 확정본) → 없으면 review/doc_requirements.csv (초안)
"""
from __future__ import annotations

import os
from pathlib import Path

from . import context, field_map, rules


def requirements() -> list[dict]:
    """서버 확정본 + (확정본에 아직 없는 서류는) 코드 안 초안. 새 서류를 확정 전에도 검토할 수 있게"""
    bundled = rules.load_requirements(None)
    d = os.environ.get("WASTE_DATA_DIR")
    if not (d and (Path(d) / rules.TABLE).is_file()):
        return bundled
    active = rules.load_requirements(d)
    have = {r["doc_type"] for r in active}
    return active + [r for r in bundled if r["doc_type"] not in have]


DONE = ("agreed", "conditional", "confirmed")
FORM_NOTE = ("배출자 신고서는 경우마다 서식이 다릅니다: 계속 배출(시행규칙 제18조제2항제1호) 제6호, "
             "공사·작업(제2호) 제7호, 공동처리(제3호) 제8호. 이 검토는 제6호 기준입니다")


def _designated(found: dict, proposal: dict) -> bool | None:
    st = proposal.get("waste_status")
    if st == "designated":
        return True
    a, s = context.answer(found, "A-2")
    if a == "yes" and s in DONE:
        return True
    if st == "general" or (a == "no" and s in DONE):
        return False
    return None


def applicability(doc_type: str, found: dict, proposal: dict) -> tuple[str | None, str | None]:
    """우리 법률 판단으로 이 서류가 필요한지. (not_applicable | check | None, 안내 문장)"""
    if doc_type == "emitter_report":
        a, s = context.answer(found, "H-1")
        if a == "no" and s in DONE:
            return "not_applicable", "법률 판단(H-1)상 사업장폐기물배출자 신고 대상이 아닙니다"
        if not (a in ("yes", "conditional") and s in DONE):
            return "check", "배출자 신고 대상인지 법률 판단(H-1)에서 아직 확정되지 않았습니다"
        return None, None
    if doc_type == "acceptance_check":
        d = _designated(found, proposal)
        if d is False:
            return "not_applicable", "지정폐기물이 아니어서 처리계획용 수탁확인서가 필요 없습니다 (계약 전에는 수탁처리능력 확인서를 씁니다)"
        if d is None:
            return "check", "지정폐기물인지 법률 판단(A-2)에서 아직 확정되지 않았습니다"
        return None, None
    if doc_type == "hazard_info":
        d = _designated(found, proposal)
        if d is False:
            return "check", "지정폐기물이 아닌 경우 유해성 정보자료 대상이 아닐 수 있습니다. 대상 여부를 확인하세요"
        if d is None:
            return "check", "지정폐기물인지 법률 판단(A-2)에서 아직 확정되지 않았습니다"
    return None, None


def review_form(req: dict, box: Path | None = None, rows: list[dict] | None = None) -> dict:
    doc = str(req.get("doc") or "")
    m = field_map.load(doc)
    if m is None:
        return {"doc": doc, "verdict": "rejected_request", "summary": {}, "findings": [],
                "notes": [f"검토하지 않는 서류입니다: {doc} (가능: {', '.join(field_map.supported())})"]}
    found = context.find(str(req.get("case_id") or ""), box)
    notes = []
    if found is None:
        notes.append("이 사례의 법률 판단 결과를 찾지 못해, 경로·업체·물량 대조 없이 필수 칸·형식만 검토했습니다")
        found = {"case_id": req.get("case_id"), "case_version": None, "proposals": [], "case": {}, "law_review": []}
    proposal = context.pick(found["proposals"], req.get("route_id"))

    fields, origin = field_map.to_review(m, req.get("values") or {})
    request = {"request_id": req.get("request_id"), "case_id": found["case_id"],
               "route_id": proposal.get("route_id"), "doc_type": m["doc_type"],
               "doc_version": req.get("version"), "fields": fields,
               "attachments": field_map.attachments(m, req.get("attachments"))}
    skip = set(m.get("skip") or [])
    use_rows = [r for r in (rows if rows is not None else requirements()) if r["req_id"] not in skip]
    res = rules.review(request, proposal, found["case"], use_rows)

    by_id = {r["req_id"]: r for r in use_rows}
    for f in res.get("findings") or []:
        row = by_id.get(f.get("req_id")) or {}
        f["summary"] = row.get("summary") or ""          # 검토 결과에 쉬운 요약·원문 문장 (2026.11.20: 근거는 검토 때 보여 줌)
        f["sentence"] = row.get("sentence") or ""
        loc = f.get("field")
        f["doc_field"] = loc                             # 검토 칸 위치 (기록용)
        if loc == "attachments":
            f["form_field"] = None
            f["kind"] = "attachment"
            continue
        wc = field_map.form_field(loc, origin)
        if loc and "|" in loc:                           # 기한: 두 칸 (기준일|제출일)
            wcs = [field_map.form_field(x, origin) for x in loc.split("|")]
            wc = "|".join(w or "?" for w in wcs)
        f["form_field"] = wc
        absent = set(m.get("not_in_form") or [])
        if wc and any(w in absent for w in wc.split("|")) and f["problem"] == "missing":
            f["kind"] = "form_has_no_field"
            f["message"] += " (waste-chat 서식에 이 칸이 아직 없습니다)"
        elif loc and wc is None:
            f["kind"] = "form_has_no_field"
            f["message"] += " (waste-chat 서식에 이 칸이 없습니다)"
        elif wc and wc not in (req.get("values") or {}) and f["problem"] == "missing":
            f["kind"] = "empty"
        else:
            f["kind"] = "value"
    from law_agent.review import text_check              # 늦게 불러옴 (guide.service ↔ review.service 순환 방지)
    if text_check.enabled(req):                          # 글 칸 내용: AI 보조 검토 (경고만)
        extra, tnotes = text_check.text_findings(req, use_rows, box)
        res["findings"] = (res.get("findings") or []) + extra
        notes += tnotes
        if extra:
            summ = res.setdefault("summary", {})
            summ["warning"] = summ.get("warning", 0) + len(extra)
            if res.get("verdict") == "pass":
                res["verdict"] = "check_required"
    if m["doc_type"] == "emitter_report":
        notes.append(FORM_NOTE)
    if found["case"]:
        app, why = applicability(m["doc_type"], found, proposal)
        if why:
            notes.append(why)
        if app == "not_applicable":
            res["verdict"] = "not_applicable"
        elif app == "check" and res["verdict"] == "pass":
            res["verdict"] = "check_required"
    res.update({"doc": doc, "doc_title": m.get("title"), "case_version": found["case_version"],
                "notes": notes + (res.get("notes") or []), "engine": "law_agent review v0"})
    return res
