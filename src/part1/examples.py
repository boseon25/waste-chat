"""Three explicitly synthetic, unpublished cases; never use as factual evidence."""
from datetime import datetime, timezone
from models import WasteCase, Evidence, Locator, InformationRequest, Component
from normalization import normalize_quantity
STAMP = datetime(2026, 11, 8, 7, 0, tzinfo=timezone.utc)


def make_examples():
    specs = [
        ('SYN-01', '단일 재질 PE 자투리', 'PE 원단 절단 작업', '마른 고체 조각', '0.2t', 'single', ['PE'], None, '모름'),
        ('SYN-02', '종이·비닐 혼합물', '포장재 분리 수거 작업', '고체', '포대 10개', 'multiple_unspecified', ['종이', '비닐'], None, '모름'),
        ('SYN-03', '기계 세척액', '절삭 기계 세척 작업', '액체', '100L', None, [], False, '없음'),
    ]
    cases = []
    for cid, name, process, state, raw_q, mixture, materials, hazard, hazard_raw in specs:
        eid, mid = cid + '-EV-01', cid + '-MSG-01'
        raw = (f'[합성] 이름:{name}; 작업:{process}; 형태:{state}; 이번 처리량:{raw_q}; 재질:{materials}; '
               f'혼합:{mixture}; 유해물질:{hazard_raw}. 측정방법·날짜와 비율·분리성은 미확인. 분석자료 없음.')
        ev = Evidence(evidence_id=eid, kind='user_message', message_id=mid,
                      source_locator=Locator(kind='message', message_id=mid), raw_excerpt=raw,
                      applicable_case_ids=[cid], applicability='matched')
        def fact(value, raw_value=None):
            return dict(value=value, raw_value=raw_value if raw_value is not None else str(value),
                        basis='user_report', evidence_ids=[eid], applicability='matched',
                        status='conditional' if value is not None else 'needs_info', updated_at=STAMP)
        q = normalize_quantity(raw_q, eid)
        question = InformationRequest(request_id=cid + '-Q-01', case_id=cid, based_on_case_version=0,
            requested_by='part1', fields=['quantity.method', 'quantity.measured_at'],
            trigger_rule_id='SYN-Q-QUANTITY-BASIS', reason='합성 서식의 이번 처리량 기재 근거 확인을 시연합니다.',
            question='이 양은 실제로 재거나 센 값인가요? 방법과 날짜를 알려 주세요. 모르면 미확인으로 남깁니다.',
            answer_type='text', acceptable_evidence=['user_message', 'measurement'],
            blocking_scope='document:SYN-lot-quantity')
        c = WasteCase(case_id=cid, company_id='SYN-CO', submission_id='SYN-SUB', lot_id=cid + '-LOT',
                      synthetic=True, name=fact(name), process_description=fact(process), physical_state=fact(state),
                      mixture_type=fact(mixture, mixture or '모름'), hazard_declaration=fact(hazard, hazard_raw),
                      quantity=q, evidence=[ev], information_requests=[question],
                      components=[Component(name=fact(m)) for m in materials],
                      missing_fields=['site_id', 'quantity.method', 'quantity.measured_at', 'photos.full', 'photos.close'],
                      limitations=['SYNTHETIC_NOT_PUBLISHED', 'HAZARD_UNVERIFIED', 'NO_REVIEWED_CLASSIFICATION_RULES'],
                      extractor_version='synthetic-fixture-1')
        data = c.model_dump(mode='json')
        def stable_times(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == 'updated_at':
                        obj[k] = STAMP.isoformat()
                    else:
                        stable_times(v)
            elif isinstance(obj, list):
                for item in obj:
                    stable_times(item)
        stable_times(data)
        if cid == 'SYN-02':
            data['missing_fields'] += ['mixture_type', 'separability']
        if cid == 'SYN-03':
            data['limitations'].append('NO_DENSITY_EVIDENCE')
        cases.append(WasteCase.model_validate(data))
    return cases
