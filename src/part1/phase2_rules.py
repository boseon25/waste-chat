"""Small explicit question rules. No LLM confidence or legal auto-classification."""
import re
from datetime import date
from zoneinfo import ZoneInfo
from datetime import datetime
from normalization import normalize_quantity, convert_quantity
from intake_parser import uid
from decimal import Decimal
from internal_records import records

VERSION='phase2-0.3.0'
UNKNOWN={'모름','미확인','자료 없음','자료없음','잘 모르겠습니다','잘 모르겠어요','자료가 없습니다','자료가 없어요'}
FIELDS={
 'site_address':dict(priority=0,label='배출·보관 현장',kind='text',scope='all',question='이 폐기물의 실제 배출·보관 현장을 주소와 동/장소로 알려 주세요. 공통 현장과 다르면 구분해 주세요.'),
 'name':dict(priority=0,label='폐기물 이름',kind='text',scope='all',question='이 표가 가리키는 폐기물의 현장 이름을 알려 주세요. 다른 폐기물과 구분할 수 있게 적어 주세요.'),
 'process_description':dict(priority=20,label='발생 작업',kind='text',scope='classification',question='이 폐기물은 어떤 작업에서 발생했나요? 사용 원료·약품을 알면 함께 알려 주세요.'),
 'physical_state':dict(priority=20,label='형태',kind='text',scope='classification',question='현재 형태를 알려 주세요. 예: 마른 고체 조각, 액체, 진흙 같은 상태.'),
 'quantity':dict(priority=15,label='이번 처리량',kind='quantity',scope='all',question='이번에 처리할 실제 내용물의 양을 단위와 함께 알려 주세요. 예: 200kg, 100L, 포대 10개. 용기 최대 용량이나 월 발생량은 제외합니다.'),
 'quantity.method':dict(priority=40,label='측정 방법',kind='text',scope='document:lot-quantity',question='이번 처리량을 실제로 어떻게 재거나 셌나요? 무게라면 용기 무게를 제외했는지도 알려 주세요.'),
 'quantity.measured_at':dict(priority=40,label='실제 측정일',kind='date',scope='document:lot-quantity',question='이번 처리량을 실제로 측정한 날짜를 YYYY-MM-DD로 알려 주세요. 작성일이나 업로드일과 다를 수 있습니다.'),
 'mixture_type':dict(priority=25,label='혼합 형태',kind='mixture',scope='classification',question='단일 재질인가요, 서로 떨어지는 여러 물건이 섞였나요, 코팅·접착 등으로 붙은 복합재인가요? 단일 재질 / 따로 섞임 / 붙어 있음 중 하나를 골라 주세요.'),
 'hazard_declaration':dict(priority=25,label='유해물질 포함 진술',kind='hazard',scope='classification',question='유해물질 포함에 대해 현재 아는 내용을 있음 / 없음 / 모름 중 하나로 알려 주세요. 없음도 사용자 진술일 뿐 분석 검증은 아닙니다.'),
 'components':dict(priority=30,label='재질명',kind='text',scope='classification',question='단일 재질의 이름 또는 제품 라벨 문구를 알고 있나요? 확인할 수 없으면 모름으로 남깁니다. 구성비는 추정하지 않습니다.'),
 'separability':dict(priority=35,label='분리 가능성',kind='boolean',scope='route',question='선택 경로 검토를 위해 묻습니다. 현장 작업으로 서로 분리할 수 있나요? 가능 / 불가능 / 모름 중 하나를 답하고 작업 기록은 추후 요청 시 제출하세요.'),
 'classification_evidence':dict(priority=50,label='분류 근거자료',kind='evidence',scope='classification',topic='classification',question='같은 현장·품목의 기존 신고자료나 현재 건 적용 조건이 명시된 관할기관 회신이 있나요? 번호를 직접 확정하지 않고 자료 요청으로 연결합니다.'),
 'hazard_evidence':dict(priority=45,label='공정·오염 관련 확인자료',kind='evidence',scope='classification',topic='hazard',question='공정·물질·오염 관찰에 대한 구체적인 원문이 있어 확인합니다. 원료/약품 자료나 해당 시료 분석자료가 있나요? 지정폐기물 해당 여부를 단정하지 않습니다.'),
}


def unknown(text):
    return text.strip() in UNKNOWN


def normalize(field, raw, today=None):
    if field not in FIELDS or FIELDS[field]['kind']=='evidence':
        raise ValueError('이 항목은 사용자 값 입력이 아니라 증빙 요청으로 연결해야 합니다.')
    text=raw.strip()
    if not text or len(text)>2000:
        raise ValueError('답변은 1~2000자로 입력하세요.')
    if unknown(text):return None
    kind=FIELDS[field]['kind']
    if kind=='date':
        try:d=date.fromisoformat(text)
        except ValueError:raise ValueError('날짜는 YYYY-MM-DD 또는 모름으로 입력하세요.')
        if d.isoformat()!=text:raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
        current=today or datetime.now(ZoneInfo('Asia/Seoul')).date()
        if d>current:raise ValueError('실제 측정일은 미래 날짜일 수 없습니다.')
        return text
    if kind=='quantity':
        q=normalize_quantity(text,'phase2-proposed')
        return dict(value=str(q.value),unit=q.unit,dimension=q.dimension,conversion_rule=q.conversion_rule)
    if kind=='hazard':
        if text not in ('있음','없음'):raise ValueError('있음 / 없음 / 모름 중 하나로 입력하세요.')
        return text=='있음'
    if kind=='mixture':
        mapping={'단일 재질':'single','single':'single','따로 섞임':'mixed','mixed':'mixed','붙어 있음':'composite','composite':'composite'}
        if text not in mapping:raise ValueError('단일 재질 / 따로 섞임 / 붙어 있음 / 모름 중 하나로 입력하세요.')
        return mapping[text]
    if kind=='boolean':
        if text not in ('가능','불가능','예','아니요'):raise ValueError('가능 / 불가능 / 모름 중 하나로 입력하세요.')
        return text in ('가능','예')
    if re.fullmatch(r'(둘 다|둘다|모두|전부|위와 같음|같음)',text):
        raise ValueError('짧은 공통 답변으로 여러 건을 갱신하지 않습니다. 해당 폐기물의 값을 구체적으로 적어 주세요.')
    if field in ('site_address','name') and ('또는' in text or text in ('미정','불명확','확인 필요')):
        raise ValueError('대상이 하나로 식별되지 않습니다. 한 현장/폐기물로 구분하거나 모름으로 답하세요.')
    if len(text)<2:raise ValueError('구체적인 값을 두 글자 이상 적어 주세요.')
    return text


