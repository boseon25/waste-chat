from __future__ import annotations

import csv
from pathlib import Path

from .models import HazardousResult, WasteCase, WasteClassificationResult
from .waste_classifier import normalize_waste_code


class HazardousChecker:
    """공식 분류 결과와 hazardous_codes.csv의 지정폐기물 목록을 교차 확인합니다."""

    REQUIRED_COLUMNS = {"분류번호", "지정폐기물", "말단"}

    def __init__(self, rules_path: str | Path) -> None:
        self.rules_path = Path(rules_path)
        self.rules = self._load_rules()
        self._rules_by_code = {row["분류번호"]: row for row in self.rules}

    def _load_rules(self) -> list[dict[str, str]]:
        if not self.rules_path.exists():
            raise FileNotFoundError(f"지정폐기물 규칙 파일이 없습니다: {self.rules_path}")
        with self.rules_path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            if not reader.fieldnames:
                raise ValueError("hazardous_codes.csv 헤더가 없습니다.")
            missing = self.REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                raise ValueError(f"지정폐기물 CSV 필수 컬럼 누락: {sorted(missing)}")
            rows = [dict(row) for row in reader]

        indexed: dict[str, dict[str, str]] = {}
        for row in rows:
            code = normalize_waste_code(row.get("분류번호"))
            if code is None:
                raise ValueError(f"잘못된 지정폐기물 분류번호: {row.get('분류번호')!r}")
            if code in indexed:
                raise ValueError(f"지정폐기물 분류번호 중복: {code}")
            if (row.get("지정폐기물") or "").strip().upper() != "Y":
                raise ValueError(f"{code}: hazardous_codes.csv에는 지정폐기물(Y)만 있어야 합니다.")
            row["분류번호"] = code
            indexed[code] = row
        return list(indexed.values())

    def evaluate(
        self,
        case: WasteCase,
        classification: WasteClassificationResult | None = None,
    ) -> HazardousResult:
        code = (
            classification.normalized_waste_code
            if classification is not None
            else normalize_waste_code(case.waste_code)
        )
        if not code:
            return HazardousResult(
                status="NEEDS_INFO",
                matched=False,
                waste_code=None,
                required_inputs=["waste_code"],
                questions=["폐기물 분류번호를 확인해 주세요."],
            )

        if classification is not None and classification.status != "MATCHED":
            return HazardousResult(
                status="NOT_EVALUATED",
                matched=False,
                waste_code=code,
                warnings=["말단 분류가 확정되지 않아 지정폐기물 검사를 실행하지 않았습니다."],
            )

        row = self._rules_by_code.get(code)
        designation = (
            classification.hazardous_designation
            if classification is not None
            else None
        )
        if row is not None:
            if designation == "N":
                return HazardousResult(
                    status="REVIEW",
                    matched=True,
                    waste_code=code,
                    rule=row,
                    warnings=["공식 분류표는 비지정(N), 지정폐기물 목록은 지정(Y)으로 상충합니다."],
                )
            warnings = ["공식 지정폐기물 목록과 일치했습니다. 개별 법령의 예외·분석 조건은 별도 확인이 필요합니다."]
            if (case.designated_waste_status or "").lower() in {"non_designated", "general"}:
                warnings.append("파트 1은 비지정으로 보냈지만 분류번호가 지정폐기물이므로 지정폐기물로 판정합니다.")
            return HazardousResult(
                status="HAZARDOUS",
                matched=True,
                waste_code=code,
                rule=row,
                warnings=warnings,
            )

        if designation == "Y":
            return HazardousResult(
                status="REVIEW",
                matched=False,
                waste_code=code,
                warnings=["공식 분류표는 지정폐기물(Y)이지만 hazardous_codes.csv에 코드가 없습니다."],
            )

        # 분류번호는 일반인데 지정폐기물 의심이 있으면 지정폐기물 기준으로 판정 (관문 1·3 SKIP)
        part1_designated = (case.designated_waste_status or "").lower() == "designated"
        if case.hazardous_suspect is True or part1_designated:
            basis = "hazardous_suspect" if case.hazardous_suspect is True else "designated_waste_status"
            return HazardousResult(
                status="SUSPECTED",
                matched=False,
                waste_code=code,
                questions=["성분 분석으로 지정폐기물 해당 여부를 확인했나요?"],
                warnings=[
                    f"분류번호는 일반폐기물이지만 파트 1 {basis} 값으로 지정폐기물 기준을 적용합니다. "
                    "분석 결과 일반이면 case_version을 올려 다시 보내야 합니다."
                ],
            )

        if designation == "N":
            questions = []
            if case.hazardous_suspect is None:
                questions.append("지정폐기물 의심 여부(hazardous_suspect)를 알려 주세요.")
            return HazardousResult(
                status="NON_HAZARDOUS",
                matched=False,
                waste_code=code,
                questions=questions,
                warnings=["공식 분류표의 지정폐기물 값이 N이며 지정폐기물 목록과 일치하지 않습니다."],
            )

        return HazardousResult(
            status="REVIEW",
            matched=False,
            waste_code=code,
            warnings=["지정폐기물 여부를 확인할 공식 분류 속성이 전달되지 않았습니다."],
        )
