"""Curated official paths. A homepage, a directory and a submission entrance are distinct."""
from copy import deepcopy

ROUTES={
 'authority':dict(title='관할 행정기관 서면 질의',provider='배출 현장 관할 폐기물 담당기관 (접수 시 소관 확인)',
   discovery_url='https://www.epeople.go.kr/nep/pttn/pttnOnln/PttnOnlnContent.npaid',
   source_url='https://www.epeople.go.kr/nep/pttn/pttnOnln/PttnOnlnWrtnInfo.npaid',
   submission_url='https://www.epeople.go.kr/nep/pttn/gnrlPttn/PttnRqstWrtnInfo.paid',
   procedure_url='https://www.epeople.go.kr/nep/gdnc/PttnUtilGdncContent.npaid',
   menu=['국민신문고','민원','일반민원','민원신청','로그인/본인인증 후 작성'],
   verified_on='2026-11-15',browser_scope='민원상담 안내 → 민원상담 신청 → 민원신청 연결을 눌러 로그인 경계 확인. 로그인 후 양식/제출 미수행.',
   known_status='login_required',login_path='/nep/crtf/userLogn.npaid',
   target_markers=['로그인'],
   fallback='소관기관을 모르면 민원상담 안내 또는 정부민원안내콜센터 110으로 담당기관·절차를 먼저 문의.',
   preparation=['사용자가 검토한 질의문','해당 현장 주소·발생공정·폐기물 설명','보유한 신고자료/사진/성적서 중 관련 자료만','신청자 연락처·통지방식(사용자 확인)'],
   steps=['현장 관할·문의 목적 확인; 소관이 불분명하면 상담으로 문의',
          '공식 민원신청 입구에서 로그인/본인인증 (사용자 직접 수행)',
          '성명·주소·연락처·통지방식·제목·내용 작성; 생성한 초안은 검토 후 옮기기',
          '유사사례 검토 → 처리기관 선택 → 관련 자료 첨부 → 사용자가 제출',
          '접수번호 기록; 소관기관의 담당부서 배정 및 보완 요청 대응',
          '선택한 통지방식으로 처리결과 수령; 회신 원문을 같은 폐기물 건에 첨부',
          '회신의 현장·품목·조건·날짜와 필요한 값들을 대조; 없는 값만 추가 문의'],
   limits=['국민신문고는 접수 통로이며 법적 분류를 결정하는 기관이 아닙니다.','로그인 이후 화면·처리기간·개별 수신기관은 미확인입니다.','기관 회신을 자동으로 selected_code에 채택하지 않습니다.']),
 'lab':dict(title='시험 항목 상담 및 의뢰',provider='한국화학융합시험연구원(KTR) 접수 경로 예시; 지점·항목 수행 가능 여부 확인 필요',
   discovery_url='https://www.nier.go.kr/common/kor/content/comContsDetail.do?contsNo=191&menuNo=13004',
   source_url='https://www.ktr.or.kr/customer-service/reception-request/contentsid/1964/index.do',
   submission_url='https://cs.ktr.or.kr/',
   procedure_url='https://www.ktr.or.kr/customer-service/reception-request/contentsid/358/index.do',
   menu=['KTR','시험접수','접수 · 의뢰안내','온라인 접수'],
   verified_on='2026-11-15',browser_scope='공식 온라인 접수 링크 및 도착 페이지 확인: 임시 점검 중. 택배 접수 절차와 서식 안내 페이지 확인.',
   known_status='maintenance',login_path=None,target_markers=['온라인','접수'],
   fallback='온라인은 점검 중입니다. 공식 택배/방문 접수 안내에서 담당 부서와 먼저 상담하세요. 현재 수용 여부·지정분야·시험방법·시료 조건을 확인하기 전에는 발송하지 마세요.',
   preparation=['시험 의뢰 준비 초안 (기관 공식 의뢰서와 구분)','기관의 의뢰(택배)접수신청서','개인정보 수집·이용 및 제3자 제공 동의서(사용자 직접 검토·서명)',
                '현장·폐기물·시료 식별, 원하는 시험항목·단위·시험법/기준','기관이 요구한 사업자 정보 및 관련 자료만','기관과 협의한 채취·보관·포장·운송 조건의 시료'],
   steps=['국립환경과학원 분석기관지정 → 검사기관에서 폐기물분석 전문기관 선택; 기관·지점·지정분야 확인',
          '선택 기관에 필요한 항목만 제시; 시험 가능 여부, 법적 목적에 맞는 방법·시료 조건·비용·기간·한 성적서 발급 여부 상담',
          '온라인 경로 정상 여부 재확인. 점검 중이면 공식 방문/택배 절차로 협의 (임의 이메일/주소 생성 금지)',
          '기관의 신청서·동의서에 검토한 정보를 옮겨 작성; 서명·개인정보 동의는 사용자 직접 수행',
          '수용 가능한 시료와 발송 지점을 기관에 확인한 뒤 제출/발송; 접수번호 보관',
          '기관이 작성·안내한 의뢰시험신청서와 수수료 확인 → 사용자가 결제 → 시험 진행',
          '시험 완료 통지 후 성적서 수령 방식(직접/택배/요청한 이메일 등) 확인하여 수령',
          '성적서를 같은 건에 첨부; 시료번호·채취일·시험일·단위·시험법·건조/습윤 기준을 대조; 빠진 항목만 보완'],
   limits=['확인된 NIER 화면에서 KTR 영남은 일반분야, KTR 과천은 PCBs로 구분되어 있습니다(목록 갱신일 2026-07-22).','이 등록현황만으로 발열량·수분·염소 등 모든 항목 수행 가능성을 보장하지 않습니다.','실제 접수 URL은 확인했지만 현재 서비스 가동은 확인되지 않았고 점검 안내가 보였습니다.','위험 시료를 임의 채취·포장·택배 발송하지 마세요.']),
}


def route_card(kind):
    if kind not in ROUTES:raise ValueError('Unknown route')
    return deepcopy(ROUTES[kind])