def field_value(case,field,sites):
    if field=='site_address':return sites.get(case['site_id'],{}).get('address',{}).get('value')
    if field=='components':return [c['name']['value'] for c in case['components']]
    if field=='quantity':return {k:case['quantity'][k] for k in ('value','unit','dimension')}
    obj=case
    for key in field.split('.'):obj=obj[key]
    return obj.get('value')


def fact_missing(case,field,sites):
    value=field_value(case,field,sites)
    if field=='quantity':return value['value'] is None
    if field in ('site_address','name') and isinstance(value,str) and ('또는' in value or value.strip() in ('미정','불명확','확인 필요')):return True
    return value is None or value==[] or (isinstance(value,str) and unknown(value))


def raw_unknown(case,field):
    if field=='mixture_type' and not any('MIXTURE_UNCHECKED_OR_MULTIPLE' in x for x in case['limitations']):
        if any(a['field_path'].endswith('_mixture_2') for a in case['assertions']):return True
    if field in ('site_address','components'):return False
    obj=case
    for key in field.split('.'):obj=obj[key]
    return isinstance(obj,dict) and unknown(obj.get('raw_value') or '')


def identity_blocked(case,sites):
    return fact_missing(case,'site_address',sites) or fact_missing(case,'name',sites)


def dependency(case,field,route=None):
    # Changed quantity/identity creates a new context, not an endless retry of an unavailable response.
    if field in ('site_address','name'):return 'initial-target'
    identity=f"{case['site_id']}|{case['name']['value']}"
    if field.startswith('quantity.'):
        q=case['quantity'];amount=str(Decimal(q['value']).normalize()) if q['value'] is not None else None
        identity+=f"|{amount}|{q['unit']}|"+','.join(q['evidence_ids'])
    if field in ('components','separability','classification_evidence'):
        identity+='|mixture:'+str(case['mixture_type']['value'])+'|'+','.join(case['mixture_type']['evidence_ids'])
    if field=='separability':identity+='|'+str(route)
    if field in ('classification_evidence','hazard_evidence'):
        identity+='|process:'+str(case['process_description']['value'])+'|state:'+str(case['physical_state']['value'])
        identity+='|hazard:'+str(case['hazard_declaration']['value'])
        identity+='|'+','.join(e for f in ('process_description','physical_state','hazard_declaration') for e in case[f]['evidence_ids'])
    return uid(identity)


def request_id(case_id,field,context):return uid(f'{VERSION}:{case_id}:{field}:{context}')


def quantity_conflict(case):
    # Only an explicitly labelled single measured amount: do not parse arbitrary prose or tare values.
    raw=case['quantity']['method'].get('value') or ''
    match=re.fullmatch(r'실제\s*측정량\s*[:：]\s*(.+)',raw.strip())
    if not match or case['quantity']['value'] is None:return False
    try:other=normalize_quantity(match.group(1),'comparison-only')
    except ValueError:return False
    q=case['quantity']
    if other.value is None:return False
    if other.dimension!=q['dimension']:return True
    try:return convert_quantity(other,q['unit'])!=Decimal(q['value'])
    except ValueError:return True


def candidates(case,sites,route=None):
    fields=[]
    if identity_blocked(case,sites):
        for f in ('site_address','name'):
            if fact_missing(case,f,sites) and not raw_unknown(case,f):fields.append(f)
        return fields
    for f in ('process_description','physical_state','quantity','mixture_type','hazard_declaration'):
        if fact_missing(case,f,sites) and not raw_unknown(case,f):fields.append(f)
    if quantity_conflict(case) and 'quantity' not in fields:fields.append('quantity')
    if not fact_missing(case,'quantity',sites) and not quantity_conflict(case):
        for f in ('quantity.method','quantity.measured_at'):
            if fact_missing(case,f,sites) and not raw_unknown(case,f):fields.append(f)
    if case['mixture_type']['value']=='single' and not case['components']:fields.append('components')
    if route and case['mixture_type']['value'] in ('mixed','composite','multiple_unspecified') and case['separability']['value'] is None:
        fields.append('separability')
    if (case['classification']['selected_code'] is None and case['classification']['reported_code']['value'] is None
            and records(case)['history']['status'] not in ('new', 'none')):
        fields.append('classification_evidence')
    observation=' '.join((case[f].get('value') or '') for f in ('process_description','physical_state'))
    if case['hazard_declaration']['value'] is True or any(x in observation for x in ('기름막','유분','유기용제')):
        fields.append('hazard_evidence')
    return fields
