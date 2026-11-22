"""Build review-only WasteCase JSON exclusively from verified persisted DB rows."""
import json
import os
import tempfile
from contextlib import closing
from pathlib import Path
from datetime import date
from models import WasteCase, Evidence, Locator, Company, Site, Fact, Component
from intake_parser import VERSION, one, answer, uid
from db_store import connect, get_submission, load_rows, verify_connection
from review_contract import DBReviewExport
import db_followup


def build_export(sub, rows, report, requests=()):
    controls = rows['controls']
    cases = rows['case_candidates']
    case_by_table = {c['table_index']: c['case_id'] for c in cases}
    all_ids = [c['case_id'] for c in cases]
    evidence = []
    eids = {}
    for c in controls:
        eid = uid(sub['submission_id'] + ':control:' + str(c['ordinal']))
        eids[c['ordinal']] = eid
        loc = Locator(kind='docx', table=c['table_index'], row=c['row_index'], cell=c['cell_index'],
                      paragraph=c['paragraph_index'], tag=c['tag'])
        applies = [case_by_table[c['table_index']]] if c['table_index'] in case_by_table else all_ids
        evidence.append(Evidence(evidence_id=eid, kind='input_document', file_id=sub['submission_id'],
            sha256=sub['sha256'], source_locator=loc, raw_excerpt=c['raw_value'],
            applicable_case_ids=applies, applicability='matched'))

    requests_by_case = {r['case_id']: r for r in requests}
    answer_evidence = {}
    for request in requests:
        if request['answer_text'] is None:
            continue
        rid = request['request_id']
        if request['revision'] > 1:
            rid = f"{rid}:revision:{request['revision']}"
        message_id = uid(f'{rid}:answer-message')
        answer_evidence[request['case_id']] = Evidence(
            evidence_id=uid(f'{rid}:answer-evidence'), kind='user_message',
            message_id=message_id, source_locator=Locator(kind='message', message_id=message_id),
            raw_excerpt=request['answer_text'], applicable_case_ids=[request['case_id']],
            applicability='matched',
        )

    def fact(value, sources, raw=None):
        sources = [s for s in sources if s is not None]
        return dict(value=value, raw_value=raw if raw is not None else ' | '.join(s['raw_value'] for s in sources) or None,
            basis='user_report' if sources else None, status='conditional' if value is not None else 'needs_info',
            verification='unverified', evidence_ids=[eids[s['ordinal']] for s in sources],
            applicability='matched' if sources else 'unknown', updated_at=sub['created_at'])

    company_row = rows['companies'][0]
    company = Company(company_id=company_row['company_id'], **{
        field: fact(company_row[field], [one(controls, tag=tag)]) for field, tag in
        [('name', 'company_name'), ('business_number', 'business_number'), ('contact_name', 'contact_name'), ('contact_phone', 'contact_phone')]})
    sites = [Site(site_id=s['site_id'], company_id=s['company_id'],
        address=fact(s['address'], [next(c for c in controls if c['ordinal'] == s['source_ordinal'])])) for s in rows['sites']]
    site_rows = {s['site_id']: s for s in rows['sites']}
    date_source = one(controls, tag='source_form_date')
    form_date = answer(date_source)
    date_warning = False
    if form_date:
        try:
            form_date = date.fromisoformat(form_date)
        except ValueError:
            form_date, date_warning = None, True
    snapshots = []
    for row in cases:
        local = [c for c in controls if c['table_index'] == row['table_index']]
        source = lambda suffix: one(local, suffix=suffix)
        group = lambda name: [c for c in local if c['tag'].rsplit('_', 1)[0].endswith('_' + name) and c['is_checkbox']]
        q = fact(row['quantity_value'], [source('quantity')], row['quantity_raw'])
        q.update(unit=row['quantity_unit'], dimension=row['quantity_dimension'], scope='current_lot',
                 conversion_rule=row['conversion_rule'])
        method_control = source('quantity_evidence')
        method_raw = answer(method_control)
        # Preserve the whole statement. Do not mistake 'no report' alone for a measurement method.
        method = method_raw if method_raw and ('측정' in method_raw or '계수' in method_raw) else None
        q['method'] = fact(method, [method_control])
        net = True if method and '용기 무게를 제외' in method else None
        q['net_weight'] = fact(net, [method_control] if net is not None else [])
        site_ev = []
        if row['site_id']:
            site_ev = [eids[site_rows[row['site_id']]['source_ordinal']]]
        missing = [db_followup.FIELD, 'photos.full', 'photos.close']
        request = requests_by_case.get(row['case_id'])
        response = answer_evidence.get(row['case_id'])
        information_requests = []
        if request:
            information_requests.append(dict(
                request_id=request['request_id'], case_id=row['case_id'], based_on_case_version=0,
                requested_by='part1', fields=[db_followup.FIELD], trigger_rule_id='missing_measurement_date',
                reason='이번 처리 수량의 실제 측정일이 입력서에서 확인되지 않습니다.',
                question=db_followup.question(row), answer_type='date',
                acceptable_evidence=['사용자 답변'], blocking_scope='all',
                status=db_followup.status(request),
                answer_evidence_ids=[response.evidence_id] if response else [],
            ))
        if response:
            measured = request['measured_at']
            q['measured_at'] = dict(
                value=measured, raw_value=request['answer_text'], basis='user_report',
                status='conditional' if measured else 'needs_info', verification='unverified',
                evidence_ids=[response.evidence_id], applicability='matched', updated_at=request['answered_at'],
            )
            if measured:
                missing.remove(db_followup.FIELD)
        for field in ('name', 'process_description', 'physical_state', 'site_id'):
            if row[field] is None:
                missing.append(field)
        if row['quantity_value'] is None:
            missing.append('quantity.value')
        if method is None:
            missing.append('quantity.method')
        if row['hazard_declaration'] is None:
            missing.append('hazard_declaration')
        limitations = ['DRAFT_DB_EXPORT_NOT_PUBLISHED', 'USER_REVIEW_REQUIRED', 'NO_REVIEWED_CLASSIFICATION_RULES',
                       'HAZARD_UNVERIFIED', 'PHOTOS_NOT_UPLOADED', 'BUSINESS_REGISTRATION_NOT_VERIFIED']
        if row['quantity_dimension'] == 'volume':
            limitations.append('NO_DENSITY_EVIDENCE')
        if date_warning:
            limitations.append('SOURCE_FORM_DATE_NEEDS_REVIEW')
        limitations += [x for x in row['warnings'].splitlines() if x]
        # All local statements remain available, including absence of photos and unknown material details.
        assertions = [dict(assertion_id=uid(row['case_id'] + ':assertion:' + str(c['ordinal'])),
            field_path='intake.' + c['tag'], raw_value=c['raw_value'], evidence_ids=[eids[c['ordinal']]],
            applicability='matched', disposition='proposed', reason='DOCX 원문 보존; 사용자 검토 전 후보')
            for c in local if c['is_real']]
        components = []
        material = source('mixture_details')
        if row['mixture_type'] == 'single' and answer(material) and not answer(material).startswith('모름'):
            components = [Component(name=fact(answer(material), [material]))]
        data = dict(case_id=row['case_id'], company_id=row['company_id'], site_id=row['site_id'],
            submission_id=sub['submission_id'], lot_id=row['lot_id'], synthetic=bool(sub['synthetic']),
            name=fact(row['name'], [source('name')]),
            process_description=fact(row['process_description'], [source('process')]),
            physical_state=fact(row['physical_state'], [source('appearance')]), quantity=q,
            mixture_type=fact(row['mixture_type'], group('mixture')),
            visible_contamination=fact(row['visible_contamination'], group('contamination')),
            hazard_declaration=fact(None if row['hazard_declaration'] is None else bool(row['hazard_declaration']),
                                    group('hazard'), row['hazard_raw']),
            components=components, evidence=evidence + ([response] if response else []), assertions=assertions,
            information_requests=information_requests, draft_revision=request['revision'] if request else 0,
            source_form_date=fact(form_date, [date_source]), site_inherited=bool(row['site_inherited']),
            site_source_evidence_ids=site_ev, missing_fields=missing, limitations=limitations,
            extractor_version=VERSION)
        snapshots.append(WasteCase.model_validate(data))
    bundle = dict(export_kind='db_verified_review_draft', schema_version='2.0', published=False,
        database_verification=report, company=company.model_dump(mode='json'),
        sites=[s.model_dump(mode='json') for s in sites], evidence=[e.model_dump(mode='json') for e in evidence + list(answer_evidence.values())],
        cases=[c.model_dump(mode='json') for c in snapshots])
    return DBReviewExport.model_validate(bundle).model_dump(mode='json')


def export_json(db, out, sid=None):
    # Verify and read in one SQLite read transaction. A stale verification report is never sufficient.
    with closing(connect(db)) as conn:
        conn.execute('BEGIN')
        report = verify_connection(conn, sid)
        sub = get_submission(conn, report['submission_id'])
        result = build_export(sub, load_rows(conn, sub['submission_id']), report,
                              db_followup.load_requests(conn, sub['submission_id']))
        from guidance_export import apply_guidance
        from db_guidance import load_requests
        guides = load_requests(conn, sub['submission_id'])
        result = apply_guidance(result, guides)
        from phase2_store import load_state
        from phase2_export import apply_phase2
        result = apply_phase2(result, load_state(conn, sub['submission_id']), guides)
    target = Path(out)
    if target.resolve() == Path(db).resolve():
        raise ValueError('Output must not overwrite DB')
    if target.suffix.lower() != '.json':
        raise ValueError('Output must use .json extension')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement only after DB verification and model validation have both succeeded.
    fd, temp = tempfile.mkstemp(prefix='.export-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return result
