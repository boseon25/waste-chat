"""Read supplemental intake fields with their original DOCX control evidence."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import re


UNKNOWN = {'모름', '미확인', '자료 없음', '해당 없음'}
HISTORY = {'있음': 'available', '처음 발생': 'new', '이력 없음': 'none', '모름': 'unknown'}


def records(case):
    found = {}
    duplicates = set()
    for item in case.get('assertions',()):
        match = re.fullmatch(r'intake\.waste_\d+_record_(.+)', item['field_path'])
        if not match:
            continue
        key = match[1]
        if key in found:
            duplicates.add(key)
        found[key] = (item['raw_value'].strip(), item['evidence_ids'])
    for key in duplicates:
        found.pop(key)
    issues = ['중복 기록 칸: ' + key for key in sorted(duplicates)]

    def value(key):
        text, ids = found.get(key, ('', []))
        return (text if text and text not in UNKNOWN else None), ids

    def text_record(prefix):
        text, ids = value(prefix)
        source, source_ids = value(prefix + '_source')
        return {'value': text, 'source': source, 'evidence_ids': ids + source_ids}

    def amount(raw):
        if raw is None or len(raw) > 24 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', raw):
            return None
        try:
            number = Decimal(raw)
        except (InvalidOperation, TypeError):
            return None
        return number if number.is_finite() and number >= 0 and len(raw) <= 32 else None

    components, months = [], []
    for index in range(1, 4):
        name, n_ids = value(f'component{index}_name')
        ratio, r_ids = value(f'component{index}_percent')
        source, s_ids = value(f'component{index}_source')
        number = amount(ratio)
        if ratio is not None and (number is None or number > 100 or not source or not name):
            issues.append(f'구성비 {index}행: 재질명·0~100 숫자·폐기물 자체의 기록 근거 필요')
            number = None
        if name or ratio or source:
            components.append({'name': name, 'percent': str(number) if number is not None else None,
                               'source': source, 'evidence_ids': n_ids + r_ids + s_ids})
        month, m_ids = value(f'month{index}_period')
        quantity, q_ids = value(f'month{index}_quantity')
        month_source, ms_ids = value(f'month{index}_source')
        if not (month or quantity or month_source):
            continue
        valid_month = month if month and re.fullmatch(r'[1-9]\d{3}-(0[1-9]|1[0-2])', month) else None
        matched = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(kg|t|[lL])', quantity or '')
        number = amount(matched[1]) if matched else None
        if not valid_month or number is None or not month_source:
            issues.append(f'월별 발생량 {index}행: 연월·수량과 단위(kg, t, L)·발생기록 근거 필요')
            continue
        unit = 'L' if matched[2].lower() == 'l' else matched[2]
        tons = str(number / 1000) if unit == 'kg' else str(number) if unit == 't' else None
        months.append({'month': valid_month, 'quantity': str(number), 'unit': unit, 'ton': tons,
                       'source': month_source, 'evidence_ids': m_ids + q_ids + ms_ids})

    duplicated_months = {key for key, count in Counter(row['month'] for row in months).items() if count > 1}
    issues.extend('월별 발생량 중복: ' + key for key in sorted(duplicated_months))
    months = [row for row in months if row['month'] not in duplicated_months]
    if sum(Decimal(row['percent']) for row in components if row['percent'] is not None) > 100:
        issues.append('폐기물 구성비의 합이 100%를 초과함')
        for row in components:
            row['percent'] = None
    raw_history, history_ids = found.get('history', ('', []))
    history_ref = text_record('history_reference')
    history = HISTORY.get(raw_history, 'unknown')
    if raw_history and raw_history not in HISTORY:
        issues.append('기존 이력은 있음 / 처음 발생 / 이력 없음 / 모름 중 하나로 작성')
    if history_ref['value'] and history in ('new', 'none'):
        issues.append('이력 없음 답변과 기재한 과거 문서 정보가 일치하지 않음')
        history = 'unknown'
    return {
        'available': bool(found or duplicates),
        'raw_materials': text_record('materials'),
        'chemicals': text_record('chemicals'),
        'label_material': text_record('label'),
        'feed_ratio': text_record('feed_ratio'),
        'components': components,
        'monthly_generation': sorted(months, key=lambda row: row['month']),
        'history': {'status': history, 'reference': history_ref['value'],
                    'evidence_ids': history_ids + history_ref['evidence_ids']},
        'issues': issues,
        'verification': 'user_reported_from_intake',
    }
