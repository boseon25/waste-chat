"""SQLite persistence for local DOCX-first checks. No JSON payload columns."""
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from intake_parser import VERSION, parse_document, derive_rows, uid
import db_followup

SCHEMA_VERSION = 1
SCHEMA = '''
CREATE TABLE IF NOT EXISTS submissions (
 submission_id TEXT PRIMARY KEY, original_name TEXT NOT NULL, sha256 TEXT NOT NULL,
 parser_version TEXT NOT NULL, synthetic INTEGER NOT NULL CHECK(synthetic IN (0,1)),
 created_at TEXT NOT NULL, original_docx BLOB NOT NULL,
 UNIQUE(sha256,parser_version)
);
CREATE TABLE IF NOT EXISTS controls (
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id), ordinal INTEGER NOT NULL,
 tag TEXT NOT NULL, raw_value TEXT NOT NULL, checked INTEGER CHECK(checked IN (0,1)),
 is_checkbox INTEGER NOT NULL, is_placeholder INTEGER NOT NULL, is_real INTEGER NOT NULL,
 table_index INTEGER, row_index INTEGER, cell_index INTEGER, paragraph_index INTEGER,
 PRIMARY KEY(submission_id,ordinal)
);
CREATE TABLE IF NOT EXISTS cells (
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
 table_index INTEGER NOT NULL,row_index INTEGER NOT NULL,cell_index INTEGER NOT NULL,raw_value TEXT NOT NULL,
 PRIMARY KEY(submission_id,table_index,row_index,cell_index)
);
CREATE TABLE IF NOT EXISTS paragraphs (
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
 paragraph_index INTEGER NOT NULL,raw_value TEXT NOT NULL,
 PRIMARY KEY(submission_id,paragraph_index)
);
CREATE TABLE IF NOT EXISTS companies (
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id), company_id TEXT PRIMARY KEY,
 name TEXT, business_number TEXT, contact_name TEXT, contact_phone TEXT
);
CREATE TABLE IF NOT EXISTS sites (
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id), site_id TEXT PRIMARY KEY,
 company_id TEXT NOT NULL REFERENCES companies(company_id), address TEXT NOT NULL, source_ordinal INTEGER NOT NULL,
 FOREIGN KEY(submission_id, source_ordinal) REFERENCES controls(submission_id, ordinal)
);
CREATE TABLE IF NOT EXISTS case_candidates (
 submission_id TEXT NOT NULL REFERENCES submissions(submission_id), case_id TEXT PRIMARY KEY,
 company_id TEXT NOT NULL REFERENCES companies(company_id),site_id TEXT REFERENCES sites(site_id),
 table_index INTEGER NOT NULL,lot_id TEXT NOT NULL,name TEXT,process_description TEXT,physical_state TEXT,
 quantity_raw TEXT,quantity_value TEXT,quantity_unit TEXT,quantity_dimension TEXT,conversion_rule TEXT,
 mixture_type TEXT,visible_contamination TEXT,hazard_declaration INTEGER CHECK(hazard_declaration IN (0,1)),hazard_raw TEXT,
 site_inherited INTEGER NOT NULL,warnings TEXT NOT NULL,UNIQUE(submission_id,table_index)
);
'''
TABLES = {'controls': 'ordinal', 'cells': 'table_index,row_index,cell_index',
          'paragraphs': 'paragraph_index', 'companies': 'company_id', 'sites': 'site_id',
          'case_candidates': 'table_index'}


