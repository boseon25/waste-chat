"""Build the editable tagged intake DOCX and its fictional example."""
from pathlib import Path
from itertools import count

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from lxml import etree

OUT = Path(__file__).resolve().parent / 'fixtures'
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'

COMMON = {
    'company_name': '가상순환산업', 'business_number': '000-00-00000',
    'contact_name': '김예시', 'contact_phone': '010-0000-0000',
    'site_address': '전주시 가상산업로 10', 'source_form_date': '2026-11-10',
}
EXAMPLES = (
    {'name': '제품 포장용 PE 필름 자투리', 'site': '전주시 가상산업로 10 A동 자재창고',
     'process': '상품 포장 공정에서 남는 필름을 절단하고 수거함', 'appearance': '마른 투명 필름 조각',
     'quantity': '200 kg', 'quantity_evidence': '저울로 용기 무게를 제외하여 측정함',
     'mixture': 1, 'contamination': 0, 'hazard': 2, 'mixture_details': 'PE 포장재',
     'record_materials': '상품 포장용 필름 PF-01', 'record_materials_source': '원료 투입대장 A동 10월 12행',
     'record_label': 'PE', 'record_label_source': 'PF-01 포장재 라벨',
     'record_component1_name': 'PE 필름', 'record_component1_percent': '98',
     'record_component1_source': '10월 폐기물 선별 계량표 A동 3행',
     'record_component2_name': '종이 라벨 조각', 'record_component2_percent': '2',
     'record_component2_source': '10월 폐기물 선별 계량표 A동 4행',
     'record_month1_period': '2026-08', 'record_month1_quantity': '180 kg',
     'record_month1_source': 'A동 발생대장 8월 15행', 'record_month2_period': '2026-09',
     'record_month2_quantity': '205 kg', 'record_month2_source': 'A동 발생대장 9월 16행',
     'record_month3_period': '2026-10', 'record_month3_quantity': '200 kg',
     'record_month3_source': 'A동 발생대장 10월 17행', 'record_history': '이력 없음'},
    {'name': '기계 세척 후 남은 세척액', 'site': '전주시 가상산업로 10 B동 세척실',
     'process': '금속 가공 기계를 세척제로 씻은 뒤 액을 회수함', 'appearance': '회색 액체이며 표면에 기름막이 일부 보임',
     'quantity': '100 L', 'quantity_evidence': '눈금 용기로 내용물 부피를 측정함',
     'mixture': 2, 'contamination': 1, 'hazard': 2, 'record_chemicals': '세척제 CL-01',
     'record_chemicals_source': '공급사 CL-01 MSDS와 B동 투입기록',
     'record_month1_period': '2026-10', 'record_month1_quantity': '100 L',
     'record_month1_source': 'B동 발생대장 10월 7행', 'record_history': '처음 발생'},
)


def xml(parent, tag, **properties):
    node = OxmlElement(tag)
    for key, value in properties.items():
        node.set(qn('w:' + key), str(value))
    parent.append(node)
    return node


def control(paragraph, tag, ids, value=None, checkbox=None):
    sdt = xml(paragraph._p, 'w:sdt')
    prop = xml(sdt, 'w:sdtPr')
    xml(prop, 'w:alias', val=tag)
    xml(prop, 'w:tag', val=tag)
    xml(prop, 'w:id', val=next(ids))
    if checkbox is None:
        xml(prop, 'w:text')
    else:
        box = etree.SubElement(prop, '{' + W14 + '}checkbox')
        for key, val in [('checked', int(checkbox)), ('checkedState', '2612'), ('uncheckedState', '2610')]:
            node = etree.SubElement(box, '{' + W14 + '}' + key)
            node.set('{' + W14 + '}val', str(val))
            if key != 'checked':
                node.set('{' + W14 + '}font', 'Noto Sans CJK KR')
        value = '☒' if checkbox else '☐'
    content = xml(sdt, 'w:sdtContent')
    txt = xml(xml(content, 'w:r'), 'w:t')
    txt.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    txt.text = value if value else '　　　　'


