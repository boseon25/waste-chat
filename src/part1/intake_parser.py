"""DOCX -> rows (not JSON). Restricted to the tagged intake template family."""
import hashlib
import io
import re
import uuid
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
from normalization import normalize_quantity, hazard_response

VERSION = 'intake-db-0.2.0'
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'w14': 'http://schemas.microsoft.com/office/word/2010/wordml'}
W = '{' + NS['w'] + '}'
W14 = '{' + NS['w14'] + '}'


def uid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def txt(node):
    return ''.join(x.text or '' for x in node.findall('.//w:t', NS))


def parse_document(blob):
    if len(blob) > 30 * 1024 * 1024:
        raise ValueError('DOCX exceeds 30 MiB')
    try:
        with ZipFile(io.BytesIO(blob)) as z:
            meta = z.getinfo('word/document.xml')
            if meta.file_size > 8 * 1024 * 1024:
                raise ValueError('XML exceeds 8 MiB')
            xml = z.read(meta)
    except (BadZipFile, KeyError, RuntimeError) as exc:
        raise ValueError('손상·암호화·미지원 DOCX') from exc
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise ValueError('DTD/entity not supported')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError('DOCX XML 손상') from exc
    parents = {child: parent for parent in root.iter() for child in parent}
    positions, cells = {}, []
    for ti, table in enumerate(root.findall('./w:body/w:tbl', NS)):
        for ri, row in enumerate(table.findall('./w:tr', NS)):
            for ci, cell in enumerate(row.findall('./w:tc', NS)):
                positions[cell] = (ti, ri, ci)
                cells.append(dict(table_index=ti, row_index=ri, cell_index=ci, raw_value=txt(cell)))
    paragraphs = [dict(paragraph_index=i, raw_value=txt(p))
                  for i, p in enumerate(root.findall('./w:body/w:p', NS))]
    p_positions = {p: i for i, p in enumerate(root.findall('./w:body/w:p', NS))}
    controls = []
    for ordinal, s in enumerate(root.findall('.//w:sdt', NS)):
        node = s
        pos, pi = (None, None, None), None
        while node in parents:
            node = parents[node]
            if node in positions:
                pos = positions[node]
                break
            if node in p_positions:
                pi = p_positions[node]
                break
        tag = s.find('./w:sdtPr/w:tag', NS)
        tag = tag.get(W + 'val', '') if tag is not None else ''
        content = s.find('./w:sdtContent', NS)
        raw = txt(content) if content is not None else ''
        checkbox = s.find('./w:sdtPr/w14:checkbox', NS)
        check_node = s.find('./w:sdtPr/w14:checkbox/w14:checked', NS)
        check_raw = check_node.get(W14 + 'val') if check_node is not None else None
        checked = (1 if check_raw in ('1', 'true', 'on') else 0 if check_raw in ('0', 'false', 'off') else None)
        placeholder = s.find('./w:sdtPr/w:showingPlcHdr', NS) is not None or bool(re.match(r'^예\s*[:：]', raw.strip()))
        real = checked == 1 or (checkbox is None and bool(raw.strip()) and not placeholder)
        controls.append(dict(ordinal=ordinal, tag=tag, raw_value=raw, checked=checked,
            is_checkbox=int(checkbox is not None), is_placeholder=int(placeholder), is_real=int(real),
            table_index=pos[0], row_index=pos[1], cell_index=pos[2], paragraph_index=pi))
    if not any(c['tag'] == 'company_name' for c in controls) or not any(c['tag'].startswith('waste_') for c in controls):
        raise ValueError('지원 대상 입력서의 콘텐츠 컨트롤을 찾지 못했습니다. 임의 DOCX는 이 로컬 경로에서 지원하지 않습니다.')
    return dict(controls=controls, cells=cells, paragraphs=paragraphs)


def one(controls, tag=None, suffix=None):
    selected = [c for c in controls if (c['tag'] == tag if tag is not None else re.search(r'^waste_\d+_' + re.escape(suffix) + '$', c['tag']))]
    return selected[0] if len(selected) == 1 else None


def answer(control):
    return control['raw_value'].strip() if control and control['is_real'] and not control['is_checkbox'] else None


def derive_rows(parsed, submission_id):
    controls = parsed['controls']
    get = lambda tag: answer(one(controls, tag=tag))
    company_id = uid(submission_id + ':company')
    company = dict(company_id=company_id, name=get('company_name'), business_number=get('business_number'),
                   contact_name=get('contact_name'), contact_phone=get('contact_phone'))
    sites = []
    common = get('site_address')
    common_id = uid(submission_id + ':site:common') if common else None
    if common:
        sites.append(dict(site_id=common_id, company_id=company_id, address=common,
                          source_ordinal=one(controls, tag='site_address')['ordinal']))
    case_rows = []
    waste_tables = sorted({c['table_index'] for c in parsed['cells']
                           if c['cell_index'] == 0 and c['raw_value'].strip() == '폐기물 이름'})
    for ti in waste_tables:
        local = [c for c in controls if c['table_index'] == ti and c['tag'].startswith('waste_')]
        if not any(c['is_real'] for c in local):
            continue
        cid = uid(submission_id + f':case:{ti}')
        val = lambda suffix: answer(one(local, suffix=suffix))
        warnings = []
        tags = [c['tag'] for c in local]
        if len(set(tags)) != len(tags):
            warnings.append('DUPLICATE_TAG_WITHIN_TABLE_REVIEW')
        site_value = val('site')
        site_id = common_id
        inherited = int(not site_value and common_id is not None)
        if site_value:
            site_control = one(local, suffix='site')
            site_id = uid(submission_id + f':site:{ti}')
            sites.append(dict(site_id=site_id, company_id=company_id, address=site_value,
                              source_ordinal=site_control['ordinal']))
        raw_q = val('quantity')
        q_value = q_unit = q_dimension = q_rule = None
        if raw_q:
            try:
                q = normalize_quantity(raw_q, 'source-placeholder')
                q_value = str(q.value) if q.value is not None else None
                q_unit, q_dimension, q_rule = q.unit, q.dimension, q.conversion_rule
            except ValueError:
                warnings.append('QUANTITY_NEEDS_REVIEW')
        def choice(group, labels):
            group_controls = [one(local, suffix=f'{group}_{i}') for i in range(len(labels))]
            checked = [i for i, c in enumerate(group_controls) if c and c['checked'] == 1]
            if len(checked) != 1 or any(c and c['is_checkbox'] and c['checked'] is None for c in group_controls):
                warnings.append(group.upper() + '_UNCHECKED_OR_MULTIPLE')
                return None
            return labels[checked[0]]
        mix = choice('mixture', ['single', 'multiple_unspecified', None])
        contamination = choice('contamination', ['not_visible', 'partial', 'widespread', None])
        hazard_raw = choice('hazard', ['있음', '없음', '모름'])
        hazard = hazard_response([hazard_raw] if hazard_raw else [])['value']
        case_rows.append(dict(case_id=cid, company_id=company_id, site_id=site_id, table_index=ti,
            lot_id=uid(cid + ':lot'), name=val('name'), process_description=val('process'), physical_state=val('appearance'),
            quantity_raw=raw_q, quantity_value=q_value, quantity_unit=q_unit, quantity_dimension=q_dimension,
            conversion_rule=q_rule, mixture_type=mix, visible_contamination=contamination,
            hazard_declaration=None if hazard is None else int(hazard), hazard_raw=hazard_raw,
            site_inherited=inherited, warnings='\n'.join(warnings)))
    return dict(companies=[company], sites=sites, case_candidates=case_rows)
