"""Explicit required values. Never demand every possible test from every user."""
import json
from decimal import Decimal,InvalidOperation
import re
FIELDS={
 'classification.reported_code':dict(label='기관 회신의 분류번호(검토 후보)',kind='authority',type='code'),
 'classification.applicability':dict(label='현재 건 적용 여부',kind='authority',type='text'),
 'classification.conditions':dict(label='적용 조건·추가 확인사항',kind='authority',type='text'),
 'designated_waste_status':dict(label='지정폐기물 해당 여부에 대한 기관의 확인 내용',kind='authority',type='text'),
 'analysis.heating_value':dict(label='발열량',kind='lab',type='number',units=['MJ/kg','kcal/kg']),
 'analysis.moisture':dict(label='수분',kind='lab',type='percent',units=['%']),
 'analysis.chlorine':dict(label='염소',kind='lab',type='number',units=['%','mg/kg']),
 'analysis.heating_value_low':dict(label='저위 발열량',kind='lab',type='number',units=['MJ/kg','kcal/kg']),
 'analysis.heating_value_high':dict(label='고위 발열량',kind='lab',type='number',units=['MJ/kg','kcal/kg']),
 'hazard.analysis_results':dict(label='유해물질별 분석값과 단위',kind='hazard',type='analytes'),
 'hazard.designation_basis':dict(label='지정폐기물 여부와 적용 기준 대조',kind='hazard',type='text'),
 'circular_registration.reference':dict(label='지정고시 등록확인서 번호 또는 등록 식별정보',kind='circular_registration',type='text'),
 'circular_registration.scope':dict(label='등록 품목·사업장·순환이용 용도와 조건',kind='circular_registration',type='text'),
 'circular_recognition.reference':dict(label='순환자원 인정번호 또는 인정서 식별정보',kind='circular_recognition',type='text'),
 'circular_recognition.scope':dict(label='인정 품목·사업장·용도·조건 및 유효기간',kind='circular_recognition',type='text'),

}
DEFAULT_NEEDS=['classification.reported_code','classification.applicability','classification.conditions']
UNKNOWN={'모름','자료 없음','미확인','확인 불가','불검출','ND','N.D.'}

def normalize(field,value,unit=None):
    if field not in FIELDS:raise ValueError('지원하지 않는 필요 항목')
    text=value.strip()
    if not text or len(text)>5000:raise ValueError('값은 1~5000자로 기재하세요.')
    if text in UNKNOWN:return None
    spec=FIELDS[field]
    if spec['type'] == 'analytes':
        analytes = []
        for line in text.splitlines():
            name, delimiter, result = line.partition('|')
            value, second_delimiter, unit = result.partition('|')
            name, value, unit = name.strip(), value.strip(), unit.strip()
            if not all((delimiter, second_delimiter, name, value, unit)) or '|' in unit:
                raise ValueError('결과서 항목을 한 줄에 물질명 | 수치 또는 불검출 | 단위로 입력하세요.')
            if value not in UNKNOWN:
                try:
                    number = Decimal(value.lstrip('<≤').strip())
                except InvalidOperation:
                    raise ValueError('분석결과는 수치, 검출한계 표기 또는 불검출로 작성하세요.') from None
                if not number.is_finite() or number < 0:
                    raise ValueError('분석 수치를 확인하세요.')
            analytes.append(dict(analyte=name, result=value, unit=unit))
        return json.dumps(analytes, ensure_ascii=False)
    if spec['type'] in ('number','percent'):
        if unit not in spec['units']:raise ValueError('성적서의 단위를 확인하세요: '+', '.join(spec['units']))
        try:n=Decimal(text)
        except InvalidOperation:raise ValueError('시험값은 수치 또는 미확인/불검출로 기록하세요.')
        if not n.is_finite() or n<0 or ((spec['type']=='percent' or unit=='%') and n>100):raise ValueError('시험값 범위 오류')
        return str(n)
    if unit:raise ValueError('기관 회신 텍스트에 수치 단위를 지정하지 마세요.')
    if spec['type']=='code' and not re.fullmatch(r'\d{2}-\d{2}-\d{2}',text):raise ValueError('분류번호 표기(XX-XX-XX)를 원문과 대조하세요. 형식 검사는 법적 검증이 아닙니다.')
    if spec['type']=='text' and len(text)<2:raise ValueError('회신 내용을 구체적으로 기록하세요.')
    return text
