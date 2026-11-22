"""Expose pending requests and their evidence, not accepted domain conclusions."""
from intake_parser import uid
from models import Evidence, Locator
from review_contract import DBReviewExport


def apply_guidance(bundle, requests):
    cases = {c['case_id']: c for c in bundle['cases']}
    cards = {}
    for r in requests:
        c = cases[r['case_id']]
        cards[r['guidance']['guidance_id']] = r['guidance']
        ids = []
        if r['history']:
            a = r['history'][-1]
            message_id = uid(r['request_id'] + ':answer:' + str(a['revision']))
            e = Evidence(evidence_id=message_id, kind='user_message', message_id=message_id,
                source_locator=Locator(kind='message', message_id=message_id),
                raw_excerpt=a['answer_text'], applicable_case_ids=[c['case_id']], applicability='matched')
            new_evidence = [e]
            ids.append(e.evidence_id)
            if a['file']:
                f = a['file']
                ev = Evidence(evidence_id=uid(f['file_id'] + ':evidence'), kind='input_document',
                    file_id=f['file_id'], sha256=f['sha256'], source_locator=Locator(kind='file', section=f['original_name']),
                    raw_excerpt='첨부 원본 보관됨. 내용 추출·시료·기간·현재 건 적용성 검토 전.',
                    applicable_case_ids=[c['case_id']], applicability='unknown')
                new_evidence.append(ev)
                ids.append(ev.evidence_id)
            for ev in new_evidence:
                data = ev.model_dump(mode='json')
                c['evidence'].append(data)
                bundle['evidence'].append(data)
            c['assertions'].append(dict(assertion_id=uid(message_id + ':assertion'),
                field_path='supplement.' + r['topic'], raw_value=a['answer_text'], evidence_ids=ids,
                disposition='held', applicability='unknown', reason='추가 답변/자료 접수. 의미·적용성 검토 전이며 사실값 자동 확정 금지.'))
        c['draft_revision'] += r['revision']
        g = r['guidance']
        question = g['question']
        if r['periods']:
            question += ' 대상: ' + ', '.join(r['periods']) + ' (기준일 직전 완료된 3개월; 누락 월 null).'
        c['information_requests'].append(dict(request_id=r['request_id'], case_id=c['case_id'],
            based_on_case_version=0, requested_by='part1', fields=g['fields'],
            trigger_rule_id='acquisition-023-' + r['topic'], reason=r['purpose'], question=question,
            answer_type='text', options=['자료 있음','자료 없음','모름'],
            acceptable_evidence=g['where'], guidance_id=g['guidance_id'], blocking_scope=r['blocking_scope'],
            status=r['status'], answer_evidence_ids=ids))
        c['limitations'].append(('SUPPLEMENT_UNAVAILABLE:' if r['status']=='unavailable' else 'SUPPLEMENT_REVIEW_PENDING:') + r['topic'])
    bundle['guidance_cards'] = list(cards.values())
    return DBReviewExport.model_validate(bundle).model_dump(mode='json')
