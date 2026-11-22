"""Official sources and application paths for each document type."""
from copy import deepcopy

from route_monitor import status as monitored_status

NIER = 'https://www.nier.go.kr/front/kor/cmm/nier_law01.do?menuNo=13004'
TRIAL_FORM = ('https://www.gb.go.kr/Sub/open_contents/section/inhen/page.do'
              '?BD_CODE=inhen_form&B_LEVEL=0&B_NUM=260451901&B_STEP=260451900&Start=0&V_NUM=13'
              '&bdName=&cmd=2&dept_code=&dept_name=&key=0&mnu_uid=8221&p1=0&p2=0&tbbscode1=inhen_form&word=')
KTR_STEPS = 'https://www.ktr.or.kr/customer-service/reception-request/contentsid/358/index.do'
KTR_TESTS = 'https://m.ktr.or.kr/customer-service/communityid/faq/list.do?ctgryDpthId=336&ctgryId=336'
CIRCULAR_MANUAL = 'https://www.re.or.kr/board/viewNoticePage.do?sn=463'
CIRCULAR_LIST = 'https://www.re.or.kr/info/listRecyclableResourcesNoticeStatAPage.do'
CIRCULAR_GOV = 'https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=14800000570'
CIRCULAR_ENV = ('https://www.mcee.go.kr/gg/web/board/read.do'
                '?boardId=1879680&boardMasterId=228&condition.hideCate=1&menuId=2245')
LEGAL_CLASSIFICATION = 'https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1032502899'
CLASSIFICATION_TABLE = 'https://www.law.go.kr/LSW/flDownload.do?flSeq=171394723'
HAZARD_LAW = 'https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1028153351'
DATE = '2026-11-16'

KINDS = ('authority', 'hazard', 'lab', 'circular_registration', 'circular_recognition')
DOCUMENT_NAMES = {
    'authority': '관할기관의 서면 회신서',
    'hazard': '폐기물 분석결과서',
    'lab': '같은 시료의 시험성적서',
    'circular_registration': '순환자원 지정고시 등록확인서',
    'circular_recognition': '순환자원 인정서',
}
DRAFT_NAMES = {
    'authority': '기관 질의문',
    'hazard': '제출용 폐기물 분석 의뢰 준비문',
    'lab': '시험 의뢰 준비문',
    'circular_registration': '순환자원 지정고시 등록 준비문',
    'circular_recognition': '순환자원 인정 신청 준비문',
}
RECORD_INSTRUCTIONS = {
    'authority': '이 현장과 공정에 적용되는 분류번호, 그 근거와 적용 조건을 회신해 주세요. 판단이 보류되면 필요한 자료·시험항목과 소관 부서를 안내해 주세요.',
    'hazard': '제출용 분석결과서에 필요한 시험 범위와 시료 채취 주체·방법을 먼저 안내해 주세요. 결과에는 분석 항목, 수치 또는 검출 상태, 단위, 시험법 및 시료 식별자를 요청합니다.',
    'lab': '같은 시료에서 선택한 항목의 시험 가능 여부를 알려 주세요. 발열량의 고위·저위 구분, 수치 단위, 건조·습윤 기준과 채취·보관 조건을 먼저 확인해 주세요.',
    'circular_registration': '현행 고시 별표 1의 지정 대상과 별표 2의 용도·방법·기준을 현재 품목 및 사업장 자료에 대조하고 적합 증빙을 준비합니다.',
    'circular_recognition': '현 신청 대상과 활용 용도를 정리하고 공식 신청서에 옮길 자료를 점검합니다. 공정도·생산 및 관리계획·해당 시험결과와 거래 관련 자료를 확인합니다.',
}

