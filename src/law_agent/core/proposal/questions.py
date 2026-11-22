# route_agent/src/proposal/questions.py 에서 복사 (2026.11.17). 바꾼 점: import 경로, 견적 질문 문구(처리비·매입 구분, 2026.11.18)
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""업체에 물을 질문 (v1.1 5절). 역할마다 기본 세트 + 매칭 단계에서 생긴 업체 질문.

field 이름은 파트 3 PartnerFeedback 칸 이름과 같아야 한다 (회신 연결).
배출업체에 물을 것은 여기 넣지 않는다 → info_request.py
"""
from __future__ import annotations

FIELDS = {   # field → 쓰는 계약 버전
    "accept": "1.0", "waste_code_detail": "1.0", "r_code_detail": "1.0", "min_quantity": "1.0",
    "capacity": "1.0", "transport_by": "1.0", "license_check": "1.0",
    "report_scope": "1.1", "input_conditions": "1.1", "quote": "1.1", "separation_by": "1.1",
    "pretreatment_by": "1.1", "purchase_confirm": "1.1", "delivery_window": "1.1",
}
# 매칭 단계 질문 field → 계약 field. None이면 기본 세트로 대신함 (업체 질문 아님 등)
STEP_FIELD = {
    "report_scope": "report_scope", "separation_by": "separation_by", "pretreatment_by": "pretreatment_by",
    "purchase_confirm": "purchase_confirm", "transport_by": "transport_by",
    "acceptance": None,                   # 처분 업체 "반입 기준·물량·단가" → accept/input_conditions/capacity/quote로 나눔
}


def _base(role: str, waste: str, ton: str) -> list[tuple[str, str]]:
    common_tail = [
        ("input_conditions", "반입 기준(성상·이물질·수분·크기·포장)을 알려주세요."),
        ("min_quantity", "1회 최소 반입량은 얼마인가요?"),
        ("quote", "단가를 알려주세요: 처리비(배출업체가 냄)인지 매입가(업체가 냄)인지, 운반비 포함 여부와 운반비를 누가 내는지도 함께."),
        ("delivery_window", "반입 가능 시간과 하차 조건을 알려주세요."),
        ("license_check", "최신 허가증 또는 신고증명서 사본을 보내 줄 수 있나요?"),
    ]
    if role in ("recycler", "preprocessor"):
        return [("accept", f"{waste} {ton}을 위탁받아 재활용할 수 있나요?"),
                ("waste_code_detail", "해당 분류번호(말단 번호)까지 허가·신고가 되어 있나요?"),
                ("r_code_detail", "허가·신고된 세부 재활용 유형(R코드)은 무엇인가요?"),
                ("capacity", f"{ton}을 받을 처리 여력이 있나요? (수탁처리능력 확인서 발급 가능 여부)"),
                ("transport_by", "운반을 직접 하나요, 별도 운반 업체가 필요한가요?")] + common_tail
    if role == "treatment":
        return [("accept", f"{waste} {ton}을 위탁받아 처리할 수 있나요?"),
                ("capacity", f"{ton}을 받을 처리 여력이 있나요? (수탁처리능력 확인서 발급 가능 여부)")] + common_tail
    if role == "end_user":
        return [("purchase_confirm", f"{waste}을(를) 원료로 실제 구매하나요? 월 {ton} 규모를 받을 수 있나요?"),
                ("input_conditions", "고시 기준(이물질·보관 상태) 외에 요구하는 품질 조건이 있나요?"),
                ("quote", "매입 단가(또는 무상 인수 여부)를 알려주세요."),
                ("transport_by", "직접 가져가나요, 운반을 따로 맡겨야 하나요?")]
    return []


def build_questions(main_step: dict | None, steps: list[dict], waste: str, ton: str) -> list[dict]:
    """경로 하나의 업체 질문 목록. main_step = 추천 업체가 있는 가공·처분·구매 단계"""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(role: str, field: str, text: str, company_id: str | None = None) -> None:
        if (role, field) in seen:
            return
        seen.add((role, field))
        out.append({"question_id": f"Q-{len(out) + 1}", "to_role": role, "company_id": company_id,
                    "field": field, "text": text})

    if main_step is not None:
        has_report = any(c.get("permit_type") == "신고" for c in main_step["candidates"])
        for field, text in _base(main_step["role"], waste, ton):
            add(main_step["role"], field, text)
        if has_report:
            add(main_step["role"], "report_scope",
                "폐기물처리 신고 업체: 신고 범위(시행규칙 별표 16)와 하루 처리량 기준 안에서 받을 수 있나요?")
    for s in steps:                                    # 매칭 단계에서 생긴 업체 질문 (선별, 전처리 등)
        if s["role"] == "transporter":
            continue                                   # 운반은 협력 확정 후 운송 건에서 따로
        for q in s.get("questions") or []:
            if q.get("to") != "partner":
                continue
            field = STEP_FIELD.get(q["field"], q["field"])
            if field:
                add(s["role"], field, q["text"])
    return out
