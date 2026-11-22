"""Read-only template inspection, not ingestion/publication. Positions are zero-based."""
import hashlib
import re
import uuid
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'w14': 'http://schemas.microsoft.com/office/word/2010/wordml'}
W = '{' + NS['w'] + '}'
W14 = '{' + NS['w14'] + '}'
EXTRACTOR_VERSION = 'docx-probe-0.1.1'


def text(element):
    return ''.join(n.text or '' for n in element.findall('.//w:t', NS))


def probe_docx(path):
    path = Path(path)
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError('DOCX size limit 30 MiB')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with ZipFile(path) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError('XML size limit 8 MiB')
            data = z.read(info)
    except (BadZipFile, KeyError, RuntimeError) as exc:
        raise ValueError('손상·암호화·미지원 DOCX: 대체 자료를 요청하세요.') from exc
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD/entity not supported')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError('DOCX XML 손상') from exc
    tables, company, seen = [], [], set()
    for ti, table in enumerate(root.findall('./w:body/w:tbl', NS)):
        controls, labels, untagged = [], [], []
        site = None
        for ri, row in enumerate(table.findall('./w:tr', NS)):
            cells = row.findall('./w:tc', NS)
            label = text(cells[0]).strip() if cells else ''
            labels.append(label)
            for ci, cell in enumerate(cells):
                sdts = cell.findall('.//w:sdt', NS)
                for oi, s in enumerate(sdts):
                    tag_node = s.find('./w:sdtPr/w:tag', NS)
                    tag = tag_node.get(W + 'val', '') if tag_node is not None else ''
                    content = s.find('./w:sdtContent', NS)
                    raw = text(content).strip() if content is not None else ''
                    checked_node = s.find('./w:sdtPr/w14:checkbox/w14:checked', NS)
                    checked_raw = checked_node.get(W14 + 'val') if checked_node is not None else None
                    checked = None if checked_raw is None else checked_raw in ('1', 'true', 'on')
                    placeholder = s.find('./w:sdtPr/w:showingPlcHdr', NS) is not None
                    example = bool(re.match(r'^예\s*[:：]', raw))
                    warnings = []
                    if tag in seen:
                        warnings.append('DUPLICATE_TAG_USE_POSITION')
                    seen.add(tag)
                    if checked_node is not None and checked_raw not in ('0', '1', 'true', 'false', 'on', 'off'):
                        warnings.append('INVALID_CHECK_STATE')
                    if placeholder and raw and not example:
                        warnings.append('PLACEHOLDER_FLAG_REQUIRES_REVIEW')
                    real = checked is True or (checked_node is None and bool(raw) and not placeholder and not example)
                    controls.append(dict(tag=tag, raw_value=raw, checked=checked,
                                         is_placeholder=placeholder or example, is_real_answer=real,
                                         source_locator=dict(kind='docx', table=ti, row=ri, cell=ci, tag=tag),
                                         control_occurrence=oi, warnings=warnings))
                if ci > 0 and not sdts:
                    raw = text(cell).strip()
                    if raw and raw != '공통 현장과 같을 경우 비워둠' and not re.match(r'^예\s*[:：]', raw):
                        item = dict(raw_value=raw, source_locator=dict(kind='docx', table=ti, row=ri, cell=ci))
                        if label == '배출 현장':
                            site = item
                        elif label in ('폐기물 이름', '나온 작업', '이번에 처리할 양', '형태'):
                            untagged.append(item)
        if '폐기물 이름' not in labels:
            company.extend(controls)
            continue
        active = any(c['is_real_answer'] for c in controls) or site is not None or bool(untagged)
        warnings = ['UNTAGGED_CONTENT_REQUIRES_REVIEW'] if untagged else []
        for group in ('mixture', 'contamination', 'hazard'):
            checks = [c for c in controls if re.search(rf'_{group}_[0-9]+$', c['tag']) and c['checked']]
            if active and len(checks) != 1:
                warnings.append(f'{group.upper()}_UNCHECKED_OR_MULTIPLE')
        if active:
            warnings.append('PARTIAL_OR_COMPLETE_CANDIDATE_REQUIRES_USER_REVIEW')
        tables.append(dict(table=ti, candidate_id=str(uuid.uuid4()) if active else None,
                           create_candidate=active, controls=controls, site_override_candidate=site,
                           untagged_candidates=untagged, warnings=warnings,
                           attachment_state='not_checked_filename_is_not_upload'))
    return dict(mode='read_only_probe', extractor_version=EXTRACTOR_VERSION, sha256=sha,
                company_controls=company, waste_tables=tables,
                case_candidate_count=sum(t['create_candidate'] for t in tables), accepted_case_count=0,
                limitations=['NO_DB_WRITE', 'NO_PUBLISH', 'NO_PHOTO_VALIDATION',
                             'ARBITRARY_LAYOUT_NOT_SUPPORTED', 'NO_IDEMPOTENT_CASE_PERSISTENCE'])
