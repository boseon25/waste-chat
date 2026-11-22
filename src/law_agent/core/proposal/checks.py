# route_agent/src/proposal/checks.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""checks 세 칸 (v1.1 4절). 세 칸을 따로 두고, 하나라도 confirmed가 아니면 검증 완료로 보지 않는다.

- regulatory : 관문 판정 그대로
- technical  : 가공 사례 DB가 없어 기본 needs_info.
               관문 1(지정 순환자원)은 고시가 용도·방법 기준을 정해 두어 conditional
- acceptance : 업체 회신 전 needs_info.
               회신 반영: 한 곳이라도 수락 → confirmed / 조건부 수락 → conditional / 모두 거절 → rejected
"""
from __future__ import annotations

ACCEPTANCE_ORDER = {"confirmed": 0, "conditional": 1, "needs_info": 2, "rejected": 3}


def technical(route: dict) -> tuple[str, str]:
    if route["gate"] == 1 and route["regulatory"] in ("confirmed", "conditional"):
        return "conditional", "순환자원 지정 고시의 용도·방법 기준을 따름. 실제 가공 사례 대조 전"
    return "needs_info", "가공 사례 대조 전 (사례 데이터 없음)"


def acceptance(candidates: list[dict]) -> tuple[str, str]:
    statuses = [c.get("status", "needs_info") for c in candidates]
    if not statuses:
        return "needs_info", "후보 업체 없음"
    if "confirmed" in statuses:
        return "confirmed", "수락한 업체 있음"
    if "conditional" in statuses:
        return "conditional", "조건부로 수락한 업체 있음"
    if all(s == "rejected" for s in statuses):
        return "rejected", "후보 업체가 모두 거절 → 다른 업체 검색 필요"
    return "needs_info", "업체 회신 전"


def build_checks(route: dict, main_candidates: list[dict]) -> tuple[dict, dict]:
    """반환: (checks, 이유)"""
    t, t_why = technical(route)
    a, a_why = acceptance(main_candidates)
    return ({"technical": t, "regulatory": route["regulatory"], "acceptance": a},
            {"technical": t_why, "regulatory": "; ".join(route.get("reasons") or []) or None, "acceptance": a_why})
