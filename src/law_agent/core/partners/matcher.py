# route_agent/src/partners/matcher.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""업체 매칭 진입점.

  matches = match_routes(routes, case, pre, data)

경로(RouteDraft)마다 RouteMatch 1개. 단계 role별로 매칭 함수를 고른다.
연결된 역할: recycler, preprocessor (재활용 업체), treatment (처분 업체), transporter (운반 업체),
            end_user (관문 1 순환자원 구매처, 추정 데이터 → 확신도 높음·중간만)
표에 없는 역할은 매칭하지 않고 비워 둔다 (경로 확보 판단 안 함).
"""
from __future__ import annotations

from law_agent.core.routes import RouteDraft, Status
from law_agent.core.case_models import PreliminaryResult, WasteCase

from . import evidence, geocode, by_end_user, by_recycler, by_transport, by_treatment, road
from .data import PartnerData
from .geo import haversine_km
from .models import RouteMatch, StepMatch
from .rank import DEFAULT_TOP_N, rank

MATCHERS = {
    "recycler": by_recycler.match,
    "preprocessor": by_recycler.match,
    "treatment": by_treatment.match,
    "transporter": by_transport.match,
    "end_user": by_end_user.match,
}
NO_PARTNER_ROLES = {"applicant"}       # 관문 3 인정 신청: 업체 없음


def _annotate(cands, step, route, case) -> None:
    """순위에 든 업체마다 통과 근거·주의 (evidence.py). 판단은 바꾸지 않는다"""
    from .by_recycler import is_designated_code
    codes = [c for wc in (step.waste_codes or route.waste_codes) for c in wc.split("+")]
    desig = [is_designated_code(c) for c in codes]
    qty = getattr(case, "quantity", None) or {}
    cat = getattr(case, "emitter_category", None)
    for c in cands:
        if c.role == "transporter":
            evidence.transporter(c, codes, desig, qty, cat, getattr(case, "material_form", None))
        elif c.role in ("recycler", "preprocessor"):
            evidence.recycler(c, codes, desig, qty)


def match_routes(routes: list[RouteDraft], case: WasteCase, pre: PreliminaryResult,
                 data: PartnerData, top_n: int = DEFAULT_TOP_N,
                 exclude: dict[int, set[str]] | None = None) -> list[RouteMatch]:
    """exclude: {경로 순번: 뺄 업체_키} — 업체 회신에서 거절한 업체를 빼고 다시 매칭할 때"""
    region = case.region or {}
    origin = data.locator.origin(region.get("sido"), region.get("sigungu"), region.get("address"))
    geocode.refine_origin(origin, region.get("address"), region.get("sido") or "")   # 주소 → 좌표 (못 찾으면 중심점)
    touched = []
    out: list[RouteMatch] = []

    for i, route in enumerate(routes):
        rm = RouteMatch(route_index=i)
        if route.regulatory == Status.REJECTED:          # 법적으로 안 되는 경로는 매칭 안 함
            out.append(rm)
            continue
        for step in route.steps:
            if step.role in NO_PARTNER_ROLES:
                continue
            sm = StepMatch(step_order=step.order, role=step.role,
                           optional=(route.gate == 1 and step.role == "transporter"))
            fn = MATCHERS.get(step.role)
            if step.role == "transporter" and route.gate == 1:
                # 관문 1 순환자원: 폐기물이 아니므로 폐기물 수집·운반업 허가 업체가 필요 없음
                sm.optional = True
                sm.questions.append({"field": "transport_by", "to": "partner",
                                     "text": "순환자원(폐기물 아님)이라 폐기물 운반 허가가 필요 없습니다. 구매 업체가 직접 가져가나요?"})
                rm.steps.append(sm)
                continue
            if fn is not None and step.role == "preprocessor" and not step.treatment_codes:
                # 재질별 선별·분리처럼 처리방법 코드가 없는 단계: 업체 표로 찾을 수 없음
                sm.optional = True
                sm.questions.append({"field": "separation_by", "to": "partner",
                                     "text": f"{step.process}: 배출 사업장에서 직접 하나요, 재활용 업체가 받아서 하나요?"})
                rm.steps.append(sm)
                continue
            if fn is not None and step.role == "treatment" and not step.treatment_codes:
                # 관문 4 "처분 (방법 미정)": 판정표에 처분 방법이 없는 분류번호
                rm.pending = True
                sm.questions.append({"field": "disposal_method", "to": "part2",
                                     "text": "판정표에 이 분류번호의 처분 방법이 없습니다. 처분 방법 확인 후 업체를 찾습니다."})
                rm.steps.append(sm)
                continue
            if fn is None:
                rm.pending = True                        # 아직 연결 안 한 역할 → 확보 여부 판단 안 함
                sm.questions.append({"field": "not_matched_yet", "to": "part2",
                                     "text": f"{step.role} 업체 매칭은 다음 단계에서 연결"})
                rm.steps.append(sm)
                continue
            found, rejected, questions = fn(route, step, data, origin)
            ex = (exclude or {}).get(i) or set()
            if ex:
                before = len(found)
                found = [c for c in found if c.company_id not in ex]
                if before > len(found):
                    rejected["업체 거절 (회신)"] = before - len(found)
            sm.total_found = len(found)
            sm.rejected_summary = dict(rejected)
            # 직선거리로 먼저 줄 세운 뒤 앞쪽 몇 곳만 카카오 도로 거리로 바꾸고 다시 줄 세운다 (호출 수 제한)
            for c in found:                              # 출발점이 주소 좌표로 바뀌었으면 직선거리 다시
                if c.location is not None and c.location.lat is not None and origin.lat is not None:
                    c.location.distance_km = haversine_km((origin.lat, origin.lng), (c.location.lat, c.location.lng))
            geocode.refine(rank(found, top_n=0), origin)  # 앞쪽 후보 중 업체 좌표 없는 곳 → 주소 좌표
            road.enrich(rank(found, top_n=0), origin)
            touched += found
            sm.candidates = rank(found, region.get("sido") or "", region.get("sigungu") or "", top_n)
            _annotate(sm.candidates + list(getattr(sm, "reserve", []) or []), step, route, case)
            if step.role == "treatment" and step.process.startswith("전처리"):
                # 매립 전 전처리: 배출 사업장이 직접 하거나 매립 업체가 같이 하는 경우가 많다
                sm.optional = True
                if not found:
                    questions = questions + [{"field": "pretreatment_by", "to": "partner",
                                              "text": f"{step.process}: 매립 업체에서 같이 하나요, 배출 사업장에서 직접 해야 하나요?"}]
            has_report = any(c.permit_type == "신고" for c in sm.candidates)
            sm.questions = [q for q in questions if q["field"] != "report_scope" or has_report]
            rm.steps.append(sm)
        out.append(rm)
    geocode.forget(touched, origin)                     # 카카오 좌표는 결과에 남기지 않음 (거리만)
    return out
