"""Case-scoped acquisition requests and append-only responses; no legal auto-acceptance."""
import hashlib
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from intake_parser import uid
from guidance_catalog import CATALOG, card, months_before
from db_followup import table_exists

SCHEMA = [
'''CREATE TABLE IF NOT EXISTS guidance_requests (
 request_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES case_candidates(case_id),
 topic TEXT NOT NULL, purpose TEXT NOT NULL, blocking_scope TEXT NOT NULL,
 as_of TEXT, asked_at TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS guidance_files (
 file_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES guidance_requests(request_id),
 original_name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS guidance_answers (
 request_id TEXT NOT NULL REFERENCES guidance_requests(request_id), revision INTEGER NOT NULL CHECK(revision>=1),
 answer_text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('answered','unavailable')),
 file_id TEXT REFERENCES guidance_files(file_id), answered_at TEXT NOT NULL,
 PRIMARY KEY(request_id, revision))''',
]
SCOPE = r'^(classification|route:[^\s:]+|matching:[^\s:]+|document:[^\s:]+)$'


def validate_request(topic, purpose, scope, as_of):
    if topic not in CATALOG or topic == 'publication':
        raise ValueError('Unsupported request topic (publication is guidance-only)')
    if not purpose.strip() or not re.fullmatch(SCOPE, scope):
        raise ValueError('구체적 요청 목적과 classification/route:id/matching:id/document:id 범위가 필요합니다.')
    if topic == 'fuel_properties' and not scope.startswith('route:'):
        raise ValueError('연료 특성은 특정 route:id와 필요 시험항목/조건을 목적에 명시하세요.')
    if topic == 'monthly_generation':
        if not as_of:
            raise ValueError('월별 발생량 요청에는 --as-of YYYY-MM-DD가 필요합니다.')
        months_before(as_of)
    elif as_of is not None:
        raise ValueError('as-of is only supported for monthly_generation')


def request_key(case_id, topic, purpose, scope, as_of):
    # Length-prefix prevents delimiter collisions without depending on JSON storage.
    parts = [case_id, topic, purpose, scope, as_of or '']
    return uid('guidance-023:' + ''.join(f'{len(x)}:{x}' for x in parts))


def load_requests(conn, sid):
    if not table_exists(conn, 'guidance_requests'):
        return []
    records = conn.execute('SELECT g.* FROM guidance_requests g JOIN case_candidates c USING(case_id) '
                           'WHERE c.submission_id=? ORDER BY g.asked_at,g.request_id', (sid,)).fetchall()
    result = []
    for record in records:
        r = dict(record)
        validate_request(r['topic'], r['purpose'], r['blocking_scope'], r['as_of'])
        if r['request_id'] != request_key(r['case_id'], r['topic'], r['purpose'], r['blocking_scope'], r['as_of']):
            raise ValueError('Guidance request identity mismatch')
        previous = datetime.fromisoformat(r['asked_at'])
        if previous.utcoffset() is None:
            raise ValueError('Question timestamp requires timezone')
        answers = []
        for n, row in enumerate(conn.execute('SELECT * FROM guidance_answers WHERE request_id=? ORDER BY revision',
                                             (r['request_id'],)), 1):
            a = dict(row)
            dt = datetime.fromisoformat(a['answered_at'])
            if a['revision'] != n or dt.utcoffset() is None or dt < previous:
                raise ValueError('Guidance answer sequence/time mismatch')
            expected = 'unavailable' if a['answer_text'] in ('모름','자료 없음') else 'answered'
            if not a['answer_text'].strip() or a['status'] != expected:
                raise ValueError('Guidance answer/status mismatch')
            a['file'] = None
            if a['file_id']:
                f = conn.execute('SELECT * FROM guidance_files WHERE file_id=? AND request_id=?',
                                 (a['file_id'], r['request_id'])).fetchone()
                if not f or a['status'] == 'unavailable':
                    raise ValueError('Invalid file/answer association')
                if hashlib.sha256(f['content']).hexdigest() != f['sha256']:
                    raise ValueError('Guidance file hash mismatch')
                if f['file_id'] != uid(r['request_id'] + ':file:' + f['sha256']):
                    raise ValueError('Guidance file identifier mismatch')
                a['file'] = {k: f[k] for k in ('file_id','original_name','sha256')}
            if answers and (a['answer_text'], a['file_id']) == (answers[-1]['answer_text'],answers[-1]['file_id']):
                raise ValueError('Duplicate consecutive guidance answers')
            answers.append(a)
            previous = dt
        r.update(history=answers, revision=len(answers), status=answers[-1]['status'] if answers else 'open')
        r['guidance'] = card(r['topic'])
        r['periods'] = months_before(r['as_of']) if r['as_of'] else []
        result.append(r)
    return result


