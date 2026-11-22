"""Local-only text placement on the visible pages of an unsigned PDF."""
from __future__ import annotations

import base64
import binascii
import io
import math
import re
import threading
from collections import defaultdict

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from .original_cells import cells_for_export
from .pdf_writer import FONT, _clean, _flow, _width

MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 64
MAX_PLACEMENTS = 100
MAX_TEXT = 500
# PDFium is not thread safe, including separate documents in the same process.
_PDFIUM_LOCK = threading.Lock()


def _pdf_data(payload):
    if not isinstance(payload, dict):
        raise ValueError("PDF와 추가할 문구를 전송해 주세요.")
    encoded = payload.get("pdf")
    max_encoded = 4 * ((MAX_SOURCE_BYTES + 2) // 3)
    if not isinstance(encoded, str) or not 8 <= len(encoded) <= max_encoded:
        raise ValueError("4 MiB 이하의 PDF를 선택해 주세요.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("PDF 파일 내용을 읽을 수 없습니다.") from None
    if len(data) > MAX_SOURCE_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("4 MiB 이하의 PDF를 선택해 주세요.")
    return data


def _index(value, count):
    if type(value) is not int or not 0 <= value < count:
        raise ValueError("문서 안에 있는 쪽 번호를 선택해 주세요.")
    return value


def _number(value, label, lower, upper):
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError(f"{label} 범위를 확인해 주세요.")
    return float(value)


def _native_check(data):
    with _PDFIUM_LOCK, pdfium.PdfDocument(data) as doc:
        count = len(doc)
        if not 1 <= count <= MAX_PAGES:
            raise ValueError(f"{MAX_PAGES}쪽 이하의 PDF를 선택해 주세요.")
        if pdfium.raw.FPDF_GetSignatureCount(doc) > 0:
            raise ValueError("전자서명된 PDF입니다. 서명 전 원본을 선택해 주세요.")
    return count


def _read(data):
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("암호화된 PDF입니다. 편집 가능한 원본을 선택해 주세요.")
        count = _native_check(data)
        if len(reader.pages) != count:
            raise ValueError("문서 쪽 구성이 올바르지 않습니다.")
        root = reader.trailer["/Root"]
        if root.get("/Perms") or (root.get("/AcroForm") and any(
                field.get("/FT") == "/Sig" and field.get("/V")
                for field in (reader.get_fields() or {}).values())):
            raise ValueError("전자서명된 PDF입니다. 서명 전 원본을 선택해 주세요.")
        return reader, count
    except ValueError:
        raise
    except Exception:
        raise ValueError("PDF를 열 수 없습니다. 파일 형식과 손상 여부를 확인해 주세요.") from None


def _view(page):
    """Map displayed bottom-left coordinates to native PDF user space."""
    media = page.mediabox
    crop = page.cropbox
    left = float(max(media.left, crop.left))
    bottom = float(max(media.bottom, crop.bottom))
    right = float(min(media.right, crop.right))
    top = float(min(media.top, crop.top))
    width, height = right - left, top - bottom
    rotation = page.rotation % 360
    if not (1 <= width <= 4000 and 1 <= height <= 4000 and rotation in (0, 90, 180, 270)):
        raise ValueError("이 PDF의 페이지 크기 또는 회전값을 지원하지 않습니다.")
    if rotation == 90:
        return height, width, (0, 1, -1, 0, right, bottom)
    if rotation == 180:
        return width, height, (-1, 0, 0, -1, right, top)
    if rotation == 270:
        return height, width, (0, -1, 1, 0, left, top)
    return width, height, (1, 0, 0, 1, left, bottom)


def _fit_cell(cell, text, size):
    if "parts" in cell:
        if not re.fullmatch(r"(?:[0-9]{6}|[0-9]{2}-[0-9]{2}-[0-9]{2})", text):
            raise ValueError(f"{cell['label']}: 여섯 자리 숫자를 11-22-33 또는 112233 형식으로 입력하세요.")
        digits = text.replace("-", "")
        values = zip(cell["parts"], (digits[0:2], digits[2:4], digits[4:6]))
    else:
        values = [(cell["write"], text)]
    pieces = []
    for (left, top, right, bottom), value in values:
        pieces.extend(_flow(value, [(left, right, top, bottom)], cell["label"], max_size=size, min_size=6))
    return pieces


def _placements(payload, reader, count, source):
    entries = payload.get("placements", [])
    if not isinstance(entries, list) or len(entries) > MAX_PLACEMENTS:
        raise ValueError(f"문구는 최대 {MAX_PLACEMENTS}개까지 추가할 수 있습니다.")
    result = defaultdict(list)
    cells = None
    used = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("추가할 문구의 형식을 확인해 주세요.")
        index = _index(entry.get("page"), count)
        size = _number(entry.get("size"), "글씨 크기", 6, 36)
        text = entry.get("text")
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            raise ValueError(f"문구 하나는 {MAX_TEXT}자 이내로 입력해 주세요.")
        text = _clean(text, "추가 문구")
        if not text:
            raise ValueError("추가할 문구를 입력해 주세요.")
        if "cellKey" in entry:
            if cells is None:
                cells = cells_for_export(payload.get("doc"), source)
            key = entry["cellKey"]
            if not isinstance(key, str) or key not in cells or index != cells[key]["page"]:
                raise ValueError("현재 쪽에 등록된 칸을 선택하세요.")
            if key in used:
                raise ValueError(f"{cells[key]['label']}에 값이 두 개 있습니다. 기존 값을 수정하세요.")
            used.add(key)
            result[index].extend(_fit_cell(cells[key], text, size))
            continue
        width, height, _ = _view(reader.pages[index])
        x = _number(entry.get("x"), "가로 위치", 0, width)
        y = _number(entry.get("y"), "세로 위치", 0, height)
        lines = text.split("\n")
        leading = size * 1.25
        if (x + max(_width(line, size) for line in lines) > width or
                y + leading * len(lines) > height):
            raise ValueError(f"{index + 1}쪽의 문구가 페이지를 벗어납니다. 위치, 줄바꿈 또는 크기를 조정해 주세요.")
        for i, line in enumerate(lines):
            result[index].append((x, y + size * .95 + i * leading, line, size))
    return result


def _merge(reader, marks):
    writer = PdfWriter(clone_from=reader)
    for index, entries in marks.items():
        page = writer.pages[index]
        width, height, transform = _view(page)
        out = io.BytesIO()
        c = Canvas(out, pagesize=(width, height), pageCompression=1, invariant=1)
        c.setFillColorRGB(.035, .105, .15)
        for x, baseline, text, size in entries:
            c.setFont(FONT, size)
            c.drawString(x, height - baseline, text)
        c.save()
        layer = PdfReader(io.BytesIO(out.getvalue()))
        page.merge_transformed_page(layer.pages[0], transform)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def _prepare(payload, *, current_only=False):
    source = _pdf_data(payload)
    reader, count = _read(source)
    index = _index(payload.get("page", 0), count)
    width, height, _ = _view(reader.pages[index])
    marks = _placements(payload, reader, count, source)
    if current_only:
        marks = {index: marks[index]} if index in marks else {}
    try:
        data = _merge(reader, marks) if marks else source
    except Exception:
        raise ValueError("문구를 PDF에 기록할 수 없습니다. 편집 가능한 원본을 확인해 주세요.") from None
    return data, index, count, width, height


def preview_info(payload):
    """Return PNG, page count, displayed width and displayed height (PDF points)."""
    data, index, count, width, height = _prepare(payload, current_only=True)
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(data) as document:
            page = document[index]
            try:
                scale = min(2, 1800 / max(width, height))
                bitmap = page.render(scale=scale, draw_annots=True)
                try:
                    out = io.BytesIO()
                    with bitmap.to_pil() as im:
                        im.save(out, format="PNG", compress_level=3)
                finally:
                    bitmap.close()
            finally:
                page.close()
    except Exception:
        raise ValueError("이 쪽의 미리보기를 만들 수 없습니다.") from None
    return out.getvalue(), count, width, height


def preview(payload) -> bytes:
    return preview_info(payload)[0]


def export(payload) -> bytes:
    return _prepare(payload)[0]
