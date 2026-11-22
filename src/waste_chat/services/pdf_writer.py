"""Korean PDF output. Coordinates are unrotated PDF points measured from page top."""
from __future__ import annotations

import hashlib
import io
import re
import threading
import unicodedata
from collections import defaultdict
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
FONT = "WasteWorkDroidSans"
FONT_PATH = ROOT / "assets" / "DroidSansFallback.ttf"
_FONT_LOCK = threading.Lock()
_SOURCE5 = "templates/official/일반/별지제5호_수탁처리능력확인서_폐기물수탁확인서.pdf"
_SOURCE6 = "templates/official/일반/별지제6호_사업장폐기물배출자신고서_변경신고서.pdf"
_HASH5 = "646e0f76517e07a7f6df80312eac3bb27f925b636ba2f6cf03017696ac7abb92"
_HASH6 = "f9878e0d0b319314c8e28802f6de71adb86aab339b3160eee2439aeeb90e49e8"


def _font():
    with _FONT_LOCK:
        if FONT not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
            except Exception as e:
                raise ValueError("한글 글꼴을 불러올 수 없습니다. assets/DroidSansFallback.ttf 파일을 복원해 주세요.") from e
    return pdfmetrics.getFont(FONT)


def _clean(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: 입력값은 문자열이어야 합니다.")
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if not value:
        return ""
    if len(value) > 10000:
        raise ValueError(f"{label}: 내용이 너무 깁니다. 별지로 나누어 주세요.")
    glyphs = _font().face.charToGlyph
    for ch in value:
        cat = unicodedata.category(ch)
        if ch == "\n":
            continue
        if (not glyphs.get(ord(ch)) or cat.startswith(("C", "M")) or cat in ("Zl", "Zp")
                or unicodedata.bidirectional(ch) in {"R", "AL", "AN"}):
            raise ValueError(f"{label}: 인쇄할 수 없는 문자(U+{ord(ch):04X})가 있습니다. 해당 문자를 바꿔 주세요.")
    return value


def _width(ch, size):
    return pdfmetrics.stringWidth(ch, FONT, size)


def _take(value, start, width, size):
    """Consume a printable line, preserving all content; newline ends a line."""
    end, occupied = start, 0
    while end < len(value) and value[end] != "\n":
        w = _width(value[end], size)
        if w + occupied > width:
            break
        occupied += w
        end += 1
    if end < len(value) and value[end] == "\n":
        return value[start:end], end + 1
    if end < len(value):
        split = value.rfind(" ", start, end)
        if split > start and split >= start + 0.65 * (end - start):
            end = split + 1
    return value[start:end], end


def _flow(value, boxes, label, max_size=10.5, min_size=7):
    # Never silently truncate or spill into the adjacent cell. For tiny table
    # rows a six-point two-line alternative can fit a short full company name.
    for size in dict.fromkeys((max_size, 10, 9.5, 9, 8.5, 8, 7.5, 7, 6.5, 6)):
        if size > max_size or size < min_size:
            continue
        pieces, pos = [], 0
        for left, right, top, bottom in boxes:
            if pos >= len(value):
                break
            h = bottom - top
            # embedded font's Hangul ink occupies -0.08em through +0.83em
            spacing = size * 1.12
            count = max(0, int((h - 0.94 * size + 1e-5) / spacing) + 1) if h >= .94 * size else 0
            rows = []
            for _ in range(count):
                if pos >= len(value):
                    break
                line, nxt = _take(value, pos, right - left, size)
                if nxt == pos:
                    break
                rows.append(line)
                pos = nxt
            if rows:
                baseline = (top + bottom) / 2 - spacing * (len(rows)-1)/2 + .35 * size
                pieces += [(left, baseline + i * spacing, line, size) for i, line in enumerate(rows)]
        if pos == len(value):
            return pieces
    raise ValueError(f"{label}: 원본 서식의 칸에 내용을 모두 넣을 수 없습니다. 기재 내용을 확인하거나 원본 서식으로 별도 작성해 주세요.")


def _compose(src, pieces, expected_sha256=None):
    if expected_sha256 and hashlib.sha256(src).hexdigest() != expected_sha256:
        raise ValueError("원본 PDF의 판본이 다릅니다. 이 좌표에 맞는 배포본의 원본 서식을 사용해 주세요.")
    if not pieces:
        return src
    reader = PdfReader(io.BytesIO(src))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    for page_id, items in pieces.items():
        if page_id < 0 or page_id >= len(reader.pages):
            raise ValueError("원본 PDF의 쪽 수가 입력 좌표와 일치하지 않습니다.")
        p = reader.pages[page_id]
        width, height = float(p.mediabox.width), float(p.mediabox.height)
        stream = io.BytesIO()
        c = Canvas(stream, pagesize=(width, height), pageCompression=1, invariant=1)
        c.setFillColorRGB(.035, .105, .15)
        for left, baseline, line, size in items:
            c.setFont(FONT, size)
            c.drawString(left, height - baseline, line)
        c.save()
        writer.pages[page_id].merge_page(PdfReader(io.BytesIO(stream.getvalue())).pages[0])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def render_overlay(src: bytes, layout, *, expected_sha256: str | None = None) -> bytes:
    """Each entry: page index, left, right, top, bottom, value, Korean field label."""
    pieces = defaultdict(list)
    for page, left, right, top, bottom, value, label in layout:
        value = _clean(value, label)
        if value:
            box = (left, right, top, bottom)
            pieces[page].extend(_flow(value, [box], label, min_size=6 if bottom-top < 15 else 7))
    return _compose(src, pieces, expected_sha256)


# An entry is a series of consecutive writable boxes: left, right, top, bottom.
def boxes(*rects):
    return tuple(rects)


_F5_A = {
    "generator_name": boxes((108,316,149.9,163)),
    "generator_registration": boxes((328,536,149.9,163)),
    "generator_representative": boxes((108,316,176.9,190)),
    "generator_birthdate": boxes((328,536,176.9,190)),
    "generator_address": boxes((187,536,191.4,204.3),(108,354,204.5,217)),
    "generator_phone": boxes((411,529,204.3,216.8)),
    "waste_state_code": boxes((187,244,264.6,283.8)),
}
_F5_TRUSTEE = {
    "name": boxes((188,316,315,337.4)),
    "registration": boxes((415,536,315,337.4)),
    "representative": boxes((188,316,342,364.4)),
    "birthdate": boxes((415,536,342,364.4)),
    "address": boxes((188,536,367.7,380.4),(188,356,380.8,393.4)),
    "phone": boxes((411,530,380.6,393.4)),
    "industry": boxes((188,316,396.4,418.7)),
    "technical_capacity": boxes((415,536,396.4,418.7)),
    "permit_number": boxes((102,180,466.5,488.5)),
    "permit_date": boxes((187,406,466.5,488.5)),
    "permit_authority": boxes((414,537,466.5,488.5)),
    "permit_industry": boxes((101,124,593.9,615.3)),
    "permit_waste": boxes((128,181,593.9,615.3)),
    "vehicle_size": boxes((437,487,593.9,615.3)),
    "vehicle_count": boxes((491,538,593.9,615.3)),
    "acceptance": boxes((414,537,730.5,751.5)),
    "bond": boxes((300,470,500,516)),  # ⑮ 행 '공제조합가입( ), 보증보험가입( )' — 고른 쪽 괄호에 V를 찍는다
    "entrusted_annual_tons": boxes((252,316,264.6,283.8)),
}
_F5_C = {
    "method": boxes((186,245,593.9,615.3)),
    "facility_type": boxes((249,276,593.9,615.3)),
    "facility_capacity": boxes((280,339,593.9,615.3)),
}
_F6 = {
    "generator_name": boxes((116,336,185.9,197.1)),
    "generator_registration": boxes((348,530,185.9,197.1)),
    "generator_representative": boxes((116,336,211.2,222.5)),
    "generator_birthdate": boxes((348,530,211.2,222.5)),
    "generator_address": boxes((193,531,225,239.3),(115,208,240.2,251)),
    "generator_phone": boxes((467,526,239.9,251)),
    "generator_industry": boxes((152,531,255.2,276.7)),
    "raw_materials": boxes((113,338,292,302.5)),      # ⑦ 주 원료명 및 사용량 — 칸 제목 아래
    "main_products": boxes((347,531,292,302.5)),      # ⑧ 주 생산품명 및 생산량
    "process_steps": boxes((172,531,305,327.5)),      # ⑨ 제조공정
    "change_before": boxes((150,342,448,484)),        # ⑮ 변경사항 · 변경 전 (변경신고만)
    "change_after": boxes((346,531,448,484)),         # ⑮ 변경사항 · 변경 후
    "change_reason": boxes((150,531,488,516)),        # ⑯ 변경사유
    "waste_legal_name": boxes((92.4,119.2,401.5,414.3)),
    "waste_state_code": boxes((200,239,401.5,414.3)),
    "monthly_tons": boxes((242.5,272,401.5,414.3)),
    "annual_tons": boxes((275.2,303.2,401.5,414.3)),
    "hauler_name": boxes((306.2,342.8,401.5,414.3)),
    "haul_amount": boxes((345.6,382.2,401.5,414.3)),
    "treatment_type": boxes((385.1,423.5,401.5,414.3)),
    "receiver_name": boxes((426.3,456.4,401.5,414.3)),
    "treatment_method": boxes((459.3,494.9,401.5,414.3)),
    "treatment_amount": boxes((497.8,533.8,401.5,414.3)),
}
MAPS = {
    "form5_b": {**_F5_A, **{"hauler_"+k:v for k,v in _F5_TRUSTEE.items()}},
    "form5_c": {**_F5_A, **{"receiver_"+k:v for k,v in {**_F5_TRUSTEE, **_F5_C}.items()}},
    "form6": _F6,
}
OFFICIAL_KEYS = {doc: tuple(mapping) + ("waste_classification",) for doc,mapping in MAPS.items()}


def _source(doc):
    fname = doc.get("file") or (_SOURCE6 if doc["id"] == "form6" else _SOURCE5)
    src = (ROOT / fname).resolve()
    if not src.is_relative_to(ROOT) or src.suffix.lower() != ".pdf":
        raise ValueError("공식 원본 PDF 경로를 확인해 주세요.")
    try:
        return src.read_bytes()
    except OSError as e:
        raise ValueError("공식 원본 PDF 파일을 찾을 수 없습니다. templates 폴더를 확인해 주세요.") from e


# Extent of the printed classification slot in the pinned originals (the
# renderer places each digit or digit pair separately inside the slot).
_CODE_SLOT_5 = (108.7, 173.7, 264.6, 283.8)
_CODE_SLOT_6 = (131.2, 187.2, 401.5, 414.3)


def _official(doc, values, labels, *, regions=None):
    kind = doc["id"]
    is6 = kind == "form6"
    src = _source(doc)
    mapping = MAPS[kind]
    if regions is not None:
        for key, rects in mapping.items():
            regions.extend((key, 0, *rect) for rect in rects)
        regions.append(("waste_classification", 0, *(_CODE_SLOT_6 if is6 else _CODE_SLOT_5)))
    pieces = defaultdict(list)
    for key, value in values.items():
        label = labels[key]
        value = _clean(value, label)
        if not value:
            continue
        if key == "waste_classification":
            if not re.fullmatch(r"[0-9]{2}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError(f"{label}: 확인된 분류번호를 XX-XX-XX 형식으로 입력해 주세요.")
            if is6:
                for x, digit in zip([135.18,143.1,155.1,163.14,175.14,183.18],value.replace("-","")):
                    pieces[0].append((x-_width(digit,6.7)/2,409.76,digit,6.7))
            else:
                for x,pair in zip([121.5,141.5,161.5],value.split("-")):
                    pieces[0].append((x-_width(pair,9)/2,277.1,pair,9))
            continue
        if key not in mapping:
            raise ValueError(f"{label}: 이 원본 서식에 연결되지 않은 입력 항목입니다.")
        if key == "waste_state_code" and value not in ({"1","2"} if is6 else {"1","2","기타"}):
            allowed = "1 또는 2" if is6 else "1, 2 또는 기타"
            raise ValueError(f"{label}: 확인된 성질·상태 코드({allowed})를 입력해 주세요.")
        if key in {"treatment_method","receiver_method"} and (not re.search(r"[가-힣]",value) or not re.search(r"(?<![0-9])[0-9]{4}(?![0-9])",value)):
            raise ValueError(f"{label}: 확인된 처리분류와 네 자리 세분류 번호를 함께 입력해 주세요.")
        if key.endswith("_bond"):
            mark = {"공제조합가입": 372.9, "보증보험가입": 449.4}.get(value)  # 원본 괄호 '( )' 안쪽 가운데
            if mark is None:
                raise ValueError(f"{label}: '공제조합가입' 또는 '보증보험가입' 중 하나를 고르세요.")
            pieces[0].append((mark - _width("V", 7) / 2, 510.3, "V", 7))
            continue
        if key.endswith("_acceptance") and value not in {"가능","불가능"}:
            raise ValueError(f"{label}: 수탁자가 확인한 결과를 '가능' 또는 '불가능'으로 입력해 주세요.")
        small = is6 and mapping[key][0][2] > 400
        pieces[0].extend(_flow(value,mapping[key],label,min_size=6 if small else 7))
    return _compose(src,pieces,_HASH6 if is6 else _HASH5)

# Custom forms are working documents. No price, date, recipient or consent is
# supplied by this renderer. A blank cell is visible and can be completed later.
_NOTES = {
    "rfq": ("견적 회신 요청", "아래 배출·운반 조건을 확인한 후 단가, 포함 비용, 유효기간 및 가능한 일정을 회신해 주세요."),
    "settlement": ("실적·정산 대조", "해당 건의 계량표, 인계·인수 기록 및 계약 단가와 수량·금액이 일치하는지 확인하세요."),
    "process_note": ("작성 확인", "폐기물이 생기는 공정과 투입 원료, 현재 성상을 설명합니다. 분석 의뢰서와 분류 질의서에 첨부합니다."),
    "analysis_request": ("의뢰 확인", "아래 폐기물의 성분 분석을 의뢰합니다. 기관 고유 의뢰서가 있으면 이 내용을 옮겨 적고 시료와 함께 제출합니다."),
    "sampling_record": ("채취 확인", "폐기물공정시험기준에 따라 채취한 시료의 기록입니다. 채취자와 입회자가 내용을 확인합니다."),
    "classify_query": ("질의 회신 요청", "아래 폐기물의 분류번호 적용에 관하여 관할 기관의 판단을 요청합니다. 첨부 자료를 함께 검토해 주시기 바랍니다."),
}


def _wrap(text, width, size):
    if not text:
        return [""]
    pos, lines = 0, []
    while pos < len(text):
        line, nxt = _take(text, pos, width, size)
        if nxt <= pos:
            raise ValueError("인쇄 영역 안에 글자를 배치할 수 없습니다.")
        lines.append(line)
        pos = nxt
    return lines


def _draft(doc, values, labels, *, regions=None):
    _font()
    title = _clean(doc["title"], "서식 제목")
    ident = doc["id"]
    purpose, default_desc = _NOTES.get(ident, ("기록 확인", "확인한 내용을 기재하고 관련 증빙과 대조해 주세요."))
    description = _clean(default_desc if ident in _NOTES else (doc.get("description") or default_desc), "서식 안내")
    paper = io.BytesIO()
    c = Canvas(paper, pagesize=A4, pageCompression=1, invariant=1)
    c.setTitle(title)
    c.setAuthor("폐기물 업무 도우미")
    c.setSubject("검토용 업무 서식")
    W, H = A4
    L, R = 44, W-44
    start_field, value_x = L+12, L+177
    value_width, label_width = R-value_x-12, value_x-start_field-20
    note = colors.HexColor("#476A68")
    ink = colors.HexColor("#182E32")
    soft = colors.HexColor("#52736E")
    linecol = colors.HexColor("#B5C7C3")
    fill = colors.HexColor("#EDF4F1")
    page = 0
    y = 0

    def text_at(x, top, txt, size, color=ink):
        c.setFillColor(color)
        c.setFont(FONT, size)
        c.drawString(x,H-top,txt)

    def line_at(top, color=linecol):
        c.setStrokeColor(color)
        c.setLineWidth(.55)
        c.line(L,H-top,R,H-top)

    def footer():
        line_at(H-42)
        text_at(L,H-27,"폐기물 업무 도우미  /  업무 검토본",8,soft)
        c.setFont(FONT,8)
        c.setFillColor(soft)
        c.drawRightString(R,H-(H-27),str(page))

    def begin():
        nonlocal page,y
        if page:
            footer()
            c.showPage()
        page += 1
        c.setFillColor(soft)
        c.rect(L,H-43,25,3,fill=1,stroke=0)
        text_at(L+33,44,"폐기물 업무 서식",9,soft)
        badge = "검토용 초안"
        bw = _width(badge,8.5)+22
        c.setFillColor(fill)
        c.roundRect(R-bw,H-49,bw,18,5,fill=1,stroke=0)
        text_at(R-bw+11,43,badge,8.5,soft)
        y = 80 if page==1 else 72
        size = 23 if page==1 else 15
        for text in _wrap(title,R-L,size):
            text_at(L,y,text,size)
            y += size+6
        if page==1:
            y += 1
            for text in _wrap(description,R-L,10.5):
                text_at(L,y,text,10.5,soft)
                y += 16
        y += 9
        c.setFillColor(fill)
        c.roundRect(L,H-y-29,R-L,29,4,fill=1,stroke=0)
        text_at(start_field,y+18,"항목",9,soft)
        text_at(value_x,y+18,"기재 내용",9,soft)
        y += 29

    begin()
    bottom = H-68
    for key,label in labels.items():
        label = _clean(label, "항목 이름")
        value = _clean(values.get(key,""),label)
        value_lines = _wrap(value or "미입력", value_width, 10.6)
        label_lines = _wrap(label, label_width, 9.8)
        wrapped = max(len(value_lines),len(label_lines))
        offset = 0
        while offset < wrapped:
            available = int((bottom-y-16)/15)
            if available < 1 or (offset==0 and wrapped<=4 and wrapped>available):
                begin()
                available = int((bottom-y-16)/15)
            n = min(available,wrapped-offset)
            for i,txt in enumerate(label_lines[offset:offset+n]):
                text_at(start_field,y+18+i*15,txt,9.8,note)
            if offset and offset>=len(label_lines):
                text_at(start_field,y+18,"(계속)",8.5,note)
            for i,txt in enumerate(value_lines[offset:offset+n]):
                text_at(value_x,y+18+i*15,txt,10.6,ink if value else soft)
            if regions is not None:
                regions.append((key, page-1, value_x-5, R, y, y+16+n*15))
            y += 16+n*15
            line_at(y)
            offset += n
            if offset < wrapped:
                begin()
    y += 20
    if y+62>bottom:
        # A short review block may move to a fresh page if this one is full.
        # Avoid using an empty, misleading table header on this last page.
        footer()
        c.showPage()
        page += 1
        y = 64
        text_at(L,44,title,11,soft)
        line_at(51)
    text_at(L,y+1,purpose,11,ink)
    text_at(L,y+21,"검토자",9,soft)
    c.setStrokeColor(linecol)
    c.line(L+35,H-y-23,L+170,H-y-23)
    text_at(L+200,y+21,"확인일",9,soft)
    c.line(L+237,H-y-23,L+375,H-y-23)
    footer()
    c.save()
    return paper.getvalue()


def _contract(values, labels, *, regions=None):
    """A written agreement for the selected parties; every business term is supplied by the user."""
    from .contract_regions import value_paragraph
    from xml.sax.saxutils import escape
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _font()
    title = "폐기물 수집·운반 및 처리 위·수탁계약서"
    paper = io.BytesIO()
    width, height = A4
    margin = 43
    usable = width - 2 * margin - 12
    ink = colors.HexColor("#192E32")
    soft = colors.HexColor("#526C68")
    rule = colors.HexColor("#CBD8D3")
    pale = colors.HexColor("#F0F5F2")
    styles = {
        "title": ParagraphStyle("ContractTitle", fontName=FONT, fontSize=21, leading=28, textColor=ink,
                                wordWrap="CJK", spaceAfter=11),
        "section": ParagraphStyle("ContractSection", fontName=FONT, fontSize=11.5, leading=16, textColor=ink,
                                  spaceBefore=7, spaceAfter=3, keepWithNext=True),
        "label": ParagraphStyle("ContractLabel", fontName=FONT, fontSize=9.2, leading=13, textColor=soft, wordWrap="CJK"),
        "value": ParagraphStyle("ContractValue", fontName=FONT, fontSize=10.4, leading=14, textColor=ink, wordWrap="CJK"),
        "note": ParagraphStyle("ContractNote", fontName=FONT, fontSize=9.8, leading=15, textColor=ink, wordWrap="CJK"),
        "date": ParagraphStyle("ContractDate", fontName=FONT, fontSize=10, leading=16, textColor=ink, alignment=TA_CENTER),
    }
    cleaned = {key: _clean(values.get(key, ""), label) for key, label in labels.items()}
    consumed = set()
    story = []

    def para(value, style):
        return Paragraph(escape(value).replace("\n", "<br/>"), styles[style])

    def section(number, heading, rows):
        if not rows:
            return
        story.append(para(f"제{number}조  {heading}", "section"))
        data = [[para(label, "label"), value_paragraph(parts, styles["value"], regions, height)]
                for label, parts in rows]
        grid = Table(data, colWidths=[151, usable-151], hAlign="LEFT", splitByRow=1, splitInRow=1)
        grid.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 7), ("RIGHTPADDING", (0,0), (-1,-1), 7),
            ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ("BACKGROUND", (0,0), (0,-1), pale),
            ("LINEBELOW", (0,0), (-1,-1), .35, rule),
            ("LINEABOVE", (0,0), (-1,0), .5, rule),
        ]))
        story.append(grid)

    def entries(keys):
        consumed.update(keys)
        return [(labels[key], [(key, cleaned[key] or "미기재")]) for key in keys if key in labels]

    def identity(text):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()

    carrier = identity(cleaned.get("hauler_name", ""))
    other_names = {identity(cleaned.get(f"{role}_name", "")) for role in ("generator", "receiver")}
    # Explicit non-company answers still appear in the party table verbatim.
    # Only another named party gets a separate carrier signature line.
    self_or_unspecified = {"없음", "해당없음", "미정", "미선정", "미확정", "해당사항없음",
                           "자가", "자가운반", "직접운반", "자체운반"}
    distinct_carrier = carrier and carrier not in other_names and carrier not in self_or_unspecified
    carrier_label = "수탁자 B · 수집·운반" if distinct_carrier else "수집·운반 담당 (해당 시)"
    story.append(para(title, "title"))
    party_rows = []
    for prefix, role in [("generator", "위탁자 A · 배출"), ("hauler", carrier_label), ("receiver", "수탁자 C · 처리")]:
        name_key = f"{prefix}_name"
        name = cleaned.get(name_key) or "미기재"
        consumed.add(name_key)
        parts = [(name_key, name)]
        for suffix in ("registration", "representative", "address", "phone"):
            key = f"{prefix}_{suffix}"
            consumed.add(key)
            if key in labels:  # 비어 있어도 줄을 둬서 미리보기에서 그 칸을 눌러 고를 수 있게 한다
                parts.append((key, f"{labels[key]}  {cleaned[key] or '미기재'}"))
        party_rows.append((role, parts))
    section(1, "계약 당사자", party_rows)
    section(2, "위탁 대상·범위 및 기간", entries([
        "waste_name", "waste_legal_name", "waste_classification", "waste_state", "contract_quantity", "contract_period",
        "pickup_location", "contract_destination", "treatment_method",
    ]))
    section(3, "운반·처리 대금 및 지급", entries(["transport_price", "processing_price", "payer"]))
    handling = entries(["precautions", "rejection_terms"])
    handling.extend(entries([key for key in labels if key not in consumed]))
    section(4, "취급 주의사항 및 특약", handling)

    signers = ["위탁자 A (배출업체)"]
    if distinct_carrier:
        signers.append("수탁자 B (별도 수집·운반업체)")
    signers.append("수탁자 C (처리업체)")
    signatures = Table([[para(role, "label"), para("대표자", "label"), "", para("(서명 또는 인)", "label")]
                        for role in signers],
                       colWidths=[176,42,usable-300,82], rowHeights=26, hAlign="LEFT")
    signatures.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("LINEBELOW", (2,0), (2,-1), .45, rule),
    ]))
    agreement = "당사자는 위에 기재한 폐기물의 수집·운반 및 처리 조건에 합의하며, 아래에 서명 또는 날인하여 이 위·수탁계약을 체결한다."
    story.append(KeepTogether([
        Spacer(1, 10), para(agreement, "note"), Spacer(1, 8),
        para("계약 체결일    __________ 년    ______ 월    ______ 일", "date"), Spacer(1, 2), signatures,
    ]))

    def furniture(canvas, template):
        canvas.setFont(FONT, 9)
        canvas.setFillColor(soft)
        canvas.drawString(margin+6, height-35, "폐기물 업무 서식")
        canvas.drawRightString(width-margin-6, height-35, "검토용 초안")
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(.5)
        canvas.line(margin+6, height-43, width-margin-6, height-43)
        canvas.line(margin+6, 37, width-margin-6, 37)
        canvas.setFont(FONT, 8)
        canvas.drawString(margin+6, 24, f"{title}  /  검토용 초안")
        canvas.drawRightString(width-margin-6, 24, str(template.page))

    document = SimpleDocTemplate(paper, pagesize=A4, leftMargin=margin, rightMargin=margin,
                                 topMargin=47, bottomMargin=43, title=title+" (검토용 초안)",
                                 author="폐기물 업무 도우미", invariant=1)
    document.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return paper.getvalue()


