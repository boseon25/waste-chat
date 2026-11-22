"""Measurement date questions and their user-reported answers."""
from datetime import date, datetime
from intake_parser import uid

FIELD = 'quantity.measured_at'
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS measurement_date_requests (
 request_id TEXT PRIMARY KEY,
 case_id TEXT NOT NULL UNIQUE REFERENCES case_candidates(case_id),
 asked_at TEXT NOT NULL,
 answer_text TEXT,
 measured_at TEXT,
 answered_at TEXT,
 CHECK (
  (answer_text IS NULL AND measured_at IS NULL AND answered_at IS NULL)
  OR (answer_text IS NOT NULL AND answered_at IS NOT NULL AND
      ((answer_text = '모름' AND measured_at IS NULL) OR
       (measured_at IS NOT NULL AND measured_at = answer_text)))
 )
)
"""

CREATE_CORRECTIONS = """
CREATE TABLE IF NOT EXISTS measurement_date_corrections (
 request_id TEXT NOT NULL REFERENCES measurement_date_requests(request_id),
 revision INTEGER NOT NULL CHECK(revision >= 2),
 answer_text TEXT NOT NULL,
 measured_at TEXT,
 answered_at TEXT NOT NULL,
 PRIMARY KEY(request_id, revision),
 CHECK ((answer_text = '모름' AND measured_at IS NULL) OR
        (measured_at IS NOT NULL AND measured_at = answer_text))
)
"""


def request_id(case_id):
    return uid(f'{case_id}:request:{FIELD}')


def parse_answer(text):
    if text == '모름':
        return None
    try:
        parsed = date.fromisoformat(text)
        if parsed.isoformat() == text:
            return text
    except ValueError:
        pass
    raise ValueError('답변은 YYYY-MM-DD 또는 모름으로 입력하세요.')


def status(row):
    if row['answer_text'] is None:
        return 'open'
    return 'answered' if row['measured_at'] is not None else 'unavailable'


def question(case):
    name = case['name'] or f"표 {case['table_index'] + 1}의 폐기물"
    return f"{name}의 이번 처리 수량 {case['quantity_value']}{case['quantity_unit']}은 실제로 언제 측정했나요?"


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def load_requests(conn, sid):
    # Older databases may have neither table or only the initial-answer table.
    if not table_exists(conn, 'measurement_date_requests'):
        return []
    rows = conn.execute(
        'SELECT r.* FROM measurement_date_requests r JOIN case_candidates c USING (case_id) '
        'WHERE c.submission_id=? ORDER BY c.table_index', (sid,)
    ).fetchall()
    corrections = {}
    if table_exists(conn, 'measurement_date_corrections'):
        for row in conn.execute(
            'SELECT a.* FROM measurement_date_corrections a '
            'JOIN measurement_date_requests r USING (request_id) '
            'JOIN case_candidates c USING (case_id) WHERE c.submission_id=? '
            'ORDER BY a.request_id, a.revision', (sid,)
        ):
            corrections.setdefault(row['request_id'], []).append(dict(row))
    requests = []
    for row in rows:
        if row['request_id'] != request_id(row['case_id']):
            raise ValueError('측정일 질문 ID와 대상 폐기물이 일치하지 않습니다.')
        previous_time = datetime.fromisoformat(row['asked_at'])
        if previous_time.utcoffset() is None:
            raise ValueError('측정일 질문 시각에 시간대가 없습니다.')
        history = corrections.get(row['request_id'], [])
        if row['answer_text'] is not None:
            first = {key: row[key] for key in ('request_id', 'answer_text', 'measured_at', 'answered_at')}
            history.insert(0, dict(first, revision=1))
        elif history:
            raise ValueError('첫 답변이 없는 질문에 정정 기록이 있습니다.')
        previous_text = None
        for expected_revision, answer in enumerate(history, start=1):
            answered = datetime.fromisoformat(answer['answered_at'])
            if (answer['revision'] != expected_revision or answered.utcoffset() is None
                    or answered < previous_time or answer['answer_text'] == previous_text):
                raise ValueError('측정일 답변 이력의 순서 또는 내용이 올바르지 않습니다.')
            if parse_answer(answer['answer_text']) != answer['measured_at']:
                raise ValueError('측정일 답변 원문과 저장값이 일치하지 않습니다.')
            previous_time, previous_text = answered, answer['answer_text']
        request = dict(row, revision=0, history=history)
        if history:
            request.update(history[-1])
        requests.append(request)
    return requests


def select_request(conn, sid, rid):
    for request in load_requests(conn, sid):
        if request['request_id'] == rid:
            return request
    raise ValueError('현재 제출에서 해당 질문 ID를 찾을 수 없습니다.')
