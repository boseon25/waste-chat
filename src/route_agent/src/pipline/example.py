
from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

# 실제 구현할 기능 모듈
from src.part2_routes.preliminary.preliminary_checker import (
    run_preliminary_check,
)
from src.part2_routes.gates.gate1 import run_gate1
from src.part2_routes.gates.gate2.processor import run_gate2
from src.part2_routes.gates.gate3 import run_gate3
from src.part2_routes.gates.gate4 import run_gate4
from src.part2_routes.validation.technical import validate_technical
from src.part2_routes.validation.regulatory import validate_regulatory
from src.part2_routes.validation.condition import validate_conditions
from src.part2_routes.matching.partner_matcher import match_partners
from src.part2_routes.matching.transport import match_transport
from src.part2_routes.matching.capacity import validate_capacity
from src.part2_routes.carbon.calculator import calculate_carbon
from src.part2_routes.comparison.route_comparator import compare_routes


class RouteState(TypedDict, total=False):
    """Part 2 전체 파이프라인 상태."""

    # 입력
    waste_case: dict[str, Any]

    # 기본 검토
    preliminary_result: dict[str, Any]
    is_prohibited: bool
    is_hazardous: bool

    # 관문별 결과
    route_candidates: list[dict[str, Any]]
    gate3_candidates: list[dict[str, Any]]

    # 검증 결과
    validated_routes: list[dict[str, Any]]
    rejected_routes: list[dict[str, Any]]
    needs_info: list[dict[str, Any]]

    # 업체 매칭 결과
    matched_routes: list[dict[str, Any]]

    # 탄소 계산 및 비교
    carbon_results: list[dict[str, Any]]
    compared_routes: list[dict[str, Any]]

    # 최종 결과
    route_proposal: dict[str, Any]
    pipeline_status: str
    errors: list[str]


# --------------------------------------------------
# 1. 기본 검토
# --------------------------------------------------

def preliminary_node(state: RouteState) -> dict:
    """금지·제한 및 지정폐기물 검사를 독립적으로 실행."""

    result = run_preliminary_check(state["waste_case"])

    return {
        "preliminary_result": result,
        "is_prohibited": result["prohibited"]["is_prohibited"],
        "is_hazardous": result["hazardous"]["is_hazardous"],
    }


# --------------------------------------------------
# 2. 관문 실행 분기
# --------------------------------------------------

def route_after_preliminary(
    state: RouteState,
) -> Literal["gate4", "gate1", "gate2"]:
    """기본 검토 결과에 따라 실행할 관문 결정."""

    if state["is_prohibited"]:
        return "gate4"

    if state["is_hazardous"]:
        return "gate2"

    return "gate1"


# --------------------------------------------------
# 3. 관문 1
# --------------------------------------------------

def gate1_node(state: RouteState) -> dict:
    """지정 순환자원 활용 경로 생성."""

    routes = run_gate1(
        waste_case=state["waste_case"],
        preliminary_result=state["preliminary_result"],
    )

    return {
        "route_candidates": routes,
    }


# --------------------------------------------------
# 4. 관문 2
# --------------------------------------------------

def gate2_node(state: RouteState) -> dict:
    """재사용·수리 → 재생이용 → 에너지 회수."""

    routes = run_gate2(
        waste_case=state["waste_case"],
        preliminary_result=state["preliminary_result"],
    )

    existing = state.get("route_candidates", [])

    return {
        "route_candidates": existing + routes,
    }


# --------------------------------------------------
# 5. 관문 3
# --------------------------------------------------

def gate3_node(state: RouteState) -> dict:
    """순환자원 인정 가능성 후보 생성.

    장기 검토 후보이며 즉시 실행 가능한 경로와 구분한다.
    """

    candidates = run_gate3(
        waste_case=state["waste_case"],
        preliminary_result=state["preliminary_result"],
    )

    return {
        "gate3_candidates": candidates,
    }


# --------------------------------------------------
# 6. 관문 4
# --------------------------------------------------

def gate4_node(state: RouteState) -> dict:
    """소각·매립 경로 생성."""

    routes = run_gate4(
        waste_case=state["waste_case"],
        preliminary_result=state["preliminary_result"],
    )

    existing = state.get("route_candidates", [])

    return {
        "route_candidates": existing + routes,
    }


# --------------------------------------------------
# 7. 공통 경로 검증
# --------------------------------------------------

def validation_node(state: RouteState) -> dict:
    """모든 실행 경로에 동일한 검증 적용."""

    validated = []
    rejected = []
    needs_info = []

    for route in state.get("route_candidates", []):

        technical = validate_technical(route, state["waste_case"])
        regulatory = validate_regulatory(route, state["waste_case"])
        conditions = validate_conditions(route, state["waste_case"])

        results = [technical, regulatory, conditions]

        if any(r["status"] == "rejected" for r in results):
            rejected.append({
                **route,
                "validation_results": results,
            })
            continue

        if any(r["status"] == "needs_info" for r in results):
            needs_info.append({
                **route,
                "validation_results": results,
            })
            continue

        validated.append({
            **route,
            "validation_results": results,
        })

    return {
        "validated_routes": validated,
        "rejected_routes": rejected,
        "needs_info": needs_info,
    }