def table(doc, widths):
    t = doc.add_table(rows=0, cols=len(widths))
    t.autofit = False
    for col, width in zip(t.columns, widths):
        col.width = Mm(width)
    p = t._tbl.tblPr
    borders = xml(p, 'w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        xml(borders, 'w:' + edge, val='single', sz=4, color='D9D9D9')
    margin = xml(p, 'w:tblCellMar')
    for edge in ('top', 'bottom', 'left', 'right'):
        xml(margin, 'w:' + edge, w=42 if edge in ('top', 'bottom') else 100, type='dxa')
    return t


def row(table):
    r = table.add_row()
    xml(r._tr.get_or_add_trPr(), 'w:cantSplit')
    for cell in r.cells:
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.paragraph_format.space_after = Pt(1)
        paragraph.paragraph_format.line_spacing = 1.0
    return r.cells


def label(cell, text):
    run = cell.paragraphs[0].add_run(text)
    run.bold = True
    xml(cell._tc.get_or_add_tcPr(), 'w:shd', fill='F1F3F5')


def field(t, title, tag, ids, val, source=None):
    cells = row(t)
    label(cells[0], title)
    value = cells[1].merge(cells[2]) if source is not None else cells[1].merge(cells[-1])
    control(value.paragraphs[0], tag, ids, val.get(tag))
    if source is not None:
        control(cells[3].paragraphs[0], source, ids, val.get(source))


def configure(doc):
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(17)
    sec.top_margin = Mm(16)
    sec.bottom_margin = Mm(15)
    for name, size in [('Normal', 9.5), ('Title', 22), ('Heading 1', 16), ('Heading 2', 11)]:
        sty = doc.styles[name]
        sty.font.name = 'Noto Sans CJK KR'
        sty.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Noto Sans CJK KR')
        sty.font.size = Pt(size)
        sty.font.color.rgb = RGBColor.from_string('000000')
        sty.paragraph_format.space_before = Pt(0 if name in ('Normal', 'Title') else 9)
        sty.paragraph_format.space_after = Pt(3 if name == 'Normal' else 6)
        sty.paragraph_format.line_spacing = 1.09
    title = doc.styles['Title'].element.get_or_add_pPr()
    border = title.find(qn('w:pBdr'))
    if border is not None:
        title.remove(border)
    nil_border = xml(title, 'w:pBdr')
    xml(nil_border, 'w:bottom', val='nil')


def build(sample):
    doc = Document()
    configure(doc)
    ids = count(500)
    doc.add_paragraph('폐기물 기초 정보 입력서', 'Title')
    doc.add_paragraph('가상 작성 예시' if sample else '사업장에서 발생한 폐기물의 기초 정보와 보유 기록을 작성하는 양식입니다.')
    doc.add_heading('사업장 정보', level=1)
    company = table(doc, (48, 58, 35, 35))
    for title, tag in [('업체명', 'company_name'), ('사업자등록번호', 'business_number'),
                       ('담당자', 'contact_name'), ('연락처', 'contact_phone'),
                       ('공통 배출 현장 주소', 'site_address'), ('작성 기준일 YYYY-MM-DD', 'source_form_date')]:
        field(company, title, tag, ids, COMMON if sample else {})
    doc.add_heading('작성 방법', level=1)
    for line in (
        '폐기물마다 한 표를 작성하세요. 실제 배출 현장이 공통 주소와 다르면 해당 폐기물 표에 주소와 세부 위치를 적습니다.',
        '표의 빈칸을 클릭하여 입력하고 해당하는 선택란을 표시합니다. 작성하지 않은 폐기물 표는 공란으로 둡니다.',
        '원료와 약품, 라벨 재질, 배합비는 구매·생산 자료에서 확인한 값과 기록명을 적습니다.',
        '폐기물의 구성비는 실제 선별·계량 또는 성분 분석으로 확인된 값만 적습니다.',
        '월별 발생량은 직전 완료된 월의 발생기록을 확인하여 월·양·단위를 적습니다. 최대 3개월을 적을 수 있습니다.',
        '원료·약품·라벨·배합비는 표의 맨 오른쪽 칸에 확인한 기록명과 위치를 적습니다.',
        '기존 이력은 있음, 처음 발생, 이력 없음, 모름 중 하나로 적습니다.',
        '신고증명서나 처리실적이 있는 경우에만 보유 자료를 적고, 접수 후 같은 폐기물 건에 첨부합니다.',
    ):
        doc.add_paragraph(line, 'List Bullet')
    for number in (1, 2):
        heading = doc.add_heading(f'폐기물 {number}', level=1)
        heading.paragraph_format.page_break_before = True
        vals = {f'waste_{number}_{key}': value for key, value in (EXAMPLES[number - 1] if sample else {}).items()}
        prefix = f'waste_{number}_'
        t = table(doc, (45, 48, 28, 55))
        for title, tag in [('폐기물 이름', 'name'), ('개별 배출 현장', 'site'), ('발생 작업', 'process'),
                           ('겉모습·성상', 'appearance'), ('이번 처리량과 단위', 'quantity'),
                           ('측정 방법', 'quantity_evidence')]:
            field(t, title, prefix + tag, ids, vals)
        for title, tag, options in [
            ('혼합 상태', 'mixture', ['단일 재질', '혼합', '모름']),
            ('눈에 보이는 오염', 'contamination', ['안 보임', '일부', '전체', '모름']),
            ('유해물질 포함 진술', 'hazard', ['있음', '없음', '모름'])]:
            cells = row(t)
            label(cells[0], title)
            paragraph = cells[1].merge(cells[-1]).paragraphs[0]
            for i, name in enumerate(options):
                control(paragraph, f'{prefix}{tag}_{i}', ids, checkbox=vals.get(prefix + tag) == i)
                paragraph.add_run(' ' + name + '     ')
        for title, tag in [('재질 메모', 'mixture_details'), ('보이는 오염 설명', 'contamination_details'),
                           ('유해물질 관련 메모', 'hazard_details'), ('기타·발생주기', 'notes'),
                           ('전체 사진 파일명', 'photo_full'), ('근접 사진 파일명', 'photo_close')]:
            field(t, title, prefix + tag, ids, vals)
        for title, key in [('사용 원료', 'materials'), ('사용 약품', 'chemicals'),
                           ('라벨상 재질', 'label'), ('투입 원료 배합비', 'feed_ratio')]:
            field(t, title, prefix + 'record_' + key, ids, vals, prefix + f'record_{key}_source')
        for heading, names in [('폐기물 구성비', ('재질·성분', '% 숫자', '기록명·위치')),
                               ('월별 발생량', ('연월 YYYY-MM', '수량과 단위', '기록명·위치'))]:
            headers = row(t)
            for c, title in zip(headers, (heading, *names)):
                label(c, title)
            component = heading == '폐기물 구성비'
            keys = ('name', 'percent', 'source') if component else ('period', 'quantity', 'source')
            base = 'component' if component else 'month'
            for i in range(1, 4):
                cr = row(t)
                cr[0].text = str(i)
                for cell, key in zip(cr[1:], keys):
                    tag = f'{prefix}record_{base}{i}_{key}'
                    control(cell.paragraphs[0], tag, ids, vals.get(tag))
        field(t, '기존 신고·처리 이력', prefix + 'record_history', ids, vals)
        field(t, '보유 문서명·번호 (선택)', prefix + 'record_history_reference', ids, vals)
    path = OUT / ('intake_records_example.docx' if sample else 'wastecase_intake_050.docx')
    doc.save(path)
    return path


if __name__ == '__main__':
    build(False)
    build(True)
