"""관문 3 — 순환자원 개별 인정 (장기 후보).

지정 순환자원 품목은 아니지만, 순환자원 인정을 따로 신청할 수 있는 후보인지 본다.
지금 바로 실행하는 경로가 아니라 "장기 검토 후보 + 확인할 질문"으로만 내보낸다.

판정표: gate3_rules (자원순환법 제21·23조, 시행령 제11조, 인정 고시 — 초안, 사람 검토 전)
입력:   WasteCase(파트 1 case), PreliminaryResult(사전검증)
        인정 기준 사실(유상 거래, 용도 등)은 파트 1 계약에 없다.
        있으면 case.raw 맨 위 또는 case.raw["gate3_facts"]에서 읽는다.
출력:   list[RouteDraft] (후보 1개 또는 빈 목록)
        - 제외 기준에 걸리면 REJECTED 후보 1개 (이유 표시)
        - 모르는 사실이 있으면 NEEDS_INFO + 질문
        - 초안 표 + 장기 후보라 confirmed는 나오지 않음 (최대 conditional)
"""
from __future__ import annotations

from preliminary.models import PreliminaryResult, WasteCase

from .models import RouteDraft, Status, Step
from .tables import Tables

# 용도가 이 말을 포함하면 인정 제외 (시행령 제11조 제1호 가·나·다목)
EXCLUDED_USES = {
    "G3-D11-1가": ("소각", "매립", "해역"),
    "G3-D11-1나": ("연료", "에너지", "고형연료", "SRF"),
    "G3-D11-1다": ("성토", "복토", "도로", "기층", "채움"),
}
ENERGY_EXCEPTION_CODES = {"51-19-00", "51-17-25"}      # 왕겨·쌀겨, 커피찌꺼기는 연료 용도 예외
TRUE_IS_BAD = {"is_transboundary_regulated", "handling_risk"}   # 나머지 칸은 false가 나쁜 값

DEFAULT_QUESTIONS = {
    "intended_use": "이 폐기물을 어떤 용도로 쓰려고 하나요? (예: 제품 원료, 연료, 성토재)",
    "notified_use_criteria": "고시된 순환자원 기준·용도에 맞게 쓸 수 있나요?",
    "is_transboundary_regulated": "수출입 규제 폐기물에 해당하나요?",
    "substitutes_resource": "다른 원료를 대신해서 통상적으로 재활용되나요?",
}


def _fact(case: WasteCase, key: str):
    raw = case.raw or {}
    facts = raw.get("gate3_facts") or {}
    if key.startswith("analysis."):
        sub = key.split(".", 1)[1]
        return (case.analysis or {}).get(sub, facts.get(key))
    return facts.get(key, raw.get(key))


def run(case: WasteCase, pre: PreliminaryResult, tables: Tables) -> list[RouteDraft]:
    if pre.waste_status != "general" or pre.restriction.recycling_blocked:
        return []                                   # 사전검증에서 보통 SKIP

    code = pre.classification.normalized_waste_code
    route = RouteDraft(
        gate=3,
        tier=None,
        route_type="direct_use",
        title="순환자원 개별 인정 신청 (장기 검토 후보)",
        waste_codes=[code],
        waste_status="general",
        steps=[Step(1, "순환자원 인정 신청 (배출자 또는 처리업자)", "applicant", waste_codes=[code])],
        regulatory=Status.CONDITIONAL,
        reasons=["지금 실행하는 경로가 아니라 장기 검토 후보", "인정 기준 표가 초안(사람 검토 전)"],
        basis_status="검토 대기",
    )

    rows = [r for r in tables.designated if r["분류번호"] == code]
    if rows and all(r["품목_한정"] == "N" for r in rows):
        route.reasons.append("이미 지정 순환자원 품목이라 관문 1이 우선")

    for rule in tables.gate3:
        key, on_fail = rule["판별_입력"], rule["불충족시"]
        if on_fail == "NOTE" or key.startswith("(") or key in ("is_hazardous", "is_designated_waste"):
            continue                                # 참고 항목, 하위 규칙 참조, 지정폐기물(이미 걸러짐)
        route.evidence_ids.append(rule["근거_청크"] or rule["rule_id"])
        value = _fact(case, key)

        if key == "intended_use":
            if value is None:
                if rule["rule_id"] == "G3-D11-1가":     # 같은 질문을 한 번만
                    route.ask(key, DEFAULT_QUESTIONS[key])
                    route.cap(Status.NEEDS_INFO, "사용 용도 미확인")
                continue
            words = EXCLUDED_USES.get(rule["rule_id"], ())
            if any(w in str(value) for w in words):
                if rule["rule_id"] == "G3-D11-1나" and code in ENERGY_EXCEPTION_CODES:
                    continue
                route.cap(Status.REJECTED, f"{rule['rule_id']}: {rule['조건']}")
            continue

        # true가 나쁜 값인 칸(위험 있음, 규제 대상)과 false가 나쁜 값인 칸(유상 거래 가능 등)을 구분
        bad = (value is True) if key in TRUE_IS_BAD else (value is False)
        if value is None:
            route.ask(key, rule["질문"] or DEFAULT_QUESTIONS.get(key, rule["조건"]))
            route.cap(Status.NEEDS_INFO, f"{rule['rule_id']} 확인 필요")
        elif bad:
            # 유상 거래 안 됨, 유해물질 기준 초과, 수출입 규제 해당, 보관·운반 위험 있음
            if on_fail == "EXCLUDE":
                route.cap(Status.REJECTED, f"{rule['rule_id']}: {rule['조건']}")
            else:
                route.cap(Status.NEEDS_INFO, f"{rule['rule_id']}: {rule['조건']} — 충족하지 못할 수 있음")

    route.evidence_ids = sorted(set(route.evidence_ids))
    route.conditions.append({
        "text": "기후에너지환경부에 순환자원 인정 신청 후 인정을 받아야 폐기물에서 제외됨",
        "basis": "순환경제사회 전환 촉진법 제21조", "basis_status": "검토 대기",
    })
    return [route]