def create_request(db, case_id, topic, purpose, scope, as_of=None, sid=None):
    from db_store import connect, verify_connection
    purpose = purpose.strip()
    validate_request(topic, purpose, scope, as_of)
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report = verify_connection(conn, sid)
        if not conn.execute('SELECT 1 FROM case_candidates WHERE case_id=? AND submission_id=?',
                            (case_id, report['submission_id'])).fetchone():
            raise ValueError('현재 제출에서 해당 폐기물 ID를 찾을 수 없습니다.')
        for sql in SCHEMA:
            conn.execute(sql)
        rid = request_key(case_id, topic, purpose, scope, as_of)
        conn.execute('INSERT INTO guidance_requests VALUES (?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING',
                     (rid, case_id, topic, purpose, scope, as_of, datetime.now(timezone.utc).isoformat()))
        return next(r for r in load_requests(conn, report['submission_id']) if r['request_id'] == rid)


def record_answer(db, rid, text, sid=None, *, attachment=None, correct=False):
    from db_store import connect, verify_connection
    text = text.strip()
    if not text or len(text) > 20000:
        raise ValueError('빈 답변 또는 20000자를 넘는 답변은 저장할 수 없습니다.')
    status = 'unavailable' if text in ('모름','자료 없음') else 'answered'
    blob = name = sha = None
    if attachment:
        if status == 'unavailable':
            raise ValueError('자료 없음/모름 응답에 파일을 함께 제출할 수 없습니다.')
        path = Path(attachment)
        if path.suffix.lower() not in ('.pdf','.docx','.xlsx','.hwpx','.hwp','.png','.jpg','.jpeg','.txt','.csv'):
            raise ValueError('지원하지 않는 첨부 형식')
        if not 0 < path.stat().st_size <= 20 * 1024 * 1024:
            raise ValueError('첨부 크기는 1바이트~20MiB여야 합니다.')
        blob, name = path.read_bytes(), path.name
        sha = hashlib.sha256(blob).hexdigest()
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report = verify_connection(conn, sid)
        r = next((x for x in load_requests(conn, report['submission_id']) if x['request_id'] == rid), None)
        if not r:
            raise ValueError('현재 제출에서 질문을 찾을 수 없습니다.')
        fid = uid(rid + ':file:' + sha) if sha else None
        latest = r['history'][-1] if r['history'] else None
        if latest and (text, fid) == (latest['answer_text'], latest['file_id']):
            return False
        if correct and not latest:
            raise ValueError('첫 답변은 supply-answer로 저장하세요.')
        if not correct and latest:
            raise ValueError('정정은 supply-correct로 저장하세요.')
        if fid:
            conn.execute('INSERT INTO guidance_files VALUES (?,?,?,?,?) ON CONFLICT(file_id) DO NOTHING',
                         (fid, rid, name, sha, blob))
        conn.execute('INSERT INTO guidance_answers VALUES (?,?,?,?,?,?)',
                     (rid, r['revision']+1, text, status, fid, datetime.now(timezone.utc).isoformat()))
        load_requests(conn, report['submission_id'])
        return True


def requests(db, sid=None, case_id=None, open_only=False):
    from db_store import connect, verify_connection
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        report = verify_connection(conn, sid)
        if case_id and not conn.execute('SELECT 1 FROM case_candidates WHERE case_id=? AND submission_id=?',
                                       (case_id, report['submission_id'])).fetchone():
            raise ValueError('현재 제출에서 해당 폐기물 ID를 찾을 수 없습니다.')
        return [r for r in load_requests(conn, report['submission_id'])
                if (not case_id or r['case_id'] == case_id) and (not open_only or r['status'] == 'open')]
