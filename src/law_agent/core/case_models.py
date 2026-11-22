# route_agent/src/preliminary/models.py 에서 복사 (2026.11.17). 바꾼 점: 없음 (WasteCase 등 자료형)
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class GateAction(str, Enum):
    RUN = "RUN"
    SKIP = "SKIP"
    NEEDS_INFO = "NEEDS_INFO"
    REVIEW = "REVIEW"


@dataclass(slots=True)
class WasteComponent:
    name: str
    waste_code: str | None = None
    share_pct: float | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    material: str | None = None
    separable: bool | None = None
    status: str | None = None          # confirmed / estimated (파트 1 값)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WasteComponent":
        material = value.get("material")
        name = str(value.get("name") or value.get("component_name") or material or "").strip()
        code = value.get("waste_code") or value.get("분류번호")
        share = value.get("share_pct", value.get("share", value.get("비율")))
        return cls(
            name=name,
            waste_code=str(code).strip() if code not in (None, "") else None,
            share_pct=float(share) if share not in (None, "") else None,
            properties=dict(value.get("properties") or {}),
            material=str(material).strip() if material not in (None, "") else None,
            separable=value.get("separable"),
            status=value.get("status"),
        )


@dataclass(slots=True)
class WasteCase:
    waste_case_id: str
    waste_code: str | None = None
    waste_name: str | None = None
    generation_process: str | None = None
    is_mixed: bool | None = None
    separable: bool | None = None
    components: list[WasteComponent] = field(default_factory=list)
    hazardous_suspect: bool | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    quantity: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    # ---- 파트 1 계약(1.0 + 협의 필드) ----
    schema_version: str | None = None
    case_version: int | None = None
    synthetic: bool | None = None
    reported_code: str | None = None             # classification.reported_code
    designated_waste_status: str | None = None   # 파트 1 판단 (대조용으로만 사용)
    region: dict[str, Any] = field(default_factory=dict)   # {sido, sigungu, address}
    item_specific: dict[str, Any] = field(default_factory=dict)
    foreign_matter: dict[str, Any] = field(default_factory=dict)
    mixed_with_other: bool | None = None
    contamination: dict[str, Any] = field(default_factory=dict)
    material_form: str | None = None
    existing_declaration: dict[str, Any] = field(default_factory=dict)
    combustible: bool | None = None
    storage_state: str | None = None
    waste_code_candidates: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)
    # 금지·제한 판정표(route_agent) 공통 규칙(PR-D1c, PR-D6b)의 필요한_입력 키
    banned_restricted_substance: bool | None = None
    natural_radioactive: bool | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WasteCase":
        if not isinstance(value, Mapping):
            raise TypeError("WasteCase 입력은 mapping이어야 합니다.")

        raw_components = value.get("components") or []
        components = [
            item if isinstance(item, WasteComponent) else WasteComponent.from_mapping(item)
            for item in raw_components
        ]
        waste_code = value.get("waste_code") or value.get("분류번호")
        hazardous_suspect = value.get("hazardous_suspect")
        if hazardous_suspect is None:
            hazardous_suspect = value.get("is_hazardous")

        return cls(
            waste_case_id=str(value.get("waste_case_id") or value.get("case_id") or "UNKNOWN"),
            waste_code=str(waste_code).strip() if waste_code not in (None, "") else None,
            waste_name=value.get("waste_name") or value.get("명칭"),
            generation_process=value.get("generation_process") or value.get("발생공정"),
            is_mixed=value.get("is_mixed"),
            separable=value.get("separable"),
            components=components,
            hazardous_suspect=hazardous_suspect,
            analysis=dict(value.get("analysis") or {}),
            properties=dict(value.get("properties") or {}),
            quantity=dict(value.get("quantity") or {}),
            location=dict(value.get("location") or {}),
            raw=dict(value),
            schema_version=value.get("schema_version"),
            case_version=value.get("case_version"),
            synthetic=value.get("synthetic"),
            reported_code=value.get("reported_code"),
            designated_waste_status=value.get("designated_waste_status"),
            region=dict(value.get("region") or {}),
            item_specific=dict(value.get("item_specific") or {}),
            foreign_matter=dict(value.get("foreign_matter") or {}),
            mixed_with_other=value.get("mixed_with_other"),
            contamination=dict(value.get("contamination") or {}),
            material_form=value.get("material_form"),
            existing_declaration=dict(value.get("existing_declaration") or {}),
            combustible=value.get("combustible"),
            storage_state=value.get("storage_state"),
            waste_code_candidates=list(value.get("waste_code_candidates") or []),
            missing=list(value.get("missing") or []),
            not_applicable=list(value.get("not_applicable") or []),
            banned_restricted_substance=value.get("banned_restricted_substance"),
            natural_radioactive=value.get("natural_radioactive"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WasteClassificationResult:
    status: str
    input_waste_code: str | None
    normalized_waste_code: str | None
    waste_name: str | None
    matched: bool
    warnings: list[str] = field(default_factory=list)
    source: str | None = None
    classification_level: str | None = None
    parent_code: str | None = None
    is_terminal: bool | None = None
    official_name: str | None = None
    waste_category_code: str | None = None
    terminal_codes: list[str] = field(default_factory=list)
    hazardous_designation: str | None = None


@dataclass(slots=True)
class RestrictionResult:
    status: str
    recycling_blocked: bool = False
    allowed_rcodes: list[str] = field(default_factory=list)
    restricted_rcodes: list[str] = field(default_factory=list)
    matched_rules: list[dict[str, Any]] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    review_required: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HazardousResult:
    status: str
    matched: bool
    waste_code: str | None
    rule: dict[str, Any] | None = None
    required_inputs: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MixtureResult:
    status: str
    is_mixed: bool
    component_count: int
    share_total_pct: float | None = None
    separable: bool | None = None
    component_codes_missing: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GatePlanItem:
    action: GateAction
    reason: str
    conditions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PreliminaryResult:
    waste_case_id: str
    classification: WasteClassificationResult
    restriction: RestrictionResult
    hazardous: HazardousResult
    mixture: MixtureResult
    gate_plan: dict[str, GatePlanItem]
    status: str
    required_information: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 관문이 같은 판단을 다시 하지 않도록 넘기는 값
    case_version: int | None = None
    schema_version: str | None = None
    waste_status: str | None = None        # general / designated (분류 미확정이면 None)
    hazardous_basis: str | None = None     # 분류번호 / 의심 / None
    component_results: list[dict[str, Any]] = field(default_factory=list)  # 성분별 분류번호가 있을 때만
    questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for gate in result["gate_plan"].values():
            gate["action"] = gate["action"].value
        return result
