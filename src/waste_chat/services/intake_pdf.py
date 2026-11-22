"""Fill the user's original two-page WasteCase intake PDF, including its checkbox marks."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

ORIGINAL = Path(__file__).resolve().parents[1] / "templates" / "intake" / "원본_폐기물기초정보입력서.pdf"
ORIGINAL_SHA256 = "d271af3bf977f8bd0ecc75c58a1939cb28582108d50b188168fdcf937857175d"

# Labels follow the printed form; site name, contact phone and quantity have
# deliberately distinct meanings from addresses, business phones and annual tonnes.
INTAKE_FIELDS = [
    {"key": "intake_date", "label": "작성일자"},
    {"key": "intake_author", "label": "작성자"},
    {"key": "generator_name", "label": "업체명 (상호)"},
    {"key": "generator_registration", "label": "사업자등록번호"},
    {"key": "contact_name", "label": "담당자 성명"},
    {"key": "contact_phone", "label": "담당자 연락처"},
    {"key": "site_name", "label": "현장 명칭"},
    {"key": "generation_process", "label": "발생 공정"},
    {"key": "waste_name", "label": "폐기물 명칭"},
    {"key": "waste_state", "label": "폐기물 상태", "choices": ["고체", "액체", "슬러지"]},
    {"key": "mixture_state", "label": "혼합 여부", "choices": ["단일 물질", "여러 물질 혼합됨"]},
    {"key": "quantity_value", "label": "배출 물량 수치"},
    {"key": "quantity_unit", "label": "배출 물량 단위"},
    {"key": "contamination_level", "label": "오염 강도", "choices": ["무오염", "약함", "강함"]},
    {"key": "contamination_detail", "label": "오염 상세 설명"},
    {"key": "hazard_status", "label": "유해물질 포함 여부", "choices": ["없음", "있음", "확인 필요"]},
    {"key": "hazard_detail", "label": "위험 성분 설명"},
    {"key": "additional_notes", "label": "기타 특이사항"},
    {"key": "attachment_types", "label": "첨부 자료 종류", "choices": ["현장/근접/라벨 사진", "과거 신고증명서", "성분분석서"]},
]
_LABEL = {f["key"]: f["label"] for f in INTAKE_FIELDS}
# PDF points from the top edge (page index, left, right, top, bottom).
_BOX = {
    "intake_date": (0, 96.2, 233, 89.8, 104.8),
    "intake_author": (0, 308.0, 481, 89.8, 104.8),
    "generator_name": (0, 199, 534, 231, 259.3),
    "generator_registration": (0, 199, 534, 271, 299.1),
    "contact_name": (0, 226.5, 300.8, 321.3, 333.3),
    "contact_phone": (0, 412.6, 501.0, 321.3, 333.3),
    "site_name": (0, 199, 534, 392.0, 419.7),
    "generation_process": (0, 199, 534, 448.3, 476.0),
    "waste_name": (0, 199, 534, 548.0, 575.0),
    "quantity_value": (0, 226.5, 300.8, 673.2, 685.2),
    "quantity_unit": (0, 401.6, 476.0, 673.2, 685.2),
    "contamination_detail": (1, 199, 534, 121.2, 144.4),  # 위 줄(오염 강도, 아래끝 120.6)과 겹치지 않게
    "hazard_detail": (1, 199, 534, 195.2, 219.7),
    "additional_notes": (1, 202, 531, 314.0, 354.6),
}
# Actual checkbox centers measured in the original PDF. Synonyms below are
# exact equivalents, not inferences about chemistry, mixing, or contamination.
_CHECKS = {
    "waste_state": (0, 616.3, [(238.4, ("고체", "고상")), (291.6, ("액체", "액상")), (344.7, ("슬러지", "슬러지(진흙)", "슬러지(진흙) 상태"))]),
    "mixture_state": (0, 641.1, [(238.4, ("단일 물질", "단일")), (315.5, ("여러 물질 혼합됨", "여러 물질 혼합", "혼합", "혼합물"))]),
    "contamination_level": (1, 110.9, [(204.8, ("무오염", "오염 없음")), (268.6, ("약함", "약한 오염")), (321.8, ("강함", "강한 오염", "강함 (심각한 오염)"))]),
    "hazard_status": (1, 186.0, [(204.8, ("없음", "미포함")), (258.0, ("있음", "포함")), (311.3, ("확인 필요", "모름", "미확인", "확인 필요 (모름)"))]),
    "attachment_types": (1, 381.0, [(204.8, ("현장/근접/라벨 사진", "현장 사진", "근접 사진", "라벨 사진", "사진")), (329.9, ("과거 신고증명서", "신고증명서")), (438.5, ("성분분석서", "성분 분석서"))]),
}
# Preserve answers such as "미확인" when the printed row has no matching choice.
_OTHER = {
    "waste_state": (0, 445, 535, 605, 626),
    "mixture_state": (0, 412, 535, 630, 652),
    "contamination_level": (1, 425, 535, 100.7, 120.6),
    "hazard_status": (1, 405, 535, 176.4, 195.0),
}

# Right edges of the printed choice labels in this specific original, measured
# with pdftotext -bbox. The left edge follows the same checkbox as the PDF ink.
_CHOICE_END = {
    "waste_state": (273.3, 326.5, 442.0),
    "mixture_state": (297.3, 408.8),
    "contamination_level": (250.4, 303.6, 422.1),
    "hazard_status": (239.9, 293.1, 401.1),
    "attachment_types": (311.7, 420.3, 504.9),
}

def _field_regions():
    for key, rect in {**_BOX, **_OTHER}.items():
        yield (key, *rect)
    for key, (page, center_y, options) in _CHECKS.items():
        for (center_x, terms), right in zip(options, _CHOICE_END[key]):
            yield (key, page, center_x - 6.4, right, center_y - 7, center_y + 8, terms[0])

def _norm(v):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", str(v))).casefold()

def _text(v):
    return unicodedata.normalize("NFC", str(v)).strip()

def _strip_example_text(page, matrix_locations, expected_count):
    """Remove complete template-only BT..ET groups, retaining every other operator.

    The static PDF stores the demo author/date and floating notes prompt as
    real searchable text. Removing only those known text objects also prevents
    copies of the generated intake from containing stale leads behind the ink.
    This map is guarded by the full original file hash and operator count.
    """
    stream = page.get_contents()
    out = []
    group = None
    removed = 0
    for args, op in stream.operations:
        if op == b"BT":
            group = [(args, op)]
        elif group is None:
            out.append((args, op))
        else:
            group.append((args, op))
            if op == b"ET":
                positions = {(round(float(a[-2]), 3), round(float(a[-1]), 3))
                             for a, o in group if o == b"Tm"}
                if positions & matrix_locations:
                    removed += 1
                else:
                    out.extend(group)
                group = None
    if group is not None or removed != expected_count:
        raise ValueError("기초 입력서의 원본 예시 위치를 확인할 수 없습니다.")
    stream.operations = out
    page.replace_contents(stream)


@lru_cache(maxsize=2)
def _base(with_notes):
    original = ORIGINAL.read_bytes()
    if sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("기초 입력서 원본이 변경되어 기존 위치에 작성할 수 없습니다.")
    writer = PdfWriter(clone_from=BytesIO(original))
    _strip_example_text(writer.pages[0], {(124.484, 135.0), (407.109, 135.0)}, 6)
    if with_notes:
        _strip_example_text(writer.pages[1], {(277.875, 1566.0)}, 22)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _draw_checks(pdf, marks):
    if not marks:
        return pdf
    writer = PdfWriter(clone_from=BytesIO(pdf))
    for index in sorted({m[0] for m in marks}):
        page = writer.pages[index]
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        layer_buf = BytesIO()
        c = canvas.Canvas(layer_buf, pagesize=(w, h))
        c.setLineCap(1)
        c.setLineJoin(1)
        c.setStrokeColorRGB(0.08, 0.18, 0.32)
        c.setLineWidth(1.45)
        for page_id, x, top in marks:
            if page_id != index:
                continue
            y = h - top
            path = c.beginPath()
            path.moveTo(x - 3.1, y + 0.0)
            path.lineTo(x - 0.6, y - 2.6)
            path.lineTo(x + 3.8, y + 3.0)
            c.drawPath(path)
        c.showPage()
        c.save()
        page.merge_page(PdfReader(BytesIO(layer_buf.getvalue())).pages[0])
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def render_intake(doc, values, *, regions=None) -> bytes:
    """Render confirmed fields on the original. Never convert quantities or guess choices."""
    from .pdf_writer import render_overlay
    known = {key: val for key, val in values.items()
             if key in _LABEL and val is not None and str(val).strip()}
    boxes = []
    marks = []
    notes = _text(known.get("additional_notes", ""))
    for key, (page, top, options) in _CHECKS.items():
        if key not in known:
            continue
        raw = known[key]
        if key == "attachment_types":
            items = raw if isinstance(raw, (list, tuple)) else re.split(r"\s*[,;，、\n]\s*", str(raw))
        else:
            items = [raw]
        for item in items:
            item = _text(item)
            if not item:
                continue
            pos = next((x for x, synonyms in options if _norm(item) in {_norm(s) for s in synonyms}), None)
            if pos is not None:
                mark = (page, pos, top)
                if mark not in marks:
                    marks.append(mark)
            elif key in _OTHER:
                boxes.append((*_OTHER[key], item, _LABEL[key]))
            else:
                # The original offers three attachment types only; retain other
                # declarations (including "없음") without asserting an upload.
                notes += ("\n" if notes else "") + "첨부 자료: " + item
    if notes:
        known["additional_notes"] = notes
    for key, rect in _BOX.items():
        if key in known:
            boxes.append((*rect, _text(known[key]), _LABEL[key]))
    base = _base(bool(notes))
    if regions is not None:
        regions.extend(_field_regions())
    return _draw_checks(render_overlay(base, boxes) if boxes else base, marks)