# --------------------------------------------------
# 8. 검증 결과에 따른 분기
# --------------------------------------------------

def route_after_validation(
    state: RouteState,
) -> Literal["matching", "waiting", "empty"]:
    """검증을 통과한 경로가 있는지 확인."""

    if state.get("validated_routes"):
        return "matching"

    if state.get("needs_info"):
        return "waiting"

    return "empty"


# --------------------------------------------------
# 9. 업체 매칭
# --------------------------------------------------

def matching_node(state: RouteState) -> dict:
    """처리업체·운반업체·처리 용량 연결."""

    matched_routes = []

    for route in state["validated_routes"]:

        partners = match_partners(route)

        transport = match_transport(
            route=route,
            waste_case=state["waste_case"],
        )

        capacity = validate_capacity(
            route=route,
            partners=partners,
            waste_case=state["waste_case"],
        )

        matched_routes.append({
            **route,
            "partners": partners,
            "transport": transport,
            "capacity": capacity,
        })

    return {
        "matched_routes": matched_routes,
    }


# --------------------------------------------------
# 10. 탄소 계산
# --------------------------------------------------

def carbon_node(state: RouteState) -> dict:
    """운송·공정·처분 탄소 배출량 계산."""

    results = []

    for route in state["matched_routes"]:
        result = calculate_carbon(
            route=route,
            waste_case=state["waste_case"],
        )

        results.append({
            **route,
            "carbon": result,
            "cost_total": None,
        })

    return {
        "carbon_results": results,
    }


# --------------------------------------------------
# 11. 경로 비교
# --------------------------------------------------

def comparison_node(state: RouteState) -> dict:
    """최종 경로 비교 및 우선순위 산정."""

    compared = compare_routes(
        routes=state["carbon_results"],
        waste_case=state["waste_case"],
    )

    return {
        "compared_routes": compared,
    }


# --------------------------------------------------
# 12. 최종 결과 생성
# --------------------------------------------------

def proposal_node(state: RouteState) -> dict:
    """Part 3 전달용 RouteProposal 생성."""

    proposal = {
        "waste_case_id": state["waste_case"]["case_id"],
        "preliminary_result": state["preliminary_result"],
        "routes": state.get("compared_routes", []),
        "rejected_routes": state.get("rejected_routes", []),
        "needs_info": state.get("needs_info", []),
        "gate3_candidates": state.get("gate3_candidates", []),
        "cost_total": None,
    }

    return {
        "route_proposal": proposal,
        "pipeline_status": (
            "completed"
            if proposal["routes"]
            else "no_valid_route"
        ),
    }


# --------------------------------------------------
# 13. 추가 정보 대기
# --------------------------------------------------

def waiting_node(state: RouteState) -> dict:
    """추가 정보가 필요한 경우 경로 확정 보류."""

    return {
        "pipeline_status": "needs_info",
    }


def empty_node(state: RouteState) -> dict:
    """생성된 유효 경로가 없는 경우."""

    return {
        "pipeline_status": "no_valid_route",
    }


# --------------------------------------------------
# 14. LangGraph 구성
# --------------------------------------------------

def build_route_graph():
    graph = StateGraph(RouteState)

    # 노드 등록
    graph.add_node("preliminary", preliminary_node)
    graph.add_node("gate1", gate1_node)
    graph.add_node("gate2", gate2_node)
    graph.add_node("gate3", gate3_node)
    graph.add_node("gate4", gate4_node)

    graph.add_node("validation", validation_node)
    graph.add_node("matching", matching_node)
    graph.add_node("carbon", carbon_node)
    graph.add_node("comparison", comparison_node)
    graph.add_node("proposal", proposal_node)

    graph.add_node("waiting", waiting_node)
    graph.add_node("empty", empty_node)

    # 시작
    graph.add_edge(START, "preliminary")

    # 기본 검토 결과에 따른 관문 분기
    graph.add_conditional_edges(
        "preliminary",
        route_after_preliminary,
        {
            "gate4": "gate4",
            "gate1": "gate1",
            "gate2": "gate2",
        },
    )

    # 일반폐기물: 관문 1 → 2 → 3 → 4
    graph.add_edge("gate1", "gate2")
    graph.add_edge("gate2", "gate3")
    graph.add_edge("gate3", "gate4")

    # 지정폐기물: 관문 2 → 4
    # 금지·제한 대상: 관문 4만 실행
    # 세 경로 모두 관문 4에서 공통 검증으로 연결
    graph.add_edge("gate4", "validation")

    # 검증 결과에 따른 분기
    graph.add_conditional_edges(
        "validation",
        route_after_validation,
        {
            "matching": "matching",
            "waiting": "waiting",
            "empty": "empty",
        },
    )

    # 공통 처리 흐름
    graph.add_edge("matching", "carbon")
    graph.add_edge("carbon", "comparison")
    graph.add_edge("comparison", "proposal")

    # 종료
    graph.add_edge("proposal", END)
    graph.add_edge("waiting", END)
    graph.add_edge("empty", END)

    return graph.compile()
