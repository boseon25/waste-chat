
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .hazardous_checker import HazardousChecker
from .input_adapter import InputAdapter
from .mixture_checker import MixtureChecker
from .models import (
    GateAction,
    GatePlanItem,
    HazardousResult,
    PreliminaryResult,
    RestrictionResult,
    WasteCase,
    WasteClassificationResult,
)
from .restriction_checker import RestrictionChecker
from .waste_classifier import WasteClassifier, normalize_waste_code


# 지정폐기물 기준으로 판정하는 상태 (관문 1·3 SKIP)
DESIGNATED_STATUSES = {"HAZARDOUS", "SUSPECTED"}

# 여러 분류번호(혼합물 성분)의 결과를 합칠 때 우선순위: 앞쪽이 우선
CLASSIFICATION_PRIORITY = ["REVIEW", "MISMATCH", "NOT_FOUND", "NEEDS_INFO", "PARENT_CLASS", "MATCHED"]
HAZARDOUS_PRIORITY = ["REVIEW", "NEEDS_INFO", "HAZARDOUS", "SUSPECTED", "NON_HAZARDOUS", "NOT_EVALUATED"]


class PreliminaryIntegrator:
    """
    관문에 들어가기 전에 어느 관문을 돌릴지만 정합니다.

    관문 선택에 쓰는 검사는 3개입니다.
    1. 분류번호가 말단 코드로 확정됐는지 (아니면 관문 전부 보류)
    2. 지정폐기물인지 (분류번호 또는 지정 의심) → 관문 1·3 SKIP
    3. 재활용 금지(BLOCK_TO_GATE4)인지 → 관문 1·2·3 SKIP

    혼합물은 2·3번을 성분 분류번호마다 돌려서 하나라도 걸리면 전체에 적용합니다.
    제한(RESTRICT)·정보 필요(NEEDS_INFO)·검토(NEEDS_REVIEW) 규칙은 기록만 하고
    관문 2에서 처리합니다.
    """

    REVIEW_CLASSIFICATION_STATUSES = {"MISMATCH", "REVIEW"}
    INFO_CLASSIFICATION_STATUSES = {"NEEDS_INFO", "PARENT_CLASS", "NOT_FOUND"}

    def __init__(
        self,
        data_dir: str | Path,
        *,
        waste_codes_file: str = "waste_codes_4.csv",
        crosswalk_file: str | None = "waste_code_crosswalk.csv",
        restriction_file: str = "prohibited_restricted.csv",
        hazardous_file: str = "hazardous_codes.csv",
    ) -> None:
        base = Path(data_dir)

        crosswalk_path = base / crosswalk_file if crosswalk_file else None

        self.input_adapter = InputAdapter()
        self.classifier = WasteClassifier(
            waste_codes_path=base / waste_codes_file,
            crosswalk_path=(
                crosswalk_path
                if crosswalk_path and crosswalk_path.exists()
                else None
            ),
        )
        self.restriction_checker = RestrictionChecker(base / restriction_file)
        self.hazardous_checker = HazardousChecker(base / hazardous_file)
        self.mixture_checker = MixtureChecker()

    @staticmethod
    def _not_evaluated_restriction(reason: str) -> RestrictionResult:
        return RestrictionResult(status="NOT_EVALUATED", warnings=[reason])

    @staticmethod
    def _not_evaluated_hazardous(reason: str) -> HazardousResult:
        return HazardousResult(
            status="NOT_EVALUATED",
            matched=False,
            waste_code=None,
            warnings=[reason],
        )

    # ------------------------------------------------------------------
    # 관문 실행 계획
    # ------------------------------------------------------------------
    @classmethod
    def _build_gate_plan(
        cls,
        hazardous_status: str,
        restriction_blocked: bool,
        restriction_review: bool,
        mixture_status: str,
        restriction_status: str,
        classification_status: str,
    ) -> dict[str, GatePlanItem]:

        plan: dict[str, GatePlanItem] = {}
        gates = ("gate1", "gate2", "gate3", "gate4")

        # 1. 분류 검토가 필요한 경우
        if classification_status in cls.REVIEW_CLASSIFICATION_STATUSES:
            reason = "폐기물 분류번호와 명칭의 불일치 또는 데이터 검토가 필요합니다."
            return {g: GatePlanItem(GateAction.REVIEW, reason) for g in gates}

        # 2. 말단 분류번호가 확인되지 않은 경우
        if classification_status in cls.INFO_CLASSIFICATION_STATUSES:
            reason = "말단 분류번호가 확인되지 않아 Gate 판정을 보류합니다."
            return {g: GatePlanItem(GateAction.NEEDS_INFO, reason) for g in gates}

        # 3. 지정폐기물 판정 자체가 불가능한 경우 (분류표·지정 목록 상충 등)
        if hazardous_status in {"NEEDS_INFO", "REVIEW"}:
            action = GateAction.NEEDS_INFO if hazardous_status == "NEEDS_INFO" else GateAction.REVIEW
            reason = "지정폐기물 여부에 추가 확인이 필요합니다."
            plan = {g: GatePlanItem(action, reason) for g in ("gate1", "gate2", "gate3")}
            plan["gate4"] = GatePlanItem(GateAction.RUN, "처분 방법 및 세부 기준을 확인합니다.")
            return plan

        designated = hazardous_status in DESIGNATED_STATUSES
        suspected = hazardous_status == "SUSPECTED"
        blocked_reason = "재활용 금지 규칙(BLOCK_TO_GATE4)이 적용되었습니다."

        # Gate 1: 지정 순환자원 (지정 품목인지는 관문 1이 판단)
        if restriction_blocked:
            plan["gate1"] = GatePlanItem(GateAction.SKIP, blocked_reason)
        elif designated:
            plan["gate1"] = GatePlanItem(
                GateAction.SKIP,
                "지정폐기물 의심으로 지정폐기물 기준을 적용합니다." if suspected
                else "지정폐기물로 확인되어 Gate 1 대상에서 제외합니다.",
            )
        else:
            plan["gate1"] = GatePlanItem(
                GateAction.RUN,
                "지정 순환자원 품목·조건을 확인합니다.",
            )

        # Gate 2: 재활용 위탁
        if restriction_blocked:
            plan["gate2"] = GatePlanItem(GateAction.SKIP, blocked_reason)
        else:
            conditions: list[str] = []
            if designated:
                conditions.append("지정폐기물 기준으로 판정합니다 (지정폐기물 재활용 허가 업체만).")
            if suspected:
                conditions.append("지정폐기물 의심: 성분 분석 결과에 따라 다시 판정합니다.")
            if restriction_status == "RESTRICTED":
                conditions.append("재활용 제한 규칙의 허용 R코드 안에서만 경로를 만듭니다.")
            if restriction_status == "NEEDS_INFO":
                conditions.append("재활용 제한 규칙에 필요한 입력이 부족합니다 (관문 2에서 needs_info).")
            if restriction_review:
                conditions.append("재활용 제한 규칙의 검토가 필요합니다 (관문 2에서 needs_info).")
            if mixture_status == "MIXED":
                conditions.append("혼합물: 분리 가능 여부에 따라 경로 종류를 정합니다.")
            plan["gate2"] = GatePlanItem(
                GateAction.RUN,
                "재활용 유형별 허용 조건을 확인합니다.",
                conditions,
            )

        # Gate 3: 순환자원 개별 인정 (장기 후보)
        if restriction_blocked:
            plan["gate3"] = GatePlanItem(GateAction.SKIP, blocked_reason)
        elif designated:
            plan["gate3"] = GatePlanItem(
                GateAction.SKIP,
                "지정폐기물(의심 포함)은 Gate 3 후보에서 제외합니다.",
            )
        else:
            plan["gate3"] = GatePlanItem(
                GateAction.RUN,
                "개별 순환자원 인정 신청 후보 요건을 확인합니다.",
            )

        # Gate 4: 소각·매립
        plan["gate4"] = GatePlanItem(
            GateAction.RUN,
            "처분 방법 및 세부 기준을 확인합니다.",
            ["지정폐기물 처분 기준을 적용합니다."] if designated else [],
        )
        return plan

    # ------------------------------------------------------------------
    # 성분 분류번호
    # ------------------------------------------------------------------
    @staticmethod
    def _component_codes(case: WasteCase) -> list[str]:
        """성분에 따로 적힌 분류번호 (폐기물 전체 분류번호와 다른 것만)."""
        main = normalize_waste_code(case.waste_code)
        codes: list[str] = []
        for comp in case.components:
            code = normalize_waste_code(comp.waste_code)
            if comp.waste_code and code != main and comp.waste_code not in codes:
                codes.append(comp.waste_code)
        return codes

    @staticmethod
    def _worst(values: list[str], priority: list[str]) -> str:
        return min(values, key=lambda v: priority.index(v) if v in priority else len(priority))

    @staticmethod
    def _merge_restrictions(results: list[RestrictionResult]) -> RestrictionResult:
        if len(results) == 1:
            return results[0]
        allowed_sets = [set(r.allowed_rcodes) for r in results if r.allowed_rcodes]
        allowed = set.intersection(*allowed_sets) if allowed_sets else set()
        statuses = [r.status for r in results]
        order = ["BLOCKED", "NEEDS_REVIEW", "NEEDS_INFO", "RESTRICTED", "CHECKED", "NO_MATCH", "NOT_EVALUATED"]
        return RestrictionResult(
            status=min(statuses, key=lambda s: order.index(s) if s in order else len(order)),
            recycling_blocked=any(r.recycling_blocked for r in results),
            allowed_rcodes=sorted(allowed),
            restricted_rcodes=sorted({c for r in results for c in r.restricted_rcodes}),
            matched_rules=[m for r in results for m in r.matched_rules],
            required_inputs=sorted({x for r in results for x in r.required_inputs}),
            questions=sorted({x for r in results for x in r.questions}),
            review_required=any(r.review_required for r in results),
            warnings=[w for r in results for w in r.warnings],
        )

    # ------------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------------
    def run(
        self,
        payload: WasteCase | Mapping[str, Any] | str | bytes,
    ) -> PreliminaryResult:
        """입력 변환부터 관문 실행 계획까지 실행합니다."""

        # 1. 입력 변환
        case = payload if isinstance(payload, WasteCase) else self.input_adapter.adapt(payload)

        # 폐기물 전체 분류번호가 없고 성분에만 있으면 첫 성분 분류번호를 대표로 사용
        if not case.waste_code:
            first = next((c.waste_code for c in case.components if c.waste_code), None)
            if first:
                case.waste_code = first

        # 2. 분류 (대표 분류번호 + 성분별 분류번호)
        classification = self.classifier.classify(case.waste_code, case.waste_name)
        comp_codes = self._component_codes(case)
        comp_classes: list[WasteClassificationResult] = [
            self.classifier.classify(code) for code in comp_codes
        ]
        combined_cls_status = self._worst(
            [classification.status] + [c.status for c in comp_classes], CLASSIFICATION_PRIORITY
        )

        # 3. 지정폐기물·금지 규칙 (분류가 모두 확정된 경우에만)
        component_results: list[dict[str, Any]] = []
        if combined_cls_status == "MATCHED":
            hazardous = self.hazardous_checker.evaluate(case, classification)
            restrictions = [self.restriction_checker.evaluate(case)]
            haz_statuses = [hazardous.status]

            for code, cls_result in zip(comp_codes, comp_classes):
                comp_haz = self.hazardous_checker.evaluate(case, cls_result)
                comp_res = self.restriction_checker.evaluate(case, cls_result.normalized_waste_code)
                restrictions.append(comp_res)
                haz_statuses.append(comp_haz.status)
                component_results.append({
                    "waste_code": code,
                    "classification_status": cls_result.status,
                    "hazardous_status": comp_haz.status,
                    "recycling_blocked": comp_res.recycling_blocked,
                    "matched_rules": [m["rule_id"] for m in comp_res.matched_rules],
                })

            worst_haz = self._worst(haz_statuses, HAZARDOUS_PRIORITY)
            if worst_haz != hazardous.status:
                hazardous.status = worst_haz
                hazardous.warnings.append(f"혼합물 성분 중 {worst_haz} 판정이 있어 전체에 적용합니다.")
            restriction = self._merge_restrictions(restrictions)
        else:
            reason = "분류번호가 공식 말단 분류로 확정되지 않아 제한 규칙 검사를 실행하지 않았습니다."
            restriction = self._not_evaluated_restriction(reason)
            hazardous = self._not_evaluated_hazardous(
                "분류번호가 공식 말단 분류로 확정되지 않아 지정폐기물 검사를 실행하지 않았습니다."
            )
            for code, cls_result in zip(comp_codes, comp_classes):
                component_results.append({"waste_code": code, "classification_status": cls_result.status})

        # 4. 혼합 여부 (사실 기록만)
        mixture = self.mixture_checker.evaluate(case)

        # 5. 관문 실행 계획
        gate_plan = self._build_gate_plan(
            hazardous_status=hazardous.status,
            restriction_blocked=restriction.recycling_blocked,
            restriction_review=restriction.review_required,
            mixture_status=mixture.status,
            restriction_status=restriction.status,
            classification_status=combined_cls_status,
        )

        # 6. 추가 입력·질문·경고
        required_information = sorted(set(
            classification_required_information(combined_cls_status)
            + restriction.required_inputs
            + hazardous.required_inputs
        ))
        questions = list(dict.fromkeys(hazardous.questions + restriction.questions + mixture.questions))
        warnings = classification.warnings + restriction.warnings + hazardous.warnings + mixture.warnings
        if case.reported_code and normalize_waste_code(case.reported_code) != normalize_waste_code(case.waste_code):
            warnings.append(
                f"신고 분류번호({case.reported_code})와 선택 분류번호({case.waste_code})가 다릅니다."
            )

        # 7. 전체 상태 (관문을 돌릴 수 있는지만 판단)
        if combined_cls_status in self.REVIEW_CLASSIFICATION_STATUSES:
            overall_status = "NEEDS_REVIEW"
        elif combined_cls_status in self.INFO_CLASSIFICATION_STATUSES:
            overall_status = "NEEDS_INFO"
        elif hazardous.status == "NEEDS_INFO":
            overall_status = "NEEDS_INFO"
        elif hazardous.status == "REVIEW":
            overall_status = "NEEDS_REVIEW"
        else:
            overall_status = "READY"

        waste_status = (
            "designated" if hazardous.status in DESIGNATED_STATUSES
            else "general" if hazardous.status == "NON_HAZARDOUS"
            else None
        )
        hazardous_basis = (
            "분류번호" if hazardous.status == "HAZARDOUS"
            else "의심" if hazardous.status == "SUSPECTED"
            else None
        )

        return PreliminaryResult(
            waste_case_id=case.waste_case_id,
            classification=classification,
            restriction=restriction,
            hazardous=hazardous,
            mixture=mixture,
            gate_plan=gate_plan,
            status=overall_status,
            required_information=required_information,
            warnings=warnings,
            case_version=case.case_version,
            schema_version=case.schema_version,
            waste_status=waste_status,
            hazardous_basis=hazardous_basis,
            component_results=component_results,
            questions=questions,
        )


def classification_required_information(status: str) -> list[str]:
    """분류 상태에 따른 추가 입력 필드를 반환합니다."""
    if status in {"NEEDS_INFO", "PARENT_CLASS", "NOT_FOUND"}:
        return ["waste_code"]
    if status in {"MISMATCH", "REVIEW"}:
        return ["waste_code", "waste_name"]
    return []
