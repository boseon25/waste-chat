# 0.2.3 — 자료 확보 안내와 건별 보완 요청

## 이번 구현 범위

사용자가 올린 0.2.2 ZIP을 기준으로 작업했습니다. 원본 ZIP과 기존 측정일 question/answer/correct/history는 유지했습니다. 기준본 101개 테스트와 새 테스트 26개, 합계 127개가 통과했습니다.

추가한 것은 **자료 확보 안내 → 보완 요청 → 응답/원본 첨부 보관 → 정정 이력 → 검토 대기 상태 전달**입니다. 아직 자유 서술/첨부 분석서의 수치를 자동 추출·검증해 분류번호·지정 여부·월별 발생량 등 최종 필드에 반영하는 기능은 아닙니다.

## 주제 목록

| topic | 자료/확인 항목 |
|---|---|
| classification | 기존 신고자료의 해당 행, 현재 건 적용 조건이 명시된 기관 회신 |
| hazard | 구체적인 공정·물질 근거, 현재 시료 분석자료, 공급자 MSDS |
| monthly_generation | 명시한 3개월의 현장·품목별 발생대장 |
| composition | 구성비 분석서, 이물질·분리 작업/선별 기록 |
| treatment | 기존 처리 계획과 실제 이력을 구분한 기록 |
| fuel_properties | 특정 경로가 요구하는 시험 항목·단위·시료·시험일 |
| item_conditions | 모델·공정·재질·등록·보관·혼합·활용 조건별 증빙 |
| publication | 사용자 질문이 아닌 발행 기능 미구현 안내만 제공 |

질문을 만들기 전에 왜 필요한지와 보류할 범위를 지정합니다. 모든 자료를 일괄 요구하지 않습니다. 특히 연료 특성은 route:id가 필수이며 요청 목적에 해당 경로의 조건·필요 시험항목을 적어야 합니다. 조건의 진위는 이 버전에서 자동 판단하지 않습니다.

## 실행

기존 ingest/inspect로 입력서를 저장한 DB에서 실행합니다. CASE_ID와 REQUEST_ID는 실제 출력된 ID로 바꿉니다.

```bash
python db_intake.py guide
python db_intake.py guide classification
python db_intake.py supply-question --db output/my_check.sqlite3 --case CASE_ID --topic classification --purpose "현재 품목의 신고 코드와 현재 건 적용 조건 확인" --scope classification
python db_intake.py supply-answer --db output/my_check.sqlite3 --request REQUEST_ID "자료 없음"
python db_intake.py supply-list --db output/my_check.sqlite3
python db_intake.py supply-correct --db output/my_check.sqlite3 --request REQUEST_ID "기존 담당자가 보관본을 찾았습니다. 검토해 주세요." --file "기존신고자료.pdf"
python db_intake.py supply-history --db output/my_check.sqlite3 --request REQUEST_ID
python db_intake.py export --db output/my_check.sqlite3 --out output/review.json
python db_intake.py validate-json output/review.json
```

측정일의 기존 answer/correct/history와 구분하기 위해 자료 보완 명령에는 `supply-`를 붙였습니다. DB에 여러 제출이 있으면 `--submission`을 지정합니다.

처음부터 가상 자료로 전체 흐름을 실행하려면:

```bash
python demo_guidance.py
python -m unittest discover -s . -p "test*.py" -v
```

`demo_guidance.py`는 output/guidance_demo에 가상 입력서의 DB, 보완 요청, 자료 없음 응답과 정정·가상 첨부, JSON을 만듭니다. 네트워크 전송이나 자료 자동 발급은 하지 않습니다.

## 자료 없음과 재질문 억제

- `모름`, `자료 없음`은 unavailable. false나 0으로 변환하지 않습니다.
- 동일 건+주제+목적+보류 범위+대상 기간의 요청을 다시 만들어도 기존 요청/답변을 재사용합니다.
- supply-list는 기본적으로 open만 최대 3개 보여줍니다. unavailable/answered는 다시 요구하지 않습니다.
- 응답이 있으면 같은 요청의 첫 답변을 덮어쓸 수 없고 supply-correct를 사용합니다.
- 답변/정정이 같으면 중복 저장하지 않으며 초안 차수를 올리지 않습니다.
- 답변 도착은 answered이지 resolved가 아닙니다. 자료 없음이면 대안과 해당 보류 범위를 표시합니다.

## 월별 발생량 기간

```bash
python db_intake.py supply-question --db output/my_check.sqlite3 --case CASE_ID --topic monthly_generation --purpose "선택한 매칭의 월별 발생량 조건 확인" --scope matching:M1 --as-of 2026-11-11
```

