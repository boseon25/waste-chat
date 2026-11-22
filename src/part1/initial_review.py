"""Phase 1: read-only preview then explicit per-case initial review record.
Local demo only: reviewer name is self-reported, not authenticated approval.
"""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from db_store import connect, get_submission, load_rows, verify_connection
from db_followup import table_exists

CREATE_REVIEWS = '''CREATE TABLE IF NOT EXISTS initial_reviews (
 review_id INTEGER PRIMARY KEY AUTOINCREMENT,
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
 case_id TEXT NOT NULL REFERENCES case_candidates(case_id),
 review_hash TEXT NOT NULL, reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL,
 unresolved_ack INTEGER NOT NULL CHECK(unresolved_ack IN (0,1)),
 UNIQUE(submission_id,case_id,review_hash,reviewer)
)'''


def copy_template(out):
    source = Path(__file__).parent / 'fixtures' / 'blank_intake_template.docx'
    target = Path(out)
    if target.suffix.lower() != '.docx':
        raise ValueError('출력 파일명은 .docx 여야 합니다.')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects the user's already edited template and original.
    with source.open('rb') as src, target.open('xb') as dst:
        dst.write(src.read())
    return target


def preview_from_connection(conn, case_id, sid=None):
    report = verify_connection(conn, sid)
    sub = get_submission(conn, report['submission_id'])
    rows = load_rows(conn, report['submission_id'])
    case = next((c for c in rows['case_candidates'] if c['case_id'] == case_id), None)
    if case is None:
        raise ValueError('현재 제출에서 폐기물 ID를 찾을 수 없습니다.')
    site = next((s for s in rows['sites'] if s['site_id'] == case['site_id']), None)
    company = next((c for c in rows['companies'] if c['company_id'] == case['company_id']), None)
    if not company:
        raise ValueError('해당 건의 업체 정보를 찾을 수 없습니다.')
    # Include whole document controls. Date, company and both sites are all traceable.
    sources = [c for c in rows['controls'] if c['table_index'] in (0, case['table_index'])
               or c['paragraph_index'] is not None]
    warnings = []
    for k, label in [('name','업체명'),('business_number','사업자등록번호'),
                     ('contact_name','담당자 성명'),('contact_phone','담당자 연락처')]:
        if not company.get(k):
            warnings.append('업체 필수값 미확인: '+label)
    for k,label in [('name','폐기물 이름'),('process_description','나온 작업'),
                    ('physical_state','형태'),('quantity_value','이번 처리량'),('site_id','배출 현장')]:
        if case.get(k) is None:
            warnings.append('폐기물 필수값 미확인: '+label)
    warnings.extend(x for x in case['warnings'].splitlines() if x)
    if case['hazard_declaration'] is None:
        warnings.append('유해물질 사용자 응답 미확인 또는 모름')
    warnings.append('컨트롤 밖 텍스트는 정규화에 자동 반영되지 않을 수 있어 아래 셀 원문을 대조해야 함')
    warnings.append('사진 파일의 실제 첨부·건 연결·전체/근접은 검증되지 않음')
    warnings.append('실제 수량 측정일은 별도 보완 질문으로 확인 필요')
    source_cells = [cell for cell in rows['cells'] if cell['table_index'] in (0, case['table_index'])]
    payload = dict(submission_id=sub['submission_id'], sha256=sub['sha256'],
                   company=company, site=site, case=case, source_controls=sources,
                   source_cells=source_cells, source_paragraphs=rows['paragraphs'], warnings=warnings)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    payload['review_hash'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return payload


def preview(db, case_id, sid=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        return preview_from_connection(conn, case_id, sid)


def format_preview(p):
    co, c, site = p['company'], p['case'], p['site']
    lines = [f"제출: {p['submission_id']} / 폐기물: {c['case_id']}",
             f"원본 SHA-256: {p['sha256']}",
             f"업체: {co['name']} / 사업자번호: {co['business_number']}",
             f"담당자: {co['contact_name']} / 연락처: {co['contact_phone']}",
             f"현장: {site['address'] if site else '미확인'} / 공통 현장 상속: {'예' if c['site_inherited'] else '아니요'}",
             f"폐기물: {c['name']} / 작업: {c['process_description']} / 형태: {c['physical_state']}",
             f"수량 원문: {c['quantity_raw']} / 후보: {c['quantity_value']} {c['quantity_unit']} ({c['quantity_dimension']})",
             f"혼합: {c['mixture_type']} / 겉 오염: {c['visible_contamination']} / 유해물질 응답: {c['hazard_raw']}",
             '', '원문 컨트롤 (위치·태그·표시값·체크 상태):']
    for control in p['source_controls']:
        loc = f"표 {control['table_index']}, 행 {control['row_index']}, 셀 {control['cell_index']}" if control['table_index'] is not None else f"문단 {control['paragraph_index']}"
        check = '선택' if control['checked'] == 1 else '미선택' if control['checked'] == 0 else '-'
        placeholder = ' [예시/placeholder]' if control['is_placeholder'] else ''
        lines.append(f"  [{loc}] {control['tag']}: {control['raw_value']!r} / {check}{placeholder}")
    lines += ['', '셀 원문 (컨트롤 밖 텍스트 포함):']
    for cell in p['source_cells']:
        lines.append(f"  [표 {cell['table_index']}, 행 {cell['row_index']}, 셀 {cell['cell_index']}] {cell['raw_value']!r}")
    lines += ['', '문단 원문:']
    for paragraph in p['source_paragraphs']:
        lines.append(f"  [문단 {paragraph['paragraph_index']}] {paragraph['raw_value']!r}")
    lines += ['', '미확인/제한:'] + ['  - '+w for w in p['warnings']]
    lines += ['', f"검토 해시: {p['review_hash']}",
              '검토 확인은 입력 내용과 제한을 살폈다는 기록일 뿐, 사실·사진·법적 판정·발행 승인이 아닙니다.']
    return '\n'.join(lines)


def confirm(db, case_id, reviewer, expected_hash, acknowledge, sid=None):
    reviewer = reviewer.strip()
    if not reviewer or len(reviewer) > 100:
        raise ValueError('검토자 이름/식별자(1~100자)를 입력하세요.')
    if not acknowledge:
        raise ValueError('미확인·제한을 확인했다는 --acknowledge-limitations가 필요합니다.')
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        p = preview_from_connection(conn, case_id, sid)
        if expected_hash != p['review_hash']:
            raise ValueError('검토 해시가 다릅니다. initial-preview로 현재 원문을 다시 살펴보세요.')
        conn.execute(CREATE_REVIEWS)
        cursor = conn.execute('INSERT INTO initial_reviews (submission_id,case_id,review_hash,reviewer,reviewed_at,unresolved_ack) '
            'VALUES (?,?,?,?,?,1) ON CONFLICT(submission_id,case_id,review_hash,reviewer) DO NOTHING',
            (p['submission_id'],case_id,expected_hash,reviewer,datetime.now(timezone.utc).isoformat()))
        return bool(cursor.rowcount)


def history(db, case_id, sid=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        p = preview_from_connection(conn, case_id, sid)
        if not table_exists(conn, 'initial_reviews'):
            return p, []
        rows = conn.execute('SELECT review_id,review_hash,reviewer,reviewed_at,unresolved_ack '
                            'FROM initial_reviews WHERE submission_id=? AND case_id=? ORDER BY review_id',
                            (p['submission_id'],case_id)).fetchall()
        return p, [dict(row, matches_current=(row['review_hash'] == p['review_hash'])) for row in rows]
