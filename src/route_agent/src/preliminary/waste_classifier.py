from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any

from .models import WasteClassificationResult


class DataLoadError(RuntimeError):
    """분류 데이터의 로딩 또는 무결성 검증 실패."""


OFFICIAL_CODE_PATTERN = re.compile(r"^\d{2}(?:-\d{2}(?:-\d{2})?)?$")
CATEGORY_CODE_PATTERN = re.compile(r"^\d{4}$")
TOP_LEVEL_PATTERN = re.compile(r"^\d{2}$")


def normalize_waste_code(value: Any) -> str | None:
    """
    폐기물 코드 정규화.

    - 4자리 폐기물코드(예: 0101)는 별도 네임스페이스이므로 보존합니다.
    - 공식 분류번호(예: 01, 01-01, 01-01-01)는 하이픈 형식을 보존합니다.
    - 하이픈 없는 6자리 공식 코드는 2자리씩 분리합니다.
    - 임의의 숫자 추출이나 임의 변환은 수행하지 않습니다.
    """
    if value is None:
        return None
    code = str(value).strip().replace(" ", "")
    if not code:
        return None
    if CATEGORY_CODE_PATTERN.fullmatch(code):
        return code
    if OFFICIAL_CODE_PATTERN.fullmatch(code):
        return code
    if re.fullmatch(r"\d{6}", code):
        return f"{code[:2]}-{code[2:4]}-{code[4:]}"
    return None


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", str(value))
    normalized = re.sub(r"\s+", "", normalized)
    return normalized or None


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise DataLoadError(f"CSV 파일을 찾을 수 없습니다: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            if not reader.fieldnames:
                raise DataLoadError(f"CSV 헤더가 없습니다: {path}")
            return [dict(row) for row in reader], list(reader.fieldnames)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataLoadError(f"CSV 로딩 실패: {path}") from exc


def _required_columns(path: Path, actual: list[str], required: set[str]) -> None:
    missing = required - set(actual)
    if missing:
        raise DataLoadError(f"{path.name} 필수 컬럼 누락: {sorted(missing)}")


def _first(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


class WasteClassifier:
    """공식 분류표와 4자리 폐기물코드 Crosswalk를 함께 검증합니다."""

    OFFICIAL_COLUMNS = {
        "분류번호", "단계", "상위", "명칭", "지정폐기물", "말단"
    }
    CROSSWALK_COLUMNS = {
        "폐기물코드", "폐기물종류", "분류번호_중분류",
        "중분류_명칭", "말단_분류번호", "말단_수"
    }

    def __init__(
        self,
        waste_codes_path: str | Path,
        crosswalk_path: str | Path | None = None,
    ) -> None:
        self.waste_codes_path = Path(waste_codes_path)
        self.crosswalk_path = Path(crosswalk_path) if crosswalk_path else None
        self._codes: dict[str, dict[str, str]] = {}
        self._crosswalk: dict[str, dict[str, Any]] = {}
        self._crosswalk_by_middle: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        code_rows, code_headers = _read_csv(self.waste_codes_path)
        _required_columns(self.waste_codes_path, code_headers, self.OFFICIAL_COLUMNS)

        codes: dict[str, dict[str, str]] = {}
        for row in code_rows:
            code = normalize_waste_code(row.get("분류번호"))
            if code is None:
                raise DataLoadError(f"잘못된 공식 분류번호: {row.get('분류번호')!r}")
            if code in codes:
                raise DataLoadError(f"공식 분류번호 중복: {code}")
            row["분류번호"] = code
            row["말단"] = (row.get("말단") or "").strip().upper()
            row["지정폐기물"] = (row.get("지정폐기물") or "").strip().upper()
            if row["말단"] not in {"Y", "N"}:
                raise DataLoadError(f"{code}: 말단 값은 Y/N이어야 합니다.")
            if row["지정폐기물"] not in {"Y", "N"}:
                raise DataLoadError(f"{code}: 지정폐기물 값은 Y/N이어야 합니다.")
            codes[code] = row

        crosswalk: dict[str, dict[str, Any]] = {}
        by_middle: dict[str, dict[str, Any]] = {}
        if self.crosswalk_path:
            cross_rows, cross_headers = _read_csv(self.crosswalk_path)
            _required_columns(self.crosswalk_path, cross_headers, self.CROSSWALK_COLUMNS)

            for row in cross_rows:
                category_code = (row.get("폐기물코드") or "").strip()
                middle_code = normalize_waste_code(row.get("분류번호_중분류"))
                if not category_code or not re.fullmatch(r"\d{2,4}", category_code):
                    raise DataLoadError(f"잘못된 폐기물코드: {category_code!r}")
                if category_code in crosswalk:
                    raise DataLoadError(f"Crosswalk 폐기물코드 중복: {category_code}")
                if middle_code is None:
                    raise DataLoadError(
                        f"{category_code}: 잘못된 분류번호_중분류 "
                        f"{row.get('분류번호_중분류')!r}"
                    )

                terminal_codes = [
                    normalize_waste_code(item)
                    for item in (row.get("말단_분류번호") or "").split(";")
                    if item.strip()
                ]
                if not terminal_codes or any(item is None for item in terminal_codes):
                    raise DataLoadError(f"{category_code}: 말단 분류번호 목록이 비어 있거나 잘못되었습니다.")
                if len(set(terminal_codes)) != len(terminal_codes):
                    raise DataLoadError(f"{category_code}: 말단 분류번호가 중복됩니다.")

                try:
                    declared_count = int((row.get("말단_수") or "").strip())
                except ValueError as exc:
                    raise DataLoadError(f"{category_code}: 말단_수가 정수가 아닙니다.") from exc
                if declared_count != len(terminal_codes):
                    raise DataLoadError(
                        f"{category_code}: 말단_수({declared_count})와 "
                        f"실제 목록 수({len(terminal_codes)})가 다릅니다."
                    )

                # Crosswalk의 중분류 코드가 공식 표에 없을 수 있으므로
                # 중분류 존재 여부나 직속 parent 관계는 강제하지 않습니다.
                for terminal in terminal_codes:
                    official = codes.get(terminal)
                    if official is None:
                        raise DataLoadError(
                            f"{category_code}: Crosswalk 말단 코드 {terminal}가 공식 분류표에 없습니다."
                        )
                    if official["말단"] != "Y":
                        raise DataLoadError(
                            f"{category_code}: Crosswalk 코드 {terminal}가 공식 말단(Y)이 아닙니다."
                        )
                    if not terminal.startswith(middle_code + "-"):
                        raise DataLoadError(
                            f"{category_code}: 말단 코드 {terminal}가 연결 코드 {middle_code} "
                            "하위 접두어와 일치하지 않습니다."
                        )

                item = {
                    "category_code": category_code,
                    "category_name": (row.get("폐기물종류") or "").strip(),
                    "middle_code": middle_code,
                    "middle_name": (row.get("중분류_명칭") or "").strip(),
                    "terminal_codes": terminal_codes,
                }
                crosswalk[category_code] = item
                if middle_code in by_middle:
                    raise DataLoadError(f"Crosswalk 중분류 코드 중복: {middle_code}")
                by_middle[middle_code] = item

        self._codes = codes
        self._crosswalk = crosswalk
        self._crosswalk_by_middle = by_middle

    def _name_matches(
        self,
        supplied_name: str | None,
        candidates: list[str | None],
    ) -> bool:
        supplied = _normalize_name(supplied_name)
        if supplied is None:
            return True
        normalized_candidates = {
            value for value in (_normalize_name(item) for item in candidates) if value
        }
        return supplied in normalized_candidates

    def _result(
        self,
        *,
        status: str,
        input_code: str | None,
        normalized_code: str | None,
        supplied_name: str | None,
        official_name: str | None = None,
        matched: bool = False,
        warnings: list[str] | None = None,
        row: dict[str, str] | None = None,
        category_code: str | None = None,
        terminal_codes: list[str] | None = None,
        classification_level: str | None = None,
    ) -> WasteClassificationResult:
        return WasteClassificationResult(
            status=status,
            input_waste_code=input_code,
            normalized_waste_code=normalized_code,
            waste_name=supplied_name or official_name,
            matched=matched,
            warnings=warnings or [],
            source=self.waste_codes_path.name,
            classification_level=classification_level or (row or {}).get("단계"),
            parent_code=(row or {}).get("상위") or None,
            is_terminal=(row or {}).get("말단") == "Y" if row else False,
            official_name=official_name,
            waste_category_code=category_code,
            terminal_codes=terminal_codes or [],
            hazardous_designation=(row or {}).get("지정폐기물"),
        )

    def classify(
        self,
        waste_code: str | None,
        waste_name: str | None = None,
    ) -> WasteClassificationResult:
        if waste_code is None or not str(waste_code).strip():
            return self._result(
                status="NEEDS_INFO",
                input_code=waste_code,
                normalized_code=None,
                supplied_name=waste_name,
                warnings=["폐기물 분류번호가 입력되지 않았습니다."],
            )

        normalized = normalize_waste_code(waste_code)
        if normalized is None:
            return self._result(
                status="REVIEW",
                input_code=waste_code,
                normalized_code=None,
                supplied_name=waste_name,
                warnings=["지원하지 않는 분류번호 형식입니다. 입력값을 확인해야 합니다."],
            )

        # 4자리 폐기물코드는 공식 분류번호로 변환하지 않고 Crosswalk에서 조회합니다.
        if re.fullmatch(r"\d{4}", normalized):
            mapping = self._crosswalk.get(normalized)
            if mapping is None:
                return self._result(
                    status="NOT_FOUND",
                    input_code=waste_code,
                    normalized_code=normalized,
                    supplied_name=waste_name,
                    warnings=["Crosswalk에서 4자리 폐기물코드를 찾지 못했습니다."],
                )
            middle_code = mapping["middle_code"]
            row = self._codes.get(middle_code)
            official_name = (
                (row or {}).get("명칭")
                or mapping["middle_name"]
                or mapping["category_name"]
                or None
            )
            candidates = [
                official_name, mapping["middle_name"], mapping["category_name"]
            ]
            if not self._name_matches(waste_name, candidates):
                return self._result(
                    status="MISMATCH",
                    input_code=waste_code,
                    normalized_code=middle_code,
                    supplied_name=waste_name,
                    official_name=official_name,
                    warnings=["입력 명칭이 Crosswalk 또는 공식 분류표의 명칭과 일치하지 않습니다."],
                    row=row,
                    category_code=normalized,
                    terminal_codes=mapping["terminal_codes"],
                    classification_level=(row or {}).get("단계") or "CROSSWALK_GROUP",
                )
            warnings = [
                "4자리 폐기물코드는 중분류 연결 코드로 해석했습니다. 말단 분류번호를 추가 확인해야 합니다."
            ]
            if not row:
                warnings.append(
                    "Crosswalk 연결 코드는 공식 분류표에 독립 항목으로 존재하지 않습니다. "
                    "말단 코드 목록을 기준으로 세부 분류를 확인해야 합니다."
                )
            if not waste_name:
                warnings.append("입력 명칭이 없어 Crosswalk의 명칭을 참고해 표시했습니다.")
            return self._result(
                status="PARENT_CLASS",
                input_code=waste_code,
                normalized_code=middle_code,
                supplied_name=waste_name,
                official_name=official_name,
                warnings=warnings,
                row=row,
                category_code=normalized,
                terminal_codes=mapping["terminal_codes"],
                classification_level=(row or {}).get("단계") or "CROSSWALK_GROUP",
            )

        # 공식 코드 우선 조회. 말단 여부는 단계가 아니라 말단 컬럼으로 판단합니다.
        row = self._codes.get(normalized)
        mapping = self._crosswalk_by_middle.get(normalized)
        if row is None and mapping is not None:
            official_name = mapping["middle_name"] or mapping["category_name"] or None
            if not self._name_matches(waste_name, [official_name, mapping["category_name"]]):
                return self._result(
                    status="MISMATCH",
                    input_code=waste_code,
                    normalized_code=normalized,
                    supplied_name=waste_name,
                    official_name=official_name,
                    warnings=["입력 명칭이 Crosswalk 명칭과 일치하지 않습니다."],
                    category_code=mapping["category_code"],
                    terminal_codes=mapping["terminal_codes"],
                    classification_level="CROSSWALK_GROUP",
                )
            warnings = ["공식 분류표에 중분류 행은 없지만 Crosswalk 연결 그룹으로 확인했습니다."]
            if not waste_name:
                warnings.append("입력 명칭이 없어 Crosswalk의 명칭을 참고해 표시했습니다.")
            return self._result(
                status="PARENT_CLASS",
                input_code=waste_code,
                normalized_code=normalized,
                supplied_name=waste_name,
                official_name=official_name,
                warnings=warnings,
                category_code=mapping["category_code"],
                terminal_codes=mapping["terminal_codes"],
                classification_level="CROSSWALK_GROUP",
            )

        if row is None:
            return self._result(
                status="NOT_FOUND",
                input_code=waste_code,
                normalized_code=normalized,
                supplied_name=waste_name,
                warnings=["공식 분류표와 Crosswalk에서 분류번호를 찾지 못했습니다."],
            )

        official_name = (row.get("명칭") or "").strip() or None
        if not self._name_matches(waste_name, [official_name]):
            return self._result(
                status="MISMATCH",
                input_code=waste_code,
                normalized_code=normalized,
                supplied_name=waste_name,
                official_name=official_name,
                warnings=["입력 명칭이 공식 분류표의 명칭과 일치하지 않습니다."],
                row=row,
            )

        if row["말단"] != "Y":
            warnings = ["입력된 분류번호는 말단 분류가 아닙니다. 하위 분류번호를 확인해야 합니다."]
            if not waste_name:
                warnings.append("입력 명칭이 없어 공식 분류표의 명칭을 표시했습니다.")
            linked = self._crosswalk_by_middle.get(normalized)
            return self._result(
                status="PARENT_CLASS",
                input_code=waste_code,
                normalized_code=normalized,
                supplied_name=waste_name,
                official_name=official_name,
                warnings=warnings,
                row=row,
                category_code=(linked or {}).get("category_code"),
                terminal_codes=(linked or {}).get("terminal_codes", []),
            )

        warnings = []
        if not waste_name:
            warnings.append("입력 명칭이 없어 공식 분류표의 명칭을 사용했습니다.")
        return self._result(
            status="MATCHED",
            input_code=waste_code,
            normalized_code=normalized,
            supplied_name=waste_name,
            official_name=official_name,
            matched=True,
            warnings=warnings,
            row=row,
        )
