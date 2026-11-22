from __future__ import annotations

from .models import MixtureResult, WasteCase


class MixtureChecker:
    """
    혼합 여부와 구성 정보를 확인합니다. 관문 선택에는 쓰지 않고 사실만 기록합니다.

    - 성분이 2개 이상이면 혼합(MIXED). 성분 1개 또는 없음이면 SINGLE
    - 파트 1은 분류번호를 classification.selected_code(폐기물 전체)에 주고
      components에는 재질만 줄 수 있습니다. 이때 성분 분류번호가 없어도 정상입니다.
    - 성분 분류번호는 일부만 있을 때만 경고합니다 (전부 없거나 전부 있으면 정상)
    - 분리 가능 여부를 모르면 질문만 남깁니다 (관문 2가 경로 두 개를 needs_info로 만듦)
    """

    def evaluate(self, case: WasteCase) -> MixtureResult:
        comps = case.components or []
        count = len(comps)
        is_mixed = bool(case.is_mixed) if case.is_mixed is not None else count > 1

        if not is_mixed:
            return MixtureResult(
                status="SINGLE",
                is_mixed=False,
                component_count=count,
                separable=case.separable,
            )

        warnings: list[str] = []
        questions: list[str] = []

        shares = [c.share_pct for c in comps if c.share_pct is not None]
        share_total = round(sum(shares), 3) if shares else None
        if shares and len(shares) == count and abs(share_total - 100) > 1:
            warnings.append(f"구성비 합계가 {share_total}%입니다 (100%가 아님).")

        missing_idx = [i for i, c in enumerate(comps) if not c.waste_code]
        if 0 < len(missing_idx) < count:
            warnings.append("일부 성분에만 분류번호가 있습니다. 나머지 성분의 분류번호를 확인해야 합니다.")
        elif len(missing_idx) == count and not case.waste_code:
            questions.append("폐기물 분류번호를 알려 주세요.")

        if case.separable is None:
            questions.append("재질별로 나눌 수 있나요? (나눠서 가공 / 섞인 채 가공 판단)")

        return MixtureResult(
            status="MIXED",
            is_mixed=True,
            component_count=count,
            share_total_pct=share_total,
            separable=case.separable,
            component_codes_missing=missing_idx if 0 < len(missing_idx) < count else [],
            warnings=warnings,
            questions=questions,
        )
