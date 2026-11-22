# 입력서 대조 및 매핑

원본 `폐기물 기초 정보 입력서 .docx`는 수정하지 않았습니다.
SHA-256: `83c3bf58c11bd63e0c980213c10380ac3cf34337eafa88d0dfb9324298809d05`

## 실물 구조

업체 표 1개, 폐기물 표 2개. 콘텐츠 컨트롤 47개 중 체크 컨트롤은 20개이며 모두 미선택입니다. 나머지는 `예:` placeholder가 남아 있습니다. 정상 결과는 실제 후보 0건입니다.

일반 `.text`에서는 콘텐츠 컨트롤 내부 값이 빠질 수 있으므로 XML의 sdtContent 및 checked를 직접 읽습니다. DOCX 출처는 페이지 대신 표·행·셀·태그·컨트롤 출현 위치를 사용합니다. 표/행/셀은 0부터 시작합니다.

작성 기준일은 이미 있지만 tag는 없습니다. 폐기물 1에는 별도 현장 행과 ‘재질(알면)’이 반영돼 있고, 폐기물 2에는 현장 행이 없으며 ‘섞인 재질(알면)’ 문구가 남아 있습니다. 양식 개정은 별도 승인 후 진행합니다.

## 매핑

| 입력 tag/항목 | 계약 필드 | 주의 |
|---|---|---|
| company_name | Company.name | 예시 제외 |
| business_number | Company.business_number | 형식과 실제 등록 사실 별도 |
| contact_name/contact_phone | Company.contact_name/contact_phone | 대표자와 혼동 금지 |
| site_address | Site.address | 등록 주소가 아닌 실제 현장 |
| 작성 기준일 | source_form_date | 측정일을 대신하지 않음 |
| 폐기물별 배출 현장 | site_id/site_inherited/site_source_evidence_ids | 공통 현장 상속 근거 기록 |
| name | name | 일상 명칭, 법적 코드 아님 |
| process | process_description | 발생 작업 원문 |
| quantity | quantity | 이번 처리량과 단위·차원 |
| quantity_evidence | evidence 후보 | 파일명이 첨부 사실은 아님 |
| appearance | physical_state 후보 | 외형으로 성분 확정 금지 |
| mixture_0 | single 후보 | 재질명은 별도 |
| mixture_1 | multiple_unspecified 후보 | mixed/composite 구분 질문 필요 |
| mixture_2 | null | 모름 원문 보존 |
| mixture_details | components 후보 | 비율 추정 금지 |
| contamination_0/1/2 | not_visible/partial/widespread | 겉으로 보이는 상태만 의미 |
| contamination_3 | null | 내부 오염 없음으로 해석 금지 |
| contamination_details | assertions/evidence | 물질 확정은 검증 후 |
| hazard_0/1/2 | true/false/null | 사용자 진술, 분석 검증 아님 |
| hazard_details | substances/analysis_results 후보 | 시료·방법·검출한계·적용성 확인 |
| notes | assertions/generation 후보 | 주기와 이번 처리량 분리 |
| photo_full/photo_close | 파일 연결 요청 후보 | 실제 파일·전체/근접·중복·건 연결 검사 |

## 탐색기 한계

현재 탐색기는 **후보만** 반환하며 모든 항목을 WasteCase에 자동 반영하지 않습니다. 수용된 건은 항상 0입니다. 체크 상태 오류·미선택·복수 선택은 경고합니다. 단순한 주요 셀의 컨트롤 삭제는 별도 검토 후보로 남기지만 임의 레이아웃 전체를 해석하지는 않습니다.

태그가 중복되어도 덮어쓰지 않습니다. 후보 UUID는 실행마다 새로 생성됩니다. 재업로드 멱등성, 파일 해시+추출기 버전 캐시, 영속 case/lot ID는 M2 저장 서비스가 담당합니다.