기간 기준은 **기준일 직전 완료된 3개월**입니다. 위 예는 2026-08, 2026-09, 2026-10입니다. 당월을 포함하지 않으며 자동으로 오늘 날짜를 대입하지 않습니다. 이번 처리량·반출량을 월별 발생량으로 채우지 않습니다.

이 버전은 대상 연월을 요청 문구와 이력에 기록할 뿐, 답변 문장에서 월별 값/단위/기록을 자동 파싱하지 않습니다. 후속 검토 모듈에서 확인된 월별 레코드만 만들고, 없는 월은 null로 유지해야 합니다.

## 자료를 어디서 얻는가

각 안내 카드에는 필요 이유, 찾아볼 사내 담당자/기관, 준비할 정보, 없을 때 대안, 주의사항, 공식 경로의 확인 범위가 있습니다.

- 신고자료: 사내 환경·안전 담당자 → 배출 현장 관할 시·군·구 폐기물 담당 부서. 회신의 대상·조건·날짜 확인.
- 분석자료: 기존 시험 의뢰 담당자/발급기관 → 필요한 판단 항목을 먼저 정한 뒤 지정현황·분석 가능 항목·채취 절차 확인.
- MSDS: 제품명·제조사를 확인해 공급자/구매·안전 담당자에게 요청. 폐기물 분석 대체 불가.
- 발생대장: 현장 환경·생산 담당자의 보관 기록. 회사 기록을 공개 사이트에서 찾을 수 있다고 안내하지 않음.
- 선별·처리·품목 조건: 해당 작업/구매/생산/환경 담당자 또는 기록 발급기관. 계획/실행, 제품/폐기물 시료 구분.

## 공식 경로 확인 기록 — 2026-11-11

| 공식 URL | 확인 범위 | 확인하지 않은 것 |
|---|---|---|
| https://www.nier.go.kr/ | 홈페이지 법령정보 > 분석기관지정 메뉴 | 최신 기관 목록·현재 접수·항목·비용 |
| https://msds.kosha.or.kr/ | MSDS 제공·정보검색 메뉴 | 로그인 뒤 제공/발급 절차·개별 제품 자료 |
| https://www.law.go.kr/ | 법령과 별표·서식 검색 메뉴 | 개별 건의 적용 법령/시행본/분류번호 |

검색에서 나온 오래된 지정현황은 최신 목록으로 채택하지 않았습니다. 특정 기관 전화번호·세부 발급 URL을 생성하지 않습니다. 위 카드는 자동 발급·지정 여부·법률 판단을 약속하지 않습니다.

## DB·JSON 변화

기존 원문 테이블은 바꾸지 않습니다. 요청을 처음 만들 때 선택 테이블 guidance_requests, guidance_answers, guidance_files를 생성합니다. 기존 DB도 읽을 수 있습니다. 답변은 순번과 함께 append-only로 저장하고 첨부는 BLOB+파일명+해시로 보관합니다.

기존 JSON 묶음에 `guidance_cards`가 추가됩니다. 건별 information_requests에 목적·보류 범위·질문 상태·근거 ID, assertions에 보류된 원문 답변이 전달됩니다. 첨부는 Evidence.source_locator.kind=file, applicability=unknown입니다. 내용·시료·적용성 검토가 끝났다는 뜻이 아닙니다.

`draft_revision = 기존 측정일 답변/정정 수 + 해당 건의 추가 자료 답변/정정 수`입니다. 안내 조회·질문 생성은 사실 변경이 아니므로 차수를 올리지 않습니다. case_version=0은 기존 계약의 미발행 초안 표식입니다.

reported_code, selected_code와 hazardous_suspect/designated_waste_status 등 도메인 값의 자동 채택은 하지 않습니다. 후자의 새 도메인 필드 전체를 이번 버전에 완성한 것도 아닙니다. 해당 필드명은 수집 요청의 목표를 나타냅니다. 추가된 것은 보완 수집과 근거 보존 단계입니다.

## 제한·운영 전 주의

- 로컬 SQLite 검증용이며 인증·테넌트 권한·PostgreSQL·실제 발행은 아직 없습니다.
- 첨부는 1바이트~20MiB이며 지정 확장자만 허용합니다. 확장자 제한은 악성파일 탐지/내용 검증이 아닙니다. 파일을 실행하거나 자동 분석하지 않습니다.
- 실제 개인정보/회사 기록이 든 DB는 외부 공유하지 마세요. 운영 서비스에는 파일 암호화/접근권한/악성파일 검사/보관·삭제 정책이 필요합니다.
- 새 타입 file과 guidance_cards는 계약 2.0의 가산적 확장 제안입니다. 엄격한 이전 수신기는 갱신된 스키마 적용이 필요합니다.
- 기존 라이브러리 에이전트는 변경하지 않았습니다. 이 ZIP이 최신 코드 기준본입니다.
