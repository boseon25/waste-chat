from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import RestrictionResult, WasteCase
from .waste_classifier import normalize_waste_code


class RestrictionChecker:
    """
    prohibited_restricted.csv 규칙을 적용합니다.

    주의:
    - BLOCK_TO_GATE4는 재활용 경로 차단 신호로 보존합니다.
    - NEEDS_REVIEW는 자동으로 허용/금지로 바꾸지 않습니다.
    - PR-R0 같은 안전망 규칙은 구체 규칙과 함께 결과에 남깁니다.
    - 법적 우선순위를 코드에서 임의로 단정하지 않습니다.
    """

    REQUIRED_COLUMNS = {
        "rule_id", "근거", "대상", "action", "허용_R코드",
        "필요한_입력", "모를_때", "분류번호", "명칭",
    }

    def __init__(self, rules_path: str | Path) -> None:
        self.rules_path = Path(rules_path)
        self.rules = self._load_rules()

    def _load_rules(self) -> list[dict[str, str]]:
        if not self.rules_path.exists():
            raise FileNotFoundError(f"제한 규칙 파일이 없습니다: {self.rules_path}")
        with self.rules_path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            if not reader.fieldnames:
                raise ValueError("prohibited_restricted.csv 헤더가 없습니다.")
            missing = self.REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                raise ValueError(f"제한 규칙 CSV 필수 컬럼 누락: {sorted(missing)}")
            rows = [dict(row) for row in reader]

        for row in rows:
            row["분류번호"] = normalize_waste_code(row.get("분류번호"))
            row["action"] = (row.get("action") or "").strip().upper()
        return rows

    @staticmethod
    def _lookup_path(data: dict[str, Any], dotted_path: str) -> Any:
        current: Any = data
        for part in dotted_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _input_value(self, case: WasteCase, key: str) -> Any:
        data = case.to_dict()
        if key in ("", None):
            return None
        return self._lookup_path(data, key.strip())

    def evaluate(self, case: WasteCase) -> RestrictionResult:
        code = normalize_waste_code(case.waste_code)
        matched: list[dict[str, Any]] = []
        required_inputs: list[str] = []
        questions: list[str] = []
        warnings: list[str] = []
        recycling_blocked = False
        review_required = False
        allowed_rcodes: set[str] = set()
        restricted_rcodes: set[str] = set()

        for row in self.rules:
            rule_code = row.get("분류번호")
            # Code-specific rows match exactly. Blank-code rows are conditional
            # rules and are only activated when their required input is truthy.
            if rule_code and rule_code != code:
                continue

            required_key = (row.get("필요한_입력") or "").strip()
            if not rule_code and required_key:
                value = self._input_value(case, required_key)
                if value in (None, "", False, [], {}):
                    continue
            elif not rule_code and not required_key:
                # Generic rules without a code or trigger must not apply globally.
                continue

            action = row.get("action", "")
            rule_id = row.get("rule_id", "")
            rcode_text = (row.get("허용_R코드") or "").strip()
            rcodes = {item.strip() for item in rcode_text.replace(";", ",").split(",") if item.strip()}

            matched.append({
                "rule_id": rule_id,
                "action": action,
                "target": row.get("대상"),
                "legal_basis": row.get("근거"),
                "waste_code": rule_code,
                "allowed_rcodes": sorted(rcodes),
                "note": row.get("note"),
                "citation_status": row.get("인용_검증"),
                "source_chunk": row.get("근거_청크"),
            })

            if action == "BLOCK_TO_GATE4":
                recycling_blocked = True
            elif action == "RESTRICT":
                restricted_rcodes.update(rcodes)
            elif action == "NEEDS_INFO":
                if required_key:
                    required_inputs.append(required_key)
                question = (row.get("모를_때") or "").strip()
                if question:
                    questions.append(question)
            elif action == "NEEDS_REVIEW":
                review_required = True
                warnings.append(f"{rule_id}: 법적 해석 또는 추가 검토가 필요합니다.")
            elif action == "NO_ROUTE":
                # This is a route-level constraint; Gate 2 must compare the
                # candidate R-code with the official allowed R-code table.
                warnings.append(f"{rule_id}: 해당 유형의 R코드 허용 여부를 Gate 2에서 확인해야 합니다.")
            else:
                warnings.append(f"{rule_id}: 알 수 없는 action '{action}'입니다.")

        # Conditional input rules may be activated by Part 1 flags even when the
        # field is not nested under analysis. This keeps the CSV key authoritative.
        if any(item.get("rule_id") == "PR-D1c" for item in matched):
            warnings.append("금지·제한물질 관련 입력이 확인되어 해당 물질의 적용 범위를 별도 검토해야 합니다.")

        if recycling_blocked:
            status = "BLOCKED"
        elif review_required:
            status = "NEEDS_REVIEW"
        elif questions:
            status = "NEEDS_INFO"
        elif restricted_rcodes:
            status = "RESTRICTED"
        elif matched:
            status = "CHECKED"
        else:
            status = "NO_MATCH"

        return RestrictionResult(
            status=status,
            recycling_blocked=recycling_blocked,
            allowed_rcodes=sorted(allowed_rcodes),
            restricted_rcodes=sorted(restricted_rcodes),
            matched_rules=matched,
            required_inputs=sorted(set(required_inputs)),
            questions=sorted(set(questions)),
            review_required=review_required,
            warnings=warnings,
        )