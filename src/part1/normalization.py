"""Deterministic normalization; never infer mass from volume or container count."""
import re
from decimal import Decimal
from models import Quantity, UNITS


class ClarificationRequired(ValueError):
    pass


def normalize_quantity(raw: str, evidence_id: str) -> Quantity:
    original = raw
    raw = raw.strip()
    base = dict(raw_value=original, basis='user_report', evidence_ids=[evidence_id], applicability='matched')
    if raw in ('', '모름', '미확인'):
        return Quantity(**base)
    m = re.fullmatch(r'(?:(포대)\s*)?([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(kg|t|g|L|l|m³|m3|개|포대)', raw)
    if not m:
        raise ClarificationRequired('단일 수치·단위와 실제 내용물의 측정량인지 확인해 주세요.')
    prefix, number, unit = m.groups()
    value = Decimal(number)
    if value <= 0:
        raise ClarificationRequired('음수 또는 0인 이번 처리량을 확인해 주세요.')
    if prefix and unit != '개':
        raise ClarificationRequired('포대 수의 단위를 확인해 주세요.')
    unit = '포대' if prefix else {'l': 'L', 'm³': 'm3'}.get(unit, unit)
    if UNITS[unit] == 'count' and value != value.to_integral_value():
        raise ClarificationRequired('개수는 정수로 확인해 주세요.')
    rule = None
    if unit in ('t', 'g'):
        factor = Decimal('1000') if unit == 't' else Decimal('.001')
        rule = f'UNIT-1:{unit}->kg:*{factor}'
        value *= factor
        unit = 'kg'
    return Quantity(value=value, unit=unit, dimension=UNITS[unit], status='conditional', conversion_rule=rule, **base)


def convert_quantity(q: Quantity, target: str) -> Decimal:
    if q.value is None or q.unit is None:
        raise ClarificationRequired('수량 미확인')
    if target not in UNITS or UNITS[target] != q.dimension:
        raise ClarificationRequired('차원 간 변환 미지원: 밀도 등 별도 근거가 필요합니다.')
    if q.dimension == 'count' and q.unit != target:
        raise ClarificationRequired('포대와 개를 같은 대상으로 가정하지 않습니다.')
    factors = {'kg': Decimal(1), 't': Decimal(1000), 'g': Decimal('.001'), 'L': Decimal(1),
               'm3': Decimal(1000), '개': Decimal(1), '포대': Decimal(1)}
    return q.value * factors[q.unit] / factors[target]


def hazard_response(selected: list[str]) -> dict:
    if not selected:
        return dict(value=None, raw_value='', warning='UNCHECKED')
    if len(selected) != 1 or selected[0] not in ('있음', '없음', '모름'):
        return dict(value=None, raw_value=' / '.join(selected), warning='INVALID_OR_MULTIPLE_CHECKS')
    return dict(value={'있음': True, '없음': False, '모름': None}[selected[0]],
                raw_value=selected[0], warning='USER_REPORT_NOT_ANALYSIS')


def evaluate_conditions(results: list[str]) -> str:
    if not results or any(x not in ('true', 'false', 'unknown') for x in results):
        raise ValueError('nonempty tri-state results required')
    return 'false' if 'false' in results else ('unknown' if 'unknown' in results else 'true')