_EXTRA = {
    'hazard': {
        'title': '제출용 폐기물 분석 의뢰',
        'provider': '지정 분야가 맞는 폐기물분석전문기관',
        'discovery_url': NIER, 'source_url': HAZARD_LAW, 'procedure_url': TRIAL_FORM,
        'submission_url': None,
        'menu': ['국립환경과학원', '법령정보', '분석기관지정', '폐기물분석 전문기관', '선택 기관 시험접수'],
        'preparation': ['시료·사업장 식별, 발생공정·원료·성상', '제출 목적과 필요한 법정 시험 범위',
                        '선택한 기관의 제출용 시험의뢰서', '기관이 지정한 채취·보관·운송 조건'],
        'steps': ['기관 목록에서 지정 분야와 기관 연락처를 확인합니다.',
                  '시료 종류와 제출 목적을 설명하고 개별 시험 범위 및 채취 주체·방법을 협의합니다.',
                  '기관 양식과 기관이 지정한 채취·접수 방식대로 제출합니다.',
                  '받은 폐기물 분석결과서에서 시료·시험법·단위·유해물질별 값을 확인해 이 건에 첨부합니다.',
                  '해당 시료의 분석값과 현행 지정폐기물 적용 기준을 대조한 근거를 기록합니다.'],
        'fallback': '목록의 전화번호로 현 지점의 지정 범위와 제출용 시료 접수 방법을 확인합니다.',
        'limits': ['경상북도 보건환경연구원 서식은 제출용과 참고용을 구분하는 예시입니다. 선택한 기관의 양식을 확인하세요.',
                   '시험 범위와 적용 기준은 폐기물 종류에 따라 달라집니다.'],
        'submission_note': '실제 접수 주소·방식은 선택한 시험기관에 확인합니다.',
        'extra_links': [('폐기물 분석결과서 공식 서식', 'https://www.law.go.kr/LSW/flDownload.do?flSeq=171395565')],
    },
    'circular_registration': {
        'title': '순환자원 지정고시 등록',
        'provider': '한국환경공단 순환자원정보센터',
        'discovery_url': CIRCULAR_LIST, 'source_url': CIRCULAR_MANUAL, 'procedure_url': CIRCULAR_ENV,
        'submission_url': 'https://www.re.or.kr/main.do',
        'menu': ['순환자원정보센터', '지정고시 등록 매뉴얼', '로그인', '해당 품목 및 정보 등록'],
        'preparation': ['지정고시 대상 품목과 사업장 정보', '해당 품목의 순환이용 용도·방법·기준 충족 자료'],
        'steps': ['현행 지정고시 별표 1의 품목과 별표 2의 용도·방법·기준을 확인합니다.',
                  '공식 공지의 등록 매뉴얼을 내려받고 센터에 로그인해 관련 정보를 등록합니다.',
                  '등록확인서를 출력하고 품목·사업장·용도 범위를 확인해 이 건에 첨부합니다.'],
        'fallback': '순환자원정보센터 1800-8830의 순환자원 인정·지정고시 안내로 문의합니다.',
        'limits': ['공개 등록 안내와 로그인 입구를 확인했습니다. 로그인 뒤 실제 입력·등록은 사용자가 진행합니다.'],
        'submission_note': '공식 시스템 로그인 입구입니다. 등록 화면과 제출은 로그인 후 확인합니다.',
    },
    'circular_recognition': {
        'title': '순환자원 개별 인정 신청',
        'provider': '사업장 관할 유역·지방환경청',
        'discovery_url': CIRCULAR_ENV, 'source_url': CIRCULAR_GOV, 'procedure_url': CIRCULAR_GOV,
        'submission_url': None,
        'menu': ['정부24', '순환자원 인정(재인정)신청', '신청 방법 및 절차', '관할 유역·지방환경청 방문 또는 우편'],
        'preparation': ['공식 순환자원 인정(재인정) 신청서', '생산 및 관리계획, 공정도, 해당 시험분석·활용·거래 증빙'],
        'steps': ['정부24의 신청서와 구비서류를 확인합니다.',
                  '사업장 주소를 기준으로 유역·지방환경청의 담당 부서와 접수 주소를 확인합니다.',
                  '공식 신청서와 해당 구비자료를 작성해 관할 기관에 방문 또는 우편으로 접수합니다.',
                  '심사 보완 요청에 응답하고 인정 시 발급받은 인정서를 이 건에 첨부합니다.'],
        'fallback': '접수 주소와 품목별 구비서류는 관할 환경청에 확인합니다.',
        'limits': ['인정 여부와 적용 범위는 심사를 거쳐 결정됩니다.'],
        'submission_note': '정부24 공개 안내의 신청 방법은 방문·우편입니다.',
    },
}


def status(kind, registry=None):
    if kind in ('authority', 'lab'):
        route = monitored_status(kind, registry)
        if kind == 'authority':
            route['extra_links'] = [('폐기물 종류별 세부분류 기준 (별표 4)', CLASSIFICATION_TABLE)]
        else:
            route['discovery_url'] = NIER
            route['extra_links'] = [('KTR 시험항목 안내', KTR_TESTS)]
        return route
    route = deepcopy(_EXTRA[kind])
    route.update(verified_on=DATE, last_checked_at=DATE, usable_submission_url=None,
                 monitor_result='공개 공식 안내 확인', browser_scope='공개 공식 페이지의 절차·양식 안내 확인')
    return route
