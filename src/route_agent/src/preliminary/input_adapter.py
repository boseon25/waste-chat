
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from .models import WasteCase


logger = logging.getLogger(__name__)


class InputAdapterError(ValueError):
    """Agent 1 입력 데이터 변환 오류."""


class InputAdapter:
    """
    Agent 1의 JSON 입력을 Preliminary의 WasteCase로 변환합니다.

    주요 기능:
    - JSON 문자열 및 Mapping 입력 지원
    - 필드명 별칭 처리
    - 선택적 필드 지원
    - 자료형 검증
    - 혼합 성분 데이터 검증
    - 원본 JSON 보존
    """

    FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "waste_case_id": (
            "waste_case_id",
            "case_id",
            "request_id",
        ),
        "waste_code": (
            "waste_code",
            "분류번호",
            "폐기물분류번호",
        ),
        "waste_name": (
            "waste_name",
            "명칭",
            "폐기물명",
        ),
        "generation_process": (
            "generation_process",
            "발생공정",
            "발생공정명",
        ),
        "is_mixed": (
            "is_mixed",
            "mixed",
            "혼합여부",
        ),
        "separable": (
            "separable",
            "is_separable",
            "분리가능여부",
        ),
        "components": (
            "components",
            "성분",
            "구성성분",
        ),
        "hazardous_suspect": (
            "hazardous_suspect",
            "is_hazardous",
            "hazardous",
            "지정폐기물의심여부",
        ),
        "analysis": (
            "analysis",
            "hazardous_analysis",
            "lab_analysis",
            "분석결과",
        ),
        "properties": (
            "properties",
            "물성정보",
            "특성",
        ),
        "quantity": (
            "quantity",
            "발생량",
        ),
        "location": (
            "location",
            "사업장위치",
            "배출사업장",
        ),
    }

    BOOLEAN_FIELDS = {
        "is_mixed",
        "separable",
        "hazardous_suspect",
        "mixed_with_other",
        "combustible",
        "banned_restricted_substance",
        "natural_radioactive",
    }

    # 별칭 없이 그대로 WasteCase로 넘기는 파트 1 계약 필드
    PASS_THROUGH_FIELDS = (
        "schema_version",
        "case_version",
        "synthetic",
        "reported_code",
        "designated_waste_status",
        "region",
        "item_specific",
        "foreign_matter",
        "mixed_with_other",
        "contamination",
        "material_form",
        "existing_declaration",
        "combustible",
        "storage_state",
        "waste_code_candidates",
        "missing",
        "not_applicable",
        "banned_restricted_substance",
        "natural_radioactive",
    )

    # 업체 표(partners_*)의 시도 표기로 통일
    SIDO_SHORT = {
        "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
        "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
        "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원",
        "충청북도": "충북", "충청남도": "충남",
        "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
        "경상북도": "경북", "경상남도": "경남",
        "제주도": "제주", "제주특별자치도": "제주",
    }

    MAPPING_FIELDS = {
        "analysis",
        "properties",
        "quantity",
        "location",
    }

    def adapt(
        self,
        payload: str | bytes | Mapping[str, Any],
    ) -> WasteCase:
        """
        Agent 1 입력을 WasteCase 객체로 변환합니다.

        Args:
            payload:
                JSON 문자열, bytes 또는 Mapping

        Returns:
            정규화된 WasteCase 객체

        Raises:
            InputAdapterError:
                JSON 파싱 또는 데이터 검증 실패
        """
        raw_data = self._parse_payload(payload)

        # 파트 1 계약 형식(classification.selected_code, region 등)을 내부 형식으로 변환
        converted = self._from_part1_contract(raw_data)

        normalized = self._normalize_fields(converted)
        for key in self.PASS_THROUGH_FIELDS:
            if key in converted:
                normalized[key] = converted[key]

        self._validate_fields(normalized)

        normalized["components"] = self._normalize_components(
            normalized.get("components")
        )

        normalized["waste_case_id"] = (
            str(normalized.get("waste_case_id") or "").strip()
            or str(uuid4())
        )

        try:
            waste_case = WasteCase.from_mapping(normalized)

            # 원본 Agent 1 JSON을 그대로 보존합니다.
            waste_case.raw = dict(raw_data)

            return waste_case

        except (TypeError, ValueError, KeyError) as exc:
            raise InputAdapterError(
                f"WasteCase 변환에 실패했습니다: {exc}"
            ) from exc

    @staticmethod
    def _parse_payload(
        payload: str | bytes | Mapping[str, Any],
    ) -> dict[str, Any]:
        """JSON 입력을 dictionary로 변환합니다."""

        if isinstance(payload, Mapping):
            return dict(payload)

        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InputAdapterError(
                    "입력 JSON의 UTF-8 디코딩에 실패했습니다."
                ) from exc

        if not isinstance(payload, str):
            raise InputAdapterError(
                "입력은 JSON 문자열, bytes 또는 Mapping이어야 합니다."
            )

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise InputAdapterError(
                f"JSON 형식이 올바르지 않습니다: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise InputAdapterError(
                "JSON 최상위 구조는 객체(dictionary)여야 합니다."
            )

        return parsed

    def _from_part1_contract(self, raw: dict[str, Any]) -> dict[str, Any]:
        """
        파트 1 WasteCase 계약(1.0 + 협의 필드)을 내부 형식으로 바꿉니다.
        예전 테스트 형식(waste_code가 맨 위)이면 바꾸지 않고 그대로 둡니다.

        - 분류번호: classification.selected_code → reported_code → 맨 위 waste_code
        - name(자유 기술)은 waste_name으로 넣지 않습니다 (공식 명칭 대조에서 MISMATCH가 나므로)
        - process_description → generation_process
        - region.sido를 업체 표 표기로 통일, location.region에도 복사(예전 코드 호환)
        - material_form → properties.physical_state, contamination에 석면 → properties.contains_asbestos
        """
        out = dict(raw)

        cls_info = raw.get("classification")
        if isinstance(cls_info, Mapping):
            selected = cls_info.get("selected_code") or cls_info.get("reported_code")
            if selected and not raw.get("waste_code"):
                out["waste_code"] = selected
            if cls_info.get("reported_code"):
                out["reported_code"] = cls_info["reported_code"]

        if raw.get("process_description") and not raw.get("generation_process"):
            out["generation_process"] = raw["process_description"]

        region = raw.get("region")
        if isinstance(region, Mapping):
            region = dict(region)
            sido = (region.get("sido") or "").strip()
            region["sido"] = self.SIDO_SHORT.get(sido, sido) or None
            out["region"] = region
            out.setdefault("location", {"region": region.get("sido"), "sigungu": region.get("sigungu")})

        properties = dict(raw.get("properties") or {})
        if raw.get("material_form") and "physical_state" not in properties:
            properties["physical_state"] = raw["material_form"]
        substances = (raw.get("contamination") or {}).get("substances") or []
        if any("석면" in str(s) for s in substances):
            properties["contains_asbestos"] = True
        if properties:
            out["properties"] = properties

        foreign = raw.get("foreign_matter")
        if isinstance(foreign, Mapping) and "separable" in foreign:
            foreign = dict(foreign)
            foreign["separable"] = self._parse_bool(foreign.get("separable"), "foreign_matter.separable")
            out["foreign_matter"] = foreign

        comps = raw.get("components")
        if isinstance(comps, list) and comps:
            if "is_mixed" not in raw:
                out["is_mixed"] = len(comps) > 1
            if "separable" not in raw and len(comps) > 1:
                seps = [self._parse_bool(c.get("separable"), "components[].separable")
                        for c in comps if isinstance(c, Mapping)]
                out["separable"] = None if (not seps or None in seps) else all(seps)

        return out

    def _normalize_fields(
        self,
        raw_data: dict[str, Any],
    ) -> dict[str, Any]:
        """다양한 필드명을 내부 표준 필드명으로 변환합니다."""

        normalized: dict[str, Any] = {}

        for standard_name, aliases in self.FIELD_ALIASES.items():
            found_values = [
                raw_data[key]
                for key in aliases
                if key in raw_data and raw_data[key] is not None
            ]

            if not found_values:
                continue

            # 같은 의미의 필드가 여러 개 존재하고 값이 다르면 오류 처리
            first_value = found_values[0]

            if any(value != first_value for value in found_values[1:]):
                raise InputAdapterError(
                    f"동일 필드의 값이 서로 다릅니다: {standard_name}"
                )

            normalized[standard_name] = first_value

        # 알 수 없는 필드는 원본에 남기고 내부 모델에는 자동 삽입하지 않습니다.
        return normalized

    def _validate_fields(
        self,
        data: dict[str, Any],
    ) -> None:
        """표준 필드의 자료형을 검증하고 변환합니다."""

        for field_name in self.BOOLEAN_FIELDS:
            if field_name not in data:
                continue

            data[field_name] = self._parse_bool(
                data[field_name],
                field_name,
            )

        for field_name in self.MAPPING_FIELDS:
            if field_name not in data:
                continue

            value = data[field_name]

            if value is None:
                data[field_name] = {}
            elif not isinstance(value, Mapping):
                raise InputAdapterError(
                    f"{field_name}은 JSON 객체여야 합니다."
                )
            else:
                data[field_name] = dict(value)

        for field_name in ("waste_code", "waste_name", "generation_process"):
            if field_name in data and data[field_name] is not None:
                if not isinstance(data[field_name], str):
                    raise InputAdapterError(
                        f"{field_name}은 문자열이어야 합니다."
                    )

                data[field_name] = data[field_name].strip() or None

    @staticmethod
    def _parse_bool(value: Any, field_name: str) -> bool | None:
        """문자열 및 숫자로 전달된 Boolean 값을 정규화합니다."""

        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, int) and value in (0, 1):
            return bool(value)

        if isinstance(value, str):
            normalized = value.strip().lower()

            true_values = {"true", "yes", "y", "1", "예", "있음"}
            false_values = {"false", "no", "n", "0", "아니오", "없음"}

            if normalized in true_values:
                return True

            if normalized in false_values:
                return False

            # 파트 1은 모르는 값을 "모름"으로 보낼 수 있습니다 (null과 같게 처리)
            if normalized in {"모름", "unknown", "", "null", "none"}:
                return None

        raise InputAdapterError(
            f"{field_name}의 Boolean 값이 올바르지 않습니다: {value}"
        )

    @staticmethod
    def _normalize_components(
        components: Any,
    ) -> list[dict[str, Any]]:
        """혼합 성분 목록을 표준 구조로 변환합니다."""

        if components is None:
            return []

        if not isinstance(components, list):
            raise InputAdapterError(
                "components는 JSON 배열이어야 합니다."
            )

        normalized_components: list[dict[str, Any]] = []

        for index, component in enumerate(components):
            if not isinstance(component, Mapping):
                raise InputAdapterError(
                    f"components[{index}]는 JSON 객체여야 합니다."
                )

            item = dict(component)

            name = (
                item.get("name")
                or item.get("component_name")
                or item.get("성분명")
                or item.get("material")
            )

            if not isinstance(name, str) or not name.strip():
                raise InputAdapterError(
                    f"components[{index}]에 성분명이 없습니다."
                )

            code = (
                item.get("waste_code")
                or item.get("분류번호")
            )

            share = (
                item.get("share_pct")
                if "share_pct" in item
                else item.get("share", item.get("비율"))
            )

            if share is not None:
                try:
                    share = float(share)
                except (TypeError, ValueError) as exc:
                    raise InputAdapterError(
                        f"components[{index}]의 비율이 숫자가 아닙니다."
                    ) from exc

                if not 0 <= share <= 100:
                    raise InputAdapterError(
                        f"components[{index}]의 비율은 0~100이어야 합니다."
                    )

            properties = item.get("properties") or {}

            if not isinstance(properties, Mapping):
                raise InputAdapterError(
                    f"components[{index}].properties는 객체여야 합니다."
                )

            normalized_components.append(
                {
                    "name": name.strip(),
                    "waste_code": str(code).strip() if code else None,
                    "share_pct": share,
                    "properties": dict(properties),
                    "material": item.get("material"),
                    "separable": InputAdapter._parse_bool(
                        item.get("separable"), f"components[{index}].separable"
                    ),
                    "status": item.get("status"),
                }
            )

        return normalized_components
