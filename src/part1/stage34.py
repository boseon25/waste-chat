"""Case-scoped workflow for existing records and new evidence."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import db_guidance
import external_documents
import external_store
import guided_review
import phase2_rules
import phase2_store
from db_store import connect, verify_connection
from external_fields import DEFAULT_NEEDS, FIELDS
from external_routes import CLASSIFICATION_TABLE, KINDS, status
from internal_records import records


TOPICS = {'authority': 'classification', 'lab': 'fuel_properties', 'hazard': 'hazard',
          'circular_registration': 'item_conditions', 'circular_recognition': 'item_conditions'}
QUESTIONS = {
    'authority': '같은 배출 현장·공정에서 발생한 같은 폐기물의 신고자료나 기관 회신이 있나요? 원료·혼합 상태가 달라졌다면 현재 건의 근거로 쓸 수 있는지도 확인해 주세요.',
    'hazard': '현재 폐기물의 공정·원료·오염 상태를 확인할 자료나 제출용 분석결과서가 있나요?',
    'lab': '검토하려는 활용 경로에 필요한 시험항목이 적힌 같은 시료의 시험성적서가 있나요?',
    'circular_registration': '같은 품목·사업장·이용 조건에 해당하는 순환자원 지정고시 등록확인서가 있나요?',
    'circular_recognition': '같은 품목·사업장·이용 조건에 해당하는 순환자원 인정서가 있나요?',
}
ROUTE_SCOPES = {'lab': 'route:stage34-tests',
                'circular_registration': 'route:stage34-circular-registration',
                'circular_recognition': 'route:stage34-circular-recognition'}

EVIDENCE = {'authority': 'classification_evidence', 'lab': 'hazard_evidence', 'hazard': 'hazard_evidence'}
AUTHORITY_PURPOSE = '현재 폐기물 건의 분류 근거 확인'


def active_evidence(conn, bundle, cid, kind):
    if kind not in TOPICS:
        return None
    case = phase2_store.target_case(bundle, cid)
    sid = case['submission_id']
    requests = db_guidance.load_requests(conn, sid)
    field = EVIDENCE.get(kind, 'classification_evidence')
    scope = phase2_rules.dependency(case, field)
    if kind in ROUTE_SCOPES:
        marker = '[phase2-context:' + scope + ']'
        return next((r for r in reversed(requests) if r['case_id'] == cid and r['topic'] == TOPICS[kind]
                     and r['blocking_scope'] == ROUTE_SCOPES[kind] and marker in r['purpose']), None)
    report = verify_connection(conn, sid)
    original = phase2_store.target_case(phase2_store.initial_bundle(conn, sid, report), cid)
    return phase2_store.guidance_for(requests, cid, TOPICS[kind], scope,
                                    phase2_rules.dependency(original, field))


def evidence_question(db, cid, kind):
    if kind not in TOPICS:
        raise ValueError('지원하지 않는 자료 확인 항목입니다.')
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        bundle, _ = external_store.state(conn, cid)
        case = phase2_store.target_case(bundle, cid)
        if phase2_rules.identity_blocked(case, {s['site_id']: s for s in bundle['sites']}):
            raise ValueError('배출 현장과 폐기물 이름을 먼저 확인하세요.')
        existing = active_evidence(conn, bundle, cid, kind)
        if existing:
            return existing
        scope = phase2_rules.dependency(case, EVIDENCE.get(kind, 'classification_evidence'))
        purpose = f'{QUESTIONS[kind]} [phase2-context:{scope}]'
    return db_guidance.create_request(db, cid, TOPICS[kind], purpose, ROUTE_SCOPES.get(kind, 'classification'))


def answer_evidence(db, cid, kind, value, attachment=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        bundle, _ = external_store.state(conn, cid)
        request = active_evidence(conn, bundle, cid, kind)
    if request is None:
        raise ValueError('현재 건의 자료 보유 여부 질문을 먼저 여세요.')
    value = (value or '').strip() or ('자료를 첨부했습니다.' if attachment else '')
    short_answer = value.rstrip('.!? ').strip()
    if short_answer in {'없음', '없어요', '없습니다', '자료 없음', '자료 없어요', '자료가 없어요', '자료가 없습니다'}:
        value = '자료 없음'
    elif short_answer in {'모름', '모르겠어요', '모르겠습니다', '몰라요', '잘 모르겠어요', '잘 모르겠습니다'}:
        value = '모름'
    saved = db_guidance.record_answer(db, request['request_id'], value, attachment=attachment,
                                     correct=request['revision'] > 0)
    return {'request_id': request['request_id'], 'saved': saved}


def workspace(db, cid, registry=None, preferred_packet=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        bundle, packets = external_store.state(conn, cid)
        case = phase2_store.target_case(bundle, cid)
        intake = records(case)
        history = guided_review.history_documents(conn, cid)
        choices = {item['kind']: item for item in guided_review.decisions(conn, bundle, cid)}
        base = {'groups': {}, 'fields': FIELDS, 'intake': intake, 'history': history,
                'classification_table': CLASSIFICATION_TABLE, 'evidence_questions': QUESTIONS,
                'classification_inputs': external_store.projection(bundle, cid)}
        if phase2_rules.identity_blocked(case, {site['site_id']: site for site in bundle['sites']}):
            return base
        groups = base['groups']
        for kind in KINDS:
            evidence = active_evidence(conn, bundle, cid, kind)
            decision = choices.get(kind)
            current = [
                p for p in reversed(packets)
                if p['case_id'] == cid and p['kind'] == kind
                and p['context'] == external_store.context(bundle, cid, p['purpose'], p['sample_id'])
            ]
            packet = next((p for p in current if p['packet_id'] == preferred_packet),
                          current[0] if current else None)
            if (evidence and evidence['status'] == 'open'
                    and (kind == 'lab' or (kind == 'authority' and not decision))):
                groups[kind] = {'status': 'open', 'request_id': evidence['request_id'], 'packet': None}
                continue
            answer = evidence['history'][-1] if evidence and evidence['history'] else {}
            prior_file = answer.get('file')
            prior_history = next((doc for doc in history if prior_file and doc['sha256'] == prior_file['sha256']), None)
            reported_new = kind == 'authority' and intake['history']['status'] in ('new', 'none')
            groups[kind] = {
                'status': evidence['status'] if evidence else
                          'new_or_no_history' if reported_new else
                          'decided' if decision else 'existing_packet' if packet else 'unasked',
                'request_id': evidence['request_id'] if evidence else None,
                'prior_file': prior_file, 'prior_history': prior_history, 'decision': decision, 'packet': packet,
            }
    for kind, group in groups.items():
        if not group['packet']:
            continue
        packet = external_store.get(db, cid, group['packet']['packet_id'])
        group['packet'] = {key: packet[key] for key in (
            'packet_id', 'fields', 'sample_id', 'purpose', 'documents', 'reviews', 'still_missing',
            'requestable_fields', 'inventory_revision', 'stale', 'events',
        )}
        choice = (group['decision'] or {}).get('choice')
        reviewed = packet['reviews'] or any(event['kind'] == 'document_checked_no_values' for event in packet['events'])
        prior_unavailable = group['status'] in ('unavailable', 'new_or_no_history', 'existing_packet')
        if kind == 'authority':
            should_guide = choice == 'unresolved'
        elif kind == 'lab':
            should_guide = prior_unavailable or reviewed
        else:
            should_guide = choice == 'new'
        if packet['requestable_fields'] and not packet['stale'] and should_guide:
            group['route'] = status(kind, registry)
    return base


def classification_decision(db, cid, choice, notes, reference=None, code=None):
    guided_review.save_decision(db, cid, 'authority', choice, notes, reference, code)


def prepare(db, cid, kind, needs=None, sample=None, *, mode=None, reason=None):
    if kind not in KINDS:
        raise ValueError('지원하지 않는 문서 종류입니다.')
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        bundle, _ = external_store.state(conn, cid)
        case = phase2_store.target_case(bundle, cid)
        evidence = active_evidence(conn, bundle, cid, kind)
        if kind == 'lab' and (not evidence or evidence['status'] == 'open'):
            raise ValueError('기존 시험성적서가 있는지 먼저 답해 주세요.')
        if kind == 'authority':
            latest = next((d for d in reversed(guided_review.decisions(conn, bundle, cid))
                           if d['kind'] == 'authority'), None)
            may_use_existing_path = (evidence and evidence['status'] != 'open'
                                     or records(case)['history']['status'] in ('new', 'none'))
            if not may_use_existing_path and (not latest or latest['choice'] != 'unresolved'):
                raise ValueError('별표 4 대조 결과를 선택하거나 보유 자료를 먼저 연결하세요.')
    if kind == 'authority':
        needs, sample = DEFAULT_NEEDS, None
        purpose = AUTHORITY_PURPOSE
    elif kind == 'lab':
        if not needs or any(FIELDS.get(field, {}).get('kind') != 'lab' for field in needs):
            raise ValueError('이번에 확인할 시험항목을 선택하세요.')
        if 'analysis.heating_value' in needs and any(f.startswith('analysis.heating_value_') for f in needs):
            raise ValueError('발열량은 고위 또는 저위로 구분해서 선택하세요.')
        purpose = (reason or '').strip()
        if not purpose:
            raise ValueError('활용 경로와 선택한 시험항목이 필요한 근거를 알려 주세요.')
    else:
        reason = (reason or '').strip()
        guided_review.save_decision(db, cid, kind, mode, reason)
        needs = [field for field, spec in FIELDS.items() if spec['kind'] == kind]
        purpose = reason
    return external_store.plan(db, cid, purpose, needs, sample or None)


def draft(db, cid, pid, outdir, registry=None):
    packet = external_store.get(db, cid, pid)
    group = workspace(db, cid, registry, pid)['groups'].get(packet['kind'], {})
    if not group.get('route') or group['packet']['packet_id'] != pid:
        raise ValueError('현재 근거를 대조하고 외부 확인이 필요한지 먼저 선택하세요.')
    return external_documents.draft(db, cid, pid, outdir, registry)


def import_prior_file(db, cid, pid, issuer, day, sample=None):
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        bundle, packet, _ = external_store.current_packet(conn, cid, pid)
        evidence = active_evidence(conn, bundle, cid, packet['kind'])
        if not evidence or not evidence['history'] or not evidence['history'][-1]['file_id']:
            raise ValueError('이 폐기물 건의 현재 답변에 첨부된 원본을 찾을 수 없습니다.')
        file_id = evidence['history'][-1]['file_id']
        item = conn.execute('SELECT original_name,content FROM guidance_files WHERE file_id=? AND request_id=?',
                            (file_id, evidence['request_id'])).fetchone()
        if not item:
            raise ValueError('첨부 원본을 찾을 수 없습니다.')
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(item['original_name']).name
        path.write_bytes(item['content'])
        return external_store.attach(db, cid, pid, path, issuer, day, sample)


def mark_no_values(db, cid, pid, document_id, reason):
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise ValueError('원문을 확인한 결과와 빠진 값의 근거를 입력하세요.')
    with closing(connect(db, writable=True, create=False)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        _, packet, _ = external_store.current_packet(conn, cid, pid)
        if document_id not in {doc['document_id'] for doc in packet['documents']}:
            raise ValueError('이 문서 묶음에 첨부한 원본을 선택하세요.')
        conn.execute(
            'INSERT INTO external_events(packet_id,kind,reference,actor,created_at) VALUES(?,?,?,?,?)',
            (pid, 'document_checked_no_values', document_id + ' / ' + reason, '사용자 원문 확인', external_store.now()),
        )
