"""Curated acquisition routes; no generated URLs or legal determinations."""
from datetime import date

SOURCES = {
 'law': dict(url='https://www.law.go.kr/', checked_at='2026-11-11',
             checked_scope='공식 홈페이지와 법령·별표/서식 검색 메뉴 확인',
             limitation='개별 폐기물 적용 조항·시행본·행정기관 회신은 미검토'),
 'nier': dict(url='https://www.nier.go.kr/', checked_at='2026-11-11',
              checked_scope='공식 홈페이지 법령정보 > 분석기관지정 메뉴 확인',
              limitation='최신 지정기관 목록·현재 접수·시험항목·비용은 개별 재확인 필요'),
 'msds': dict(url='https://msds.kosha.or.kr/', checked_at='2026-11-11',
              checked_scope='공식 홈페이지 MSDS 제공·정보검색 메뉴 확인',
              limitation='로그인 후 자료 제공·개별 제품 자료 발급은 확인하지 않음'),
}

CATALOG = {
 'classification': dict(title='폐기물 분류번호', fields=['classification.reported_code','classification.selected_code'],
   reason='현재 건에 적용되는 코드와 과거 신고 기록을 구분하기 위해 필요합니다.',
   question='같은 현장·품목의 기존 신고자료 또는 현재 건의 적용 조건이 명시된 관할기관 회신이 있나요?',
   where=['사내 환경·안전 담당자에게 신고자료의 해당 품목 행 요청', '자료가 없거나 적용이 불명확하면 배출 현장 관할 시·군·구 폐기물 담당 부서에 문의'],
   prepare=['현장 주소','품목·발생 공정·원료·성상','기존 신고일/번호(알면)','보유 분석자료'],
   alternative='관할 부서의 접수/처리기관과 문의 절차부터 확인. 회신의 대상·조건·날짜를 보존하고 selected_code는 null 유지.',
   caution='신고자료 번호는 reported_code 후보. 현재 건 적용 검토 없이 selected_code로 복사하지 않음. 재질별 코드·AI 확신도 추정 금지.', sources=['law']),
 'hazard': dict(title='지정폐기물 의심·해당 여부', fields=['hazardous_suspect','designated_waste_status'],
   reason='구체적인 의심 근거와 지정 여부 확인 결과를 분리하기 위해 필요합니다.',
   question='공정·사용 약품·물질과 관련한 구체적 근거 또는 현재 시료의 분석·확인자료가 있나요?',
   where=['사내 공정/안전 담당자에게 원료·약품 정보와 기존 시험자료 요청','제품 공급자에게 해당 제품 MSDS 요청','필요 항목을 정한 후 폐기물분석전문기관 지정현황과 분석 가능 항목·시료 채취 절차 확인'],
   prepare=['현재 현장·공정·원료','제품명·제조사','시료번호·채취일','필요 판단 항목'],
   alternative='관할 담당 부서/분석기관에 필요한 항목과 채취 방법을 상담. 불필요한 일괄 분석 요구 금지.',
   caution='MSDS는 사용 후 폐기물 시험을 대체하지 않음. 모름을 false로 바꾸지 않고 의심과 해당 여부 모두 미확인 유지.', sources=['nier','msds']),
 'monthly_generation': dict(title='최근 3개월 월별 발생량', fields=['monthly_generation'],
   reason='이번 처리량·반출량과 월별 실제 발생량을 구분하기 위해 필요합니다.',
   question='지정한 3개월의 같은 현장·품목 발생대장에 연월·발생량·단위가 있나요?',
   where=['현장 환경/생산 담당자의 발생대장·생산 및 측정 기록 확인'],
   prepare=['대상 연월','현장·품목 식별','발생량·단위','측정/기록 근거'],
   alternative='기록 없는 월은 null로 남김. 현장 기록 담당자에게 보관본 확인; 공개 사이트에서 회사 기록을 찾을 수 있다고 안내하지 않음.',
   caution='반출량·이번 처리량으로 대체 금지. 확인된 질량만 t로 변환; L→t 추정 금지.', sources=[]),
 'composition': dict(title='구성비·이물질·분리 가능성', fields=['components','visible_contamination','separability'],
   reason='성분 시험값과 선별 작업으로 확인한 사실을 구분하기 위해 필요합니다.',
   question='현재 시료의 구성비 분석서 또는 이물질·분리 가능성을 확인한 작업/선별 기록이 있나요?',
   where=['기존 시험 의뢰 담당자/발급기관에서 시료별 분석서 확보','현장 선별 담당자에게 작업·분리 결과 기록 요청'],
   prepare=['시료·채취일','구성비의 단위/기준','이물질 종류','선별 방법·결과·작업일'],
   alternative='필요한 시험과 현장 작업 확인을 구분. 없는 항목은 null, 사진은 관찰 기록으로만 보존.',
   caution='미확인 구성비를 100으로 맞추지 않음. 사진만으로 성분·분리 가능성 확정 금지.', sources=['nier']),
 'treatment': dict(title='기존 처리 방식', fields=['previous_treatment'],
   reason='신고된 처리 계획과 실제 처리 이력을 구분하기 위해 필요합니다.',
   question='해당 품목의 기존 신고자료나 실제 처리기록에 처리코드·방법이 있나요?',
   where=['사내 환경/위탁처리 담당자에게 해당 품목 신고자료·처리기록 요청','기존 수탁업체에 해당 거래·품목 기록 제공 가능 여부 문의'],
   prepare=['품목·현장','처리 시점','수탁업체·기록 식별','계획/실행 여부'],
   alternative='자료가 없으면 처리코드 미확인. 계획만 있으면 계획임을 표시.',
   caution='계획을 실제 이력으로 바꾸지 않음. 처리코드 추정 금지.', sources=[]),
 'fuel_properties': dict(title='가연성·연료 활용 시험 특성', fields=['fuel_properties','combustible'],
   reason='파트 2의 특정 경로 조건과 필요한 시험 항목을 대조하기 위해 필요합니다.',
   question='해당 경로가 요구하는 항목의 시험자료에 값·단위·시료·시험일이 있나요?',
   where=['사내 제품/폐기물 시험 담당자 또는 기존 발급기관에 성적서 요청','경로별 필요 항목·시험방법을 정한 뒤 기관에 수행 가능 여부와 시료 준비 상담'],
   prepare=['검토 경로와 조건 근거','필요 항목(예: 발열량·수분·염소)','단위·시험법','시료·시험일·건조/습윤 기준'],
   alternative='필요 시험값만 미확인으로 표시하고 해당 경로만 보류. 임의 시험 패키지 요구 금지.',
   caution='combustible은 정의·근거 합의 전 비움. 폐기물/가공제품 시료를 혼동하지 않음.', sources=['nier']),
 'item_conditions': dict(title='품목별 조건·순환자원 검토', fields=['item_conditions'],
   reason='품목명 대신 실제 조건과 증빙으로 검토하기 위해 필요합니다.',
   question='검토 조건에 해당하는 모델·공정·재질·등록·보관·혼합·활용 방법의 자료가 있나요?',
   where=['제품/구매 부서: 모델·재질 사양서','생산/현장 부서: 공정·보관·혼합·활용 기록','등록증 보관 부서 또는 해당 등록 발급기관: 보유본/사본 절차 확인','해당 법령·고시와 담당기관의 조건별 적용 확인'],
   prepare=['선택한 품목과 적용 조건','아이씨트레이 모델/사용 공정 또는 비철금속 재질 등 해당 정보','도정업 등록 등 필요한 증빙','보관·혼합·활용 방법'],
   alternative='확인/미확인 조건을 나눠 기록하고 해당 판단만 보류.',
   caution='품목명만으로 순환자원 해당 여부 확정 금지. 필요 없는 등록증 일괄 요구 금지.', sources=['law']),
 'publication': dict(title='확정본 버전', fields=['case_version'],
   reason='아직 실제 발행 기능이 없음을 명확히 하기 위한 안내입니다.',
   question='발행 기능 구현 전에는 확정본 버전을 사용자에게 요청하지 않습니다.',
   where=['현재는 draft_revision으로 답변·정정만 추적'], prepare=[],
   alternative='case_version=0은 기존 모델의 초안 표식이며 발행 번호가 아님.',
   caution='사용자가 제공할 값이 아니므로 보완 요청 생성 불가.', sources=[]),
}


def months_before(as_of):
    d = date.fromisoformat(as_of)
    if d.isoformat() != as_of:
        raise ValueError('as-of must be YYYY-MM-DD')
    index = d.year * 12 + d.month - 1
    return [f'{n // 12:04d}-{n % 12 + 1:02d}' for n in range(index - 3, index)]


def card(topic):
    if topic not in CATALOG:
        raise ValueError('Unknown guidance topic')
    c = dict(CATALOG[topic], guidance_id='guidance-023-' + topic)
    c['official_routes'] = [dict(source_id=k, **SOURCES[k]) for k in c.pop('sources')]
    return c