def render_document(doc: dict, values: dict, *, regions=None) -> bytes:
    """Produce final PDF bytes for both preview and download; no temporary files."""
    if not isinstance(doc,dict) or not isinstance(values,dict):
        raise ValueError("문서와 작성 항목을 확인해 주세요.")
    labels = {f["key"]:f["label"] for f in doc["fields"]}
    selected = {key:values[key] for key in labels if key in values}
    kind = doc["id"]
    if kind in MAPS:
        return _official(doc,selected,labels,regions=regions)
    if kind == "intake":
        from .intake_pdf import render_intake
        return render_intake(doc,selected,regions=regions)
    if kind in {"assessment","approval","cplan","dplan"}:
        from .extra_pdf import render_extra
        return render_extra(kind,selected,regions=regions)
    if not labels or doc.get("kind") in {"reference","official","source","original"}:
        # Source-only forms are served directly; never manufacture an editable
        # lookalike under an official title.
        if not doc.get("file") or selected:
            raise ValueError("이 문서는 원본 서식으로 열어 직접 작성해 주세요.")
        return _source(doc)
    if kind == "contract":
        return _contract(selected, labels, regions=regions)
    return _draft(doc,selected,labels,regions=regions)


def render_with_regions(doc, values):
    """Pair one rendered draft with rectangles relative to each displayed page.

    Tagged layouts are drawn in an unrotated, uncropped, top-left coordinate
    system. If a future template uses a different view, the field picker stays
    usable without showing misleading rectangles.
    """
    raw = []
    pdf = render_document(doc, values, regions=raw)
    pages = PdfReader(io.BytesIO(pdf)).pages
    sizes = {}
    for index, page in enumerate(pages):
        box = page.mediabox
        if (page.rotation % 360 == 0 and page.cropbox == box and
                box.left == 0 and box.bottom == 0 and page.user_unit == 1):
            sizes[index] = float(box.width), float(box.height)
    regions = []
    for key, index, left, right, top, bottom, *choice in raw:
        if index not in sizes:
            continue
        width, height = sizes[index]
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            continue
        region = dict(key=key, page=index, x=left/width, y=top/height,
                      width=(right-left)/width, height=(bottom-top)/height)
        if choice:
            region["choice"] = choice[0]
        regions.append(region)
    return pdf, regions
