"""Named entries for official forms. Each rectangle is measured on its pinned original.

Coordinates are PDF points, with y measured from the top. Only the caller's explicit
values are placed; authority fields, legal declarations, signatures and checkboxes
remain part of the unchanged original.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
import unicodedata

_ROOT = Path(__file__).resolve().parents[1]

_FILES = {
    "dplan": ("templates/official/지정/별지제12호_폐기물처리계획_변경서.pdf",
              "369bf1b0b4ba793dba7f52900980114d3f662ef8b7686a348c771f1ccbed3d89"),
    "cplan": ("templates/official/건설/별지제7호_건설폐기물처리계획서_변경계획서.pdf",
              "57f3ec4cc0654e0fe46c051b809c6432280a513018ec79e0312d14984e79daf0"),
    "assessment": ("templates/official/평가/별지4의2_재활용환경성평가_신청서.pdf",
                   "26113f3344f2fe091629db1b00e824599e7e7fa11e77a35e2f517038e0b4aab1"),
    "approval": ("templates/official/평가/별지4의4_재활용_승인신청서.pdf",
                 "0e4591f8977250bc9c2db11efe299b21df20ce673426fd60408e74b89d9a70bf"),
}

# Common labels deliberately retain the applicant role: the applicant is not
# necessarily the business generating the waste. Units are the original's units.
_APP_LABELS = {
    "applicant_name": "신청인 상호(명칭)",
    "applicant_registration": "신청인 사업자등록번호",
    "applicant_representative": "신청인 성명(대표자)",
    "applicant_birthdate": "신청인 생년월일",
    "applicant_address": "신청인 주소",
    "applicant_phone": "신청인 전화번호",
    "applicant_email": "신청인 이메일",
    "target_waste_type": "대상 폐기물 종류",
    "assessment_tons": "대상 폐기물 양(톤)",
    "recycling_location": "재활용 장소 소재지",
    "recycling_area_m2": "재활용 장소 면적(제곱미터)",
    "recycling_type": "재활용 유형",
    "application_notes": "신청 내용 기타",
}

_LABELS = {"assessment": _APP_LABELS, "approval": _APP_LABELS}

# Values go beside existing labels. Larger unsplit areas are available in the
# recycling-type and other-notes rows. No rows cross a divider or heading.
_BOXES = {
    "assessment": {
        "applicant_name": (165, 374, 170, 193),
        "applicant_registration": (454, 534, 170, 193),
        "applicant_representative": (175, 374, 198, 221),
        "applicant_birthdate": (426, 534, 198, 221),
        "applicant_address": (135, 534, 226, 249),
        "applicant_phone": (155, 374, 254, 277),
        "applicant_email": (416, 534, 254, 277),
        "target_waste_type": (195, 374, 283, 307),
        "assessment_tons": (413, 534, 283, 307),
        "recycling_location": (204, 374, 311, 335),
        "recycling_area_m2": (448, 534, 311, 335),
        "recycling_type": (173, 534, 340, 364),
        "application_notes": (173, 534, 369, 392),
    },
    "approval": {
        "applicant_name": (163, 352, 124, 145),
        "applicant_registration": (433, 525, 124, 145),
        "applicant_representative": (173, 352, 150, 171),
        "applicant_birthdate": (403, 525, 150, 171),
        "applicant_address": (133, 525, 176, 196),
        "applicant_phone": (153, 352, 201, 222),
        "applicant_email": (394, 525, 201, 222),
        "target_waste_type": (196, 352, 228, 249),
        "assessment_tons": (391, 525, 228, 249),
        "recycling_location": (205, 352, 254, 275),
        "recycling_area_m2": (426, 525, 254, 275),
        "recycling_type": (173, 525, 280, 322),
        "application_notes": (173, 525, 327, 348),
    },
}


# Registry for the two generator plans. Reminder: the mailing address on the
# construction form and the project site are independent from a registered facility.
_PLAN_ENTRIES = {
    "dplan": [
        ("generator_name", "제출인 상호(명칭)", (190, 329, 156, 178)),
        ("generator_registration", "제출인 사업자등록번호", (423, 534, 156, 178)),
        ("generator_representative", "제출인 성명(대표자)", (200, 329, 182, 204)),
        ("generator_birthdate", "제출인 생년월일", (394, 534, 182, 204)),
        ("generator_address", "제출인 사업장 주소", (200, 534, 207, 219.6)),  # 아래 줄(산업단지·전화, 220~)과 겹치지 않게
        ("industrial_park", "사업장 소재지 산업단지명", (335, 414, 220, 233)),
        ("generator_phone", "제출인 사업장 전화번호", (469, 528, 220, 233)),
        ("designated_industry", "배출사업장 업종(세세분류)", (160, 534, 238, 259)),
        ("raw_materials_annual", "주 원료명 및 사용량(톤/년)", (120, 329, 275, 286)),
        ("products_annual", "주 생산품명 및 생산량(톤/년)", (334, 534, 275, 286)),
        ("manufacturing_process", "제조공정", (179, 534, 289, 311)),
        ("waste_legal_name", "첫째 행 지정폐기물 종류(확인된 법정 명칭)", (60, 101, 389, 409)),
        ("designated_waste_code", "첫째 행 지정폐기물 분류번호(00-00-00)", (110, 169, 393, 405)),
        ("designated_kg_month", "첫째 행 배출량(kg/월)", (178, 207, 389, 409)),
        ("designated_tons_year", "첫째 행 배출량(톤/년)", (212, 244, 389, 409)),
        ("designated_cycle", "첫째 행 배출주기", (248, 286, 389, 409)),
        ("designated_state", "첫째 행 성질ㆍ상태", (290, 329, 389, 409)),
        ("designated_transporter", "첫째 행 운반자", (334, 365, 389, 409)),
        ("designated_transport_tons_year", "첫째 행 운반량(톤/년)", (370, 401, 389, 409)),
        ("designated_treatment_mode", "첫째 행 처리구분", (405, 434, 389, 409)),
        ("designated_processor", "첫째 행 처리업소명", (439, 470, 389, 409)),
        ("designated_treatment_method", "첫째 행 처리방법(확인된 분류ㆍ번호)", (474, 497, 389, 409)),
        ("designated_treatment_tons_year", "첫째 행 처리량(톤/년)", (501, 534, 389, 409)),
        ("medical_facility_type", "의료폐기물 발생기관 구분(해당 시)", (179, 534, 491, 520)),
    ],
    "cplan": [
        ("generator_name", "신고인 상호(명칭)", (160, 347, 181, 197)),
        ("client_relationship", "신고인과 발주자의 관계", (441, 529, 181, 197)),
        ("generator_representative", "신고인 성명(대표자)", (169, 347, 201, 217)),
        ("generator_birthdate", "신고인 생년월일", (406, 529, 201, 217)),
        ("construction_mailing_address", "신고인 우편접수 가능 주소", (135, 397, 221, 239)),
        ("generator_phone", "신고인 전화번호", (449, 520, 227, 240)),
        ("construction_industry", "신고인 업종(소분류)", (135, 347, 243, 258)),
        ("generator_registration", "신고인 사업자등록번호", (436, 529, 243, 258)),
        ("construction_project", "공사명", (145, 347, 267, 282)),
        ("construction_period", "전체 공사기간", (421, 529, 267, 282)),
        ("construction_site", "공사(배출) 현장주소", (200, 397, 286, 305)),
        ("construction_site_phone", "공사현장 전화번호", (449, 520, 293, 305)),
        ("aggregate_duty_details", "순환골재 등 의무사용 건설공사의 내용", (296, 529, 309, 324)),
        ("construction_owner", "발주자 상호(명칭)", (150, 311, 328, 344)),
        ("construction_owner_rep", "발주자 대표자", (370, 529, 328, 344)),
        ("construction_owner_address", "발주자 주소", (126, 402, 348, 366)),
        ("construction_owner_phone", "발주자 전화번호", (455, 520, 354, 367)),
        ("construction_waste_group", "건설폐기물 분류", (52, 84, 424, 442)),
        ("construction_waste_code", "건설폐기물 분류번호(00-00-00)", (91, 157, 427, 440)),
        ("construction_waste_type", "건설폐기물 종류(확인된 종류)", (164, 204, 424, 442)),
        ("construction_expected_tons", "종류별 예상 발생량(톤)", (209, 250, 424, 442)),
        ("construction_cycle", "종류별 발생주기", (254, 287, 424, 442)),
        ("construction_treatment_mode", "처리구분(자가ㆍ위탁)", (292, 323, 424, 442)),
        ("construction_transporter", "운반자", (328, 367, 424, 442)),
        ("construction_transport_tons", "계획 운반량(톤)", (372, 403, 424, 442)),
        ("construction_processor", "처리 업소명", (407, 448, 424, 442)),
        ("construction_treatment_method", "처리방법", (452, 491, 424, 442)),
        ("construction_treatment_tons", "계획 처리량(톤)", (496, 529, 424, 442)),
        ("construction_separation", "건설폐기물 분리배출계획", (184, 529, 446, 463)),
        ("construction_storage", "건설폐기물 보관방법", (184, 529, 467, 484)),
    ],
}
for _doc, _entries in _PLAN_ENTRIES.items():
    _LABELS[_doc] = {key: label for key, label, _ in _entries}
    _BOXES[_doc] = {key: bounds for key, _, bounds in _entries}


# The original prints six individual boxes, including hyphens. Put one digit
# inside each box, leaving all printed strokes and separators intact.
_CODE_DIGITS = {
    "designated_waste_code": ((113.3, 121.3, 133.3, 141.3, 153.3, 161.3), 395.8, 402.7),
    "construction_waste_code": ((94.7, 103.5, 117.4, 126.2, 140.2, 149.0), 429.5, 437.2),
}


def _specs():
    return {doc: {"file": item[0],
                  "fields": [{"key": key, "label": _LABELS[doc][key]} for key in _BOXES[doc]]}
            for doc, item in _FILES.items()}


SPECS = _specs()


def render_extra(doc: str, values: dict, *, regions=None) -> bytes:
    """Fill supported keys only; an empty set of values returns the original bytes."""
    from .pdf_writer import render_overlay

    if not isinstance(doc, str) or doc not in SPECS:
        raise ValueError("지원하지 않는 서식입니다.")
    if not isinstance(values, dict):
        raise ValueError("입력값은 항목과 문자열 값의 쌍이어야 합니다.")
    boxes = _BOXES[doc]
    for key in values:
        if key not in boxes:
            raise ValueError(f"지원하지 않는 입력 항목: {key}")
    relative_file, digest = _FILES[doc]
    source = (_ROOT / relative_file).read_bytes()
    if hashlib.sha256(source).hexdigest() != digest:
        raise ValueError("서식 원본이 변경되어 자동 배치할 수 없습니다. 원본을 확인하세요.")
    if regions is not None:
        regions.extend((key, 0, *bounds) for key, bounds in boxes.items())
    layout = []
    for key, bounds in boxes.items():
        if key not in values:
            continue
        label = _LABELS[doc][key]
        raw = values[key]
        if not isinstance(raw, str):
            raise ValueError(f"{label}: 문자열을 입력하세요.")
        if len(raw) > 1500:
            raise ValueError(f"{label}: 칸에 넣기에는 값이 너무 깁니다.")
        value = unicodedata.normalize("NFC", raw)
        if any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in value):
            raise ValueError(f"{label}: 줄바꿈, 탭, 숨은 문자를 사용할 수 없습니다.")
        if value.strip():
            value = value.strip()
            if key in _CODE_DIGITS:
                if not re.fullmatch(r"[0-9]{6}|[0-9]{2}-[0-9]{2}-[0-9]{2}", value):
                    raise ValueError(f"{label}: 확인한 숫자 6자리를 00-00-00 형식으로 입력하세요.")
                positions, top, bottom = _CODE_DIGITS[key]
                layout.extend((0, x, x + 4.8, top, bottom, digit, label)
                              for x, digit in zip(positions, value.replace("-", "")))
            else:
                layout.append((0, *bounds, value, label))
    return render_overlay(source, layout, expected_sha256=digest) if layout else source
