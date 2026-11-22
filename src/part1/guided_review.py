"""User decisions and optional historical attachments for the current case."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import db_guidance
from datetime import date

from db_followup import table_exists
from db_store import connect
from external_fields import normalize
from external_store import context, now, sha, state, uid
from phase2_rules import identity_blocked
from phase2_store import target_case

DECISIONS = '''
CREATE TABLE IF NOT EXISTS guided_decisions (
    id INTEGER PRIMARY KEY, case_id TEXT NOT NULL REFERENCES case_candidates(case_id),
    context TEXT NOT NULL, kind TEXT NOT NULL, choice TEXT NOT NULL, reference TEXT,
    notes TEXT NOT NULL, code TEXT, recorded_at TEXT NOT NULL
)
'''
HISTORY = '''
CREATE TABLE IF NOT EXISTS history_documents (
    document_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES case_candidates(case_id),
    name TEXT NOT NULL, kind TEXT NOT NULL, issuer TEXT NOT NULL, document_date TEXT,
    sha256 TEXT NOT NULL, original BLOB NOT NULL, recorded_at TEXT NOT NULL
)
'''
EXTRA_KINDS = ('hazard', 'circular_registration', 'circular_recognition')
HISTORY_KINDS = ('declaration', 'transfer', 'treatment')


def decision_scope(bundle, cid, kind):
    return context(bundle, cid, 'guided-decision:' + kind, None)


def decisions(conn, bundle, cid, *, include_old=False):
    if not table_exists(conn, 'guided_decisions'):
        return []
    out = []
    for row in conn.execute('SELECT * FROM guided_decisions WHERE case_id=? ORDER BY id', (cid,)):
        item = dict(row)
        item['stale'] = item['context'] != decision_scope(bundle, cid, item['kind'])
        if include_old or not item['stale']:
            out.append(item)
    return out


def save_decision(db, cid, kind, choice, notes, reference=None, code=None):
    notes = (notes or '').strip()
    reference = (reference or '').strip()
    if kind == 'authority' and not notes:
        notes = {
            'unresolved': '별표 4와 현재 자료를 대조했으나 분류번호 또는 적용 조건을 확인하기 어려운 상태입니다.',
            'later': '현재 자료를 더 확인할 예정입니다.',
        }.get(choice, '')
    notes_required = kind != 'authority' or choice != 'supported'
    if (notes_required and not notes) or len(notes) > 2000 or len(reference) > 2000:
        raise ValueError('현재 건에 대한 판단 근거 또는 요청 목적을 입력하세요.')
    if kind == 'authority':
        if choice not in ('supported', 'unresolved', 'later'):
            raise ValueError('별표 4 대조 결과를 선택하세요.')
        if choice == 'supported':
            if not reference or not code:
                raise ValueError('대조한 분류번호와 별표 4 항목 또는 현재 건을 뒷받침하는 근거를 입력하세요.')
            code = normalize('classification.reported_code', code)
            if code is None:
                raise ValueError('자료로 확인한 분류번호를 입력하세요.')
        else:
            code = None
    elif kind not in EXTRA_KINDS or choice not in ('existing', 'new'):
        raise ValueError('기존 자료 검토 또는 새 발급 준비를 선택하세요.')
    else:
        code = None
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        bundle, _ = state(conn, cid)
        case = target_case(bundle, cid)
        if identity_blocked(case, {s['site_id']: s for s in bundle['sites']}):
            raise ValueError('배출 현장과 폐기물 이름을 먼저 확인하세요.')
        conn.execute(DECISIONS)
        scope = decision_scope(bundle, cid, kind)
        conn.execute('''INSERT INTO guided_decisions(case_id,context,kind,choice,reference,notes,code,recorded_at)
                     VALUES (?,?,?,?,?,?,?,?)''',
                     (cid, scope, kind, choice, reference or None, notes, code, now()))


def history_documents(conn, cid):
    if not table_exists(conn, 'history_documents'):
        return []
    out = []
    for row in conn.execute('SELECT * FROM history_documents WHERE case_id=? ORDER BY rowid', (cid,)):
        item = dict(row)
        if sha(item.pop('original')) != item['sha256']:
            raise ValueError('기존 기록의 원본 해시가 일치하지 않습니다.')
        out.append(item)
    return out


def attach_history(db, cid, kind, name, content, issuer, day):
    if kind not in HISTORY_KINDS:
        raise ValueError('신고, 인계, 처리 기록 중 문서 유형을 선택하세요.')
    basename = Path(name).name
    if Path(basename).suffix.lower() not in ('.pdf','.docx','.hwpx','.hwp','.png','.jpg','.jpeg','.txt','.csv','.xlsx'):
        raise ValueError('지원하지 않는 기록 파일 형식입니다.')
    if not 0 < len(content) <= 20 * 1024 * 1024:
        raise ValueError('첨부 크기를 확인하세요.')
    day = date.fromisoformat(day).isoformat() if day else None
    issuer = issuer.strip()
    if not issuer or len(issuer) > 300:
        raise ValueError('문서 발급 또는 작성 기관을 입력하세요.')
    digest = sha(content)
    document_id = uid(f'{cid}:history:{kind}:{digest}')
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state(conn, cid)
        conn.execute(HISTORY)
        conn.execute('INSERT INTO history_documents VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(document_id) DO NOTHING',
                     (document_id, cid, basename, kind, issuer, day, digest, content, now()))
    return document_id


def link_declaration(db, cid, document_id, request_id):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        bundle, _ = state(conn, cid)
        case = target_case(bundle, cid)
        request = next((row for row in db_guidance.load_requests(conn, case['submission_id'])
                        if row['request_id'] == request_id and row['case_id'] == cid
                        and row['topic'] == 'classification'), None)
        if not request or not table_exists(conn, 'history_documents'):
            raise ValueError('현재 건의 분류자료 요청에 보유한 신고자료를 선택하세요.')
        row = conn.execute(
            "SELECT * FROM history_documents WHERE document_id=? AND case_id=? AND kind='declaration'",
            (document_id, cid),
        ).fetchone()
        if row is None or sha(row['original']) != row['sha256']:
            raise ValueError('현재 폐기물 건에서 확인할 신고자료를 찾지 못했습니다.')
    with TemporaryDirectory() as folder:
        path = Path(folder) / Path(row['name']).name
        path.write_bytes(row['original'])
        db_guidance.record_answer(db, request_id, '보유 신고자료를 검토 대상으로 연결했습니다.',
                                  attachment=path, correct=request['revision'] > 0)