def connect(db, writable=False, *, create=True):
    path = Path(db)
    if writable and create:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=20)
    else:
        mode = 'rw' if writable else 'ro'
        conn = sqlite3.connect(path.resolve().as_uri() + f'?mode={mode}', uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def initialize(conn):
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise ValueError('Unsupported DB schema version')
    conn.executescript(SCHEMA)
    conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')


def insert_rows(conn, table, sid, rows):
    if table not in TABLES:
        raise ValueError('Unknown table')
    for row in rows:
        data = dict(submission_id=sid, **row)
        keys = ','.join(data)
        placeholders = ','.join('?' for _ in data)
        conn.execute(f'INSERT INTO {table} ({keys}) VALUES ({placeholders})', tuple(data.values()))


def ingest(docx, db, synthetic=False):
    path = Path(docx)
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError('DOCX exceeds 30 MiB')
    blob = path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    sid = uid(f'{VERSION}:{sha}')
    parsed = parse_document(blob)
    derived = derive_rows(parsed, sid)
    with closing(connect(db, True)) as conn:
        initialize(conn)
        try:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT synthetic FROM submissions WHERE submission_id=?', (sid,)).fetchone()
            if existing:
                if existing['synthetic'] != int(synthetic):
                    raise ValueError('Same document already imported with a different synthetic flag')
                verify_connection(conn, sid)
                conn.commit()
                return sid, False
            conn.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?)',
                (sid, path.name, sha, VERSION, int(synthetic), datetime.now(timezone.utc).isoformat(), blob))
            for table, rows in {**parsed, **derived}.items():
                insert_rows(conn, table, sid, rows)
            verify_connection(conn, sid)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    # Reopen the on-disk DB. No JSON file has been required or written.
    verify(db, sid)
    return sid, True


def get_submission(conn, sid=None):
    if sid is None:
        rows = conn.execute('SELECT submission_id FROM submissions').fetchall()
        if len(rows) != 1:
            raise ValueError('Specify --submission when DB has zero or multiple submissions')
        sid = rows[0]['submission_id']
    row = conn.execute('SELECT * FROM submissions WHERE submission_id=?', (sid,)).fetchone()
    if not row:
        raise ValueError('Submission not found')
    return dict(row)


def load_rows(conn, sid):
    out = {}
    for table, order in TABLES.items():
        rows = conn.execute(f'SELECT * FROM {table} WHERE submission_id=? ORDER BY {order}', (sid,)).fetchall()
        out[table] = [{k: r[k] for k in r.keys() if k != 'submission_id'} for r in rows]
    return out


def verify_connection(conn, sid=None):
    if conn.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
        raise ValueError('DB schema version mismatch')
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('SQLite integrity check failed')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Foreign key check failed')
    sub = get_submission(conn, sid)
    if sub['parser_version'] != VERSION:
        raise ValueError('Parser version mismatch')
    if hashlib.sha256(sub['original_docx']).hexdigest() != sub['sha256']:
        raise ValueError('Original DOCX hash mismatch')
    if sub['submission_id'] != uid(f"{VERSION}:{sub['sha256']}"):
        raise ValueError('Submission identifier mismatch')
    parsed = parse_document(sub['original_docx'])
    expected = {**parsed, **derive_rows(parsed, sub['submission_id'])}
    actual = load_rows(conn, sub['submission_id'])
    for table, expected_rows in expected.items():
        columns = TABLES[table].split(',')
        expected_sorted = sorted(expected_rows, key=lambda r: tuple(r[c] for c in columns))
        if actual[table] != expected_sorted:
            raise ValueError(f'DB/source mismatch: {table}')
    db_followup.load_requests(conn, sub['submission_id'])
    import db_guidance
    db_guidance.load_requests(conn, sub['submission_id'])
    from phase2_store import load_state
    load_state(conn, sub['submission_id'])
    from external_store import load as load_external
    load_external(conn, sub['submission_id'])
    return dict(submission_id=sub['submission_id'], status='PASS', sha256=sub['sha256'],
        counts={t: len(r) for t, r in actual.items()},
        checked=sum(c['checked'] == 1 for c in actual['controls']),
        unchecked=sum(c['checked'] == 0 for c in actual['controls']))


