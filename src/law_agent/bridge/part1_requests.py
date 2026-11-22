"""law_agent 의 '파트 1에 물을 것' → 파트 1 패키지 InformationRequest (schemas/InformationRequest.schema.json) 형식.

필수 칸: request_id, case_id, based_on_case_version, requested_by, fields, trigger_rule_id, reason, question,
         answer_type, blocking_scope (all | classification | route:ID | matching:ID | document:ID)
- request_id 는 (사례·판·필드) 로 만든 uuid5 → 같은 질문을 다시 보내도 같은 번호 (파트 1이 중복을 알아봄)
- fields: 파트 1 칸 이름을 알 수 있으면 그 이름, 아니면 "part2:<law_agent 필드>" (파트 1이 사람에게 물어 답을 받는 칸)
"""
from __future__ import annotations

import re
import uuid

NS = uuid.UUID("6f1c2b0e-9d7a-4c3e-8b52-2a1e5d0f7a11")

# law_agent 사실 이름(필드 뒷부분)의 낱말 → 파트 1 WasteCase 2.0 칸
PART1_FIELD = [
    (r"분류번호|폐기물 종류|말단 번호", "classification.reported_code"),
    (r"월 ?물량|발생량|배출량|물량", "generation.amount"),
    (r"농도|함량|분석|시험|PCB|용출|발열량|수분|염소", "analysis_results"),
    (r"성분|물질|용제|함유", "substances"),
    (r"지정폐기물|유해", "hazard_declaration"),
    (r"혼합|섞", "mixture_type"),
    (r"분리", "separability"),
    (r"형태|성상|상태", "physical_state"),
    (r"공정|발생 ?과정", "process_description"),
    (r"업종|배출시설", "industry"),
]
NUMBER_RX = re.compile(r"농도|함량|톤|kg|양\b|발열량|수분|%")
EVIDENCE_RX = re.compile(r"농도|함량|분석|시험|PCB|용출")


def part1_field(field: str) -> str:
    fact = field.split(".", 1)[1] if re.match(r"^[A-J]-\d+(\.c\d+)?\.", field) else field
    for rx, name in PART1_FIELD:
        if re.search(rx, fact):
            return name
    return f"part2:{field}"


def _scope(r: dict) -> str:
    qids = r.get("question_ids") or [r.get("field", "").split(".")[0]]
    if any(q.startswith("A-1") for q in qids):
        return "classification"
    routes = [x for x in r.get("affected_routes") or [] if x and not re.search(r"[\s:]", x)]
    return f"route:{routes[0]}" if len(routes) == 1 else "all"


def to_part1_requests(result: dict, part1_case_id: str | None = None, case_version: int | None = None) -> list[dict]:
    ir = result.get("information_request") or {}
    case_id = part1_case_id or result.get("case_id")
    ver = int(case_version if case_version is not None else (result.get("case_version") or 0))
    out = []
    for r in ir.get("requests") or []:
        field = r.get("field") or "unknown"
        fact = field.split(".", 1)[-1]
        out.append({
            "request_id": str(uuid.uuid5(NS, f"{case_id}|{ver}|{field}")),
            "case_id": case_id,
            "based_on_case_version": ver,
            "requested_by": "part2",
            "fields": [part1_field(field)],
            "trigger_rule_id": "law_agent:" + ",".join(r.get("question_ids") or [field.split(".")[0]]),
            "reason": r.get("text") or fact,
            "question": r.get("text") or fact,
            "answer_type": "number" if NUMBER_RX.search(fact) else "text",
            "options": [],
            "acceptable_evidence": ["시험성적서", "분석결과서"] if EVIDENCE_RX.search(fact) else [],
            "guidance_id": None,
            "blocking_scope": _scope(r),
            "status": "open",
            "answer_evidence_ids": [],
            "resolution_reason": None,
        })
    return out
