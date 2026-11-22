"""관문 1~4가 공통으로 쓰는 결과 자료형.

관문 입력은 사전검증과 같은 WasteCase(파트 1 case 1건)와 PreliminaryResult다.
관문 함수 모양: run(case, pre, tables) -> list[RouteDraft]
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum


class Status(IntEnum):
    """경로의 법적 상태(checks.regulatory). 숫자가 작을수록 나쁨."""
    REJECTED = 0
    NEEDS_INFO = 1
    CONDITIONAL = 2
    CONFIRMED = 3

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass
class Step:
    order: int
    process: str
    role: str                                   # transporter / preprocessor / recycler / treatment / end_user
    treatment_codes: list[str] = field(default_factory=list)   # 처리방법 코드 (별지 제5호): "2003"
    r_codes: list[str] = field(default_factory=list)           # 재활용 유형: "R-3-3"
    waste_codes: list[str] = field(default_factory=list)       # 이 단계에 들어가는 분류번호


@dataclass
class RouteDraft:
    gate: int
    tier: str | None                            # reuse / material_recycle / energy_recovery / disposal / None(중간 가공)
    route_type: str                             # direct_use / single_material_recycle / separate_then_recycle / mixed_recycle / alternative_treatment
    title: str
    waste_codes: list[str]
    waste_status: str                           # general / designated
    steps: list[Step]
    regulatory: Status
    reasons: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)      # {"field", "text", "to": "part1" | "partner"}
    conditions: list[dict] = field(default_factory=list)     # {"text", "basis", "basis_status"}
    required_tests: list[dict] = field(default_factory=list)
    r_code_detail: list[dict] = field(default_factory=list)  # {"r_code", "pre_analysis", "basis"}
    evidence_ids: list[str] = field(default_factory=list)
    basis_status: str = "확정"                  # 확정 / 검토 대기 (초안 판정표를 쓴 경우)

    def cap(self, limit: Status, reason: str) -> None:
        """상태를 limit 이하로 낮춘다 (올리지 않음). 이유는 기록."""
        if self.regulatory > limit:
            self.regulatory = limit
        if reason not in self.reasons:
            self.reasons.append(reason)

    def ask(self, field_name: str, text: str, to: str = "part1") -> None:
        if any(q["field"] == field_name for q in self.questions):
            return                                  # 같은 칸은 한 번만 묻는다
        self.questions.append({"field": field_name, "text": text, "to": to})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["regulatory"] = self.regulatory.label
        return d
