# 계약 결정 기록 — 합의 전 기준본

설계도 7–9, 11–13쪽을 구현한 제안입니다. 파트 2·3 담당자의 합의 없이 최종 계약으로 고정되었다고 간주하지 않습니다.

| 항목 | 현재 제안 | 합의/후속 구현 |
|---|---|---|
| schema_version | 2.0 | 수신 측 검토 후 릴리스 |
| case_version | 초안 0, 최초 발행 1 | 수정에는 draft_revision만 증가 |
| 소수 | Decimal, JSON 문자열 출력 | 수신 측도 부동소수 누적 금지 |
| Fact | 값/원문/상태/근거/확인수준/적용성 분리 | confirmed가 법적 확정은 아님 |
| 측정일/방법/순중량 | quantity 내부 Fact로 확장 | 설계 예시의 스칼라/null보다 상세하므로 수신 측 합의 |
| 혼합 | single/mixed/composite/multiple_unspecified | ‘여러 재질’만으로 혼합/복합재 확정 금지 |
| 미확인 비율 | null | 합계 임의 100 금지 |
| 현장 | site_id nullable, 상속 여부와 근거 ID | 임시 접수 허용 |
| 분류 | 후보와 규칙 참조 구조 | 실제 rule registry·법령 원문 검수 M4 |
| readiness | 기본 not_assessed | part2 경로·매칭, part3 서류 평가 |
| Handoff | snapshot+event+reviewer+비공개 파일 ID | 권한·불변성·발행 게이트 M2 |
| 충돌 | 서로 다른 주장 보존 | M1은 열린 충돌 전부 전달 거절, 후속 범위별 세분화 |
| 질문 | open/answered/resolved/unavailable | 서비스 상태 전이·중복 억제 M2 |
| 분석 결과 | 불검출/미정량은 value=null | 함량/용출, 건조/습윤 비교 엔진 미구현 |
| 추출 후보 | ExtractionCandidate에 accepted/version 필드 없음 | API 쓰기 권한 분리 M2 |

Fact.schema.json은 Fact<string> 구조 예시이며 WasteCase.schema.json에는 필드별 실제 타입이 포함됩니다. JSON Schema는 Python의 모든 교차 필드 검증을 포함하지 않습니다.

Company/Site는 별도 엔터티입니다. 실제 전달 시 회사·현장 조회 권한과 근거 연결을 제공해야 하며 문자열 ID가 업체 권한을 증명하지 않습니다. Handoff 모델만으로 개인정보·업체 필수정보가 충족되었다고 판단하지 않습니다.

M1에서 확정하지 않은 사항: 개별 폐기물 분류·지정 여부·재활용 적격성, 현재 lot에 대한 과거 분석 적용성, 운영 계정/비밀키/보관기간, 실제 검수자·시행일·규칙 버전, 외부 제출 승인.
