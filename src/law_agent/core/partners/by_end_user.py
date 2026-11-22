# route_agent/src/partners/by_end_user.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""순환자원 구매처 (end_user) 매칭 — demand_designated (관문 1 경로만).

순환자원 지정 고시 품목을 원료로 쓸 것으로 보이는 공장을 찾는다.
실제 구매 기록이 아니라 공장의 업종·생산품으로 고른 추정이라서:
  - 확신도 높음·중간만 보여준다 (낮음은 뺌)
  - 모든 후보에 "실제로 구매하는지" 확인 질문을 붙인다

조건
1. 분류번호가 맞는 공장
2. 고시 용도(R코드)가 경로의 용도와 하나라도 겹치는 공장     → "용도 다름"
3. 확신도 높음·중간                                       → "확신도 낮음"
"""
from __future__ import annotations

from collections import Counter

from law_agent.core.routes import RouteDraft, Step

from .data import PartnerData
from .models import Location, PartnerCandidate

SHOW_CONFIDENCE = {"높음", "중간"}


def match(route: RouteDraft, step: Step, data: PartnerData, origin: Location
          ) -> tuple[list[PartnerCandidate], Counter, list[dict]]:
    codes = [c for wc in (step.waste_codes or route.waste_codes) for c in wc.split("+")]
    want = set(step.r_codes)

    rejected: Counter = Counter()
    found: dict[str, PartnerCandidate] = {}
    for code in codes:
        for r in data.demand_by_code.get(code, []):
            key = r["업체_키"]
            if key in found:
                continue
            uses = set(r["용도_R코드"].split(";"))
            if want and not (uses & want):
                rejected["용도 다름"] += 1
                continue
            if r["확신도"] not in SHOW_CONFIDENCE:
                rejected["확신도 낮음"] += 1
                continue
            c = PartnerCandidate(
                company_id=key,
                name=r["회사명"],
                role=step.role,
                sido=r["시도"],
                sigungu=r["시군구"],
                address=r["공장주소"],
                phone=r["전화번호"] or None,
                data_date="2025",
                source=r["출처"],
                permit_type=None,                        # 순환자원은 폐기물이 아니라 허가 대상 아님
                registration_type=r["대표업종"] or None,
                waste_group=r["품목"],
                r_codes=sorted(uses & want) if want else sorted(uses),
                scope_text=r["생산품"] or None,
                match_confidence=r["확신도"],
                reasons=[f'추정 구매처 (확신도 {r["확신도"]}): {r["근거"]}'],
            )
            c.location = data.locator.company(key, c.sido, c.sigungu, c.address, origin)
            found[key] = c

    questions = []
    if found:
        questions.append({"field": "purchase_confirm", "to": "partner",
                          "text": "업종·생산품으로 고른 추정 구매처입니다. 이 순환자원을 원료로 실제 구매하는지, "
                                  "고시 기준(이물질·보관 상태)을 맞춘 물량을 받는지 확인해 주세요."})
    return list(found.values()), rejected, questions