def verify(db, sid=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        return verify_connection(conn, sid)


def inspect(db, sid=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        report = verify_connection(conn, sid)
        sub = get_submission(conn, report['submission_id'])
        rows = load_rows(conn, report['submission_id'])
        lines = [f"DB VERIFY: PASS / {report['submission_id']}",
                 f"원본: {sub['original_name']} / synthetic={bool(sub['synthetic'])}",
                 f"업체 {len(rows['companies'])}, 현장 {len(rows['sites'])}, 폐기물 후보 {len(rows['case_candidates'])}",
                 f"컨트롤 {len(rows['controls'])}, 체크 선택 {report['checked']}, 미선택 {report['unchecked']}"]
        for co in rows['companies']:
            lines.append(f"업체: {co['name']} / {co['business_number']} / {co['contact_name']} / {co['contact_phone']}")
        sites = {s['site_id']: s for s in rows['sites']}
        requests = {r['case_id']: r for r in db_followup.load_requests(conn, report['submission_id'])}
        for c in rows['case_candidates']:
            site = sites.get(c['site_id'], {}).get('address')
            lines.append(f"폐기물: {c['name']} | {c['quantity_value']} {c['quantity_unit']} ({c['quantity_dimension']}) | 유해물질={c['hazard_raw']}")
            lines.append(f"  현장: {site} / 공통 현장 상속={bool(c['site_inherited'])}")
            lines.append(f"  폐기물 ID: {c['case_id']}")
            request = requests.get(c['case_id'])
            if request:
                value = request['measured_at'] or '미확인'
                lines.append(f"  실제 측정일: {value} / 질문 상태: {db_followup.status(request)}")
        lines.append('DB 저장 대조 통과는 사용자 확인·법적 분류·실제 발행을 뜻하지 않습니다.')
        return '\n'.join(lines)


def create_measurement_question(db, case_id, sid=None):
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report = verify_connection(conn, sid)
        case = conn.execute(
            'SELECT * FROM case_candidates WHERE submission_id=? AND case_id=?',
            (report['submission_id'], case_id),
        ).fetchone()
        if case is None:
            raise ValueError('현재 제출에서 해당 폐기물 ID를 찾을 수 없습니다.')
        if case['quantity_value'] is None:
            raise ValueError('측정일 질문을 만들려면 이번 처리 수량이 필요합니다.')
        conn.execute(db_followup.CREATE_TABLE)
        rid = db_followup.request_id(case_id)
        conn.execute(
            'INSERT INTO measurement_date_requests (request_id, case_id, asked_at) VALUES (?,?,?) '
            'ON CONFLICT(case_id) DO NOTHING',
            (rid, case_id, datetime.now(timezone.utc).isoformat()),
        )
        return db_followup.select_request(conn, report['submission_id'], rid), db_followup.question(case)


def record_measurement_answer(db, request_id, text, sid=None, *, correct=False):
    raw = text.strip()
    measured = db_followup.parse_answer(raw)
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report = verify_connection(conn, sid)
        request = db_followup.select_request(conn, report['submission_id'], request_id)
        current = request['answer_text']
        if current == raw:
            return False
        answered_at = datetime.now(timezone.utc).isoformat()
        if correct:
            if current is None:
                raise ValueError('첫 답변은 answer 명령으로 저장하세요.')
            conn.execute(db_followup.CREATE_CORRECTIONS)
            conn.execute(
                'INSERT INTO measurement_date_corrections VALUES (?,?,?,?,?)',
                (request_id, request['revision'] + 1, raw, measured, answered_at),
            )
        else:
            if current is not None:
                raise ValueError('이미 저장된 답변입니다. 정정하려면 correct 명령을 사용하세요.')
            conn.execute(
                'UPDATE measurement_date_requests SET answer_text=?, measured_at=?, answered_at=? WHERE request_id=?',
                (raw, measured, answered_at, request_id),
            )
        db_followup.select_request(conn, report['submission_id'], request_id)
        return True


def measurement_history(db, request_id, sid=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        report = verify_connection(conn, sid)
        return db_followup.select_request(conn, report['submission_id'], request_id)['history']
