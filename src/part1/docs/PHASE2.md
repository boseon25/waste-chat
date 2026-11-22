# 2단계 구현 — 건별 질문·답변·저장 검증 (0.3.0)

## 실행 순서

```text
작성한 DOCX → 기존 ingest/inspect/verify → (1단계 초기 검토)
→ phase2 next: 현재 건별 누락/불일치 탐색, 최대 3개 질문
  ├─ 사용자 답변형 → 형식·단위·대상·질문 차수 검증 → DB에 새 이력 추가
  └─ 증빙형 → documents → 기존 자료 요청/확보 안내 → supply-answer
→ next: 같은 맥락의 모름/자료 없음 재요구 억제
→ DB 재검증·재조회 → 검토용 JSON 초안 출력
```

JSON 입력을 요구하지 않습니다. 기존 DOCX·원문 테이블을 덮어쓰지 않습니다. 입력 검토 확인을 자동 강제하는 운영 워크플로는 아직 아닙니다. 2단계 명령은 로컬 SQLite에서 실행되며 등록된 라이브러리 에이전트 자체는 변경하지 않았습니다.

## 1. 질문 구성 규칙

### 우선순위

| 우선순위 | 확인 항목 | 진행 방식 |
|---|---|---|
| 0 | 현장·폐기물 이름이 누락/명시적으로 불명확 | 해당 건의 다른 질문은 보류. 다른 폐기물 건은 독립적으로 진행 |
| 5 | 수량 형식 오류, 체크 누락/중복, 명시된 측정량 불일치 | 해당 값부터 재확인 |
| 15~30 | 처리량, 발생 작업, 형태, 혼합 구분, 유해물질 사용자 진술, 단일 재질명 | 형식에 맞는 사용자 답변으로 조건부 후보 저장 |
| 35 | 선택 경로의 분리 가능성 | --route를 명시한 경우에만 질문; 해당 경로 적격성 판단은 아님 |
| 40 | 측정 방법·실제 측정일 | 처리량이 확인되고 수량 충돌이 없을 때 질문 |
| 45~50 | 구체적인 공정·물질·오염 관련 확인자료, 분류 근거자료 | 값을 직접 확정하지 않고 자료 요청 기능에 연결 |

질문은 처음 선택될 때 DB에 저장합니다. next를 여러 번 실행해도 같은 질문이 중복 저장되거나 초안 차수가 올라가지 않습니다. 미답변 질문은 다시 표시될 수 있습니다. 답한 질문은 같은 맥락에서 자동 반복하지 않습니다.

이 구현은 **명시적인 규칙 기반**입니다. 임의의 자유 문장 사이에서 모든 모순을 알아내는 AI 검토가 아닙니다. 특히 수량 교차 대조는 `실제 측정량: 100kg`처럼 단일 값이 명시된 측정 기록만 비교하며, 용기/순중량/여러 수치를 임의 추론하지 않습니다. 서로 환산 가능한 같은 차원의 수량은 비교하고 L→kg·포대→kg는 변환하지 않습니다.

유해성 자료 질문은 사용자 진술 `있음` 또는 발생 작업/형태에 `유기용제`, `기름막`, `유분`이 명시된 경우 등 제한된 규칙으로 선택합니다. 키워드 기반 확인 요청이지 hazardous_suspect나 designated_waste_status의 자동 판정이 아닙니다. 부정문·복잡한 문맥의 의미 해석은 미구현이며, 필요한 자료가 실제로 무엇인지 후속 검토가 필요합니다. 모든 사용자에게 시험성적서를 일괄 요구하지 않습니다.

### 이번에 지원하는 사용자 답변형

| field | 입력/검증 | 저장 의미 |
|---|---|---|
| site_address | 단일 현장 주소·동/장소 | 건별 새 현장 후보. 공통 현장을 쓰는 다른 건은 변경 안 함 |
| name | 구분 가능한 폐기물 이름 | 해당 건의 이름 후보 |
| process_description | 발생 작업·알고 있는 원료 설명 | 사용자 진술 |
| physical_state | 고체/액체 등 구체적 형태 설명 | 사용자 관찰 진술 |
| quantity | 예: 200kg, 0.2t, 100L, 포대 10개 | 이번 처리량. 양수·단위·정수 개수 검사. 질량만 kg 정규화 |
| quantity.method | 측정/계수 방법 | 사용자 진술. net_weight 자동 확정 안 함 |
| quantity.measured_at | YYYY-MM-DD 또는 모름 | 실제 달력 날짜·미래 날짜 거절. 작성일로 대체 안 함 |
| mixture_type | 단일 재질 / 따로 섞임 / 붙어 있음 / 모름 | single / mixed / composite / null |
| hazard_declaration | 있음 / 없음 / 모름 | true / false / null. 없음도 불검출·무해성 확인 아님 |
| components | 단일 재질 이름·라벨 문구 | 이름만 보관. 구성비 100% 자동 생성 안 함 |
| separability | 가능 / 불가능 / 모름 | 선택 경로에 대한 사용자 진술. 작업 검증은 별도 |

일반 텍스트는 비어 있거나 `둘 다`, `모두`, `위와 같음` 같은 일괄 답변을 거절합니다. 현장/이름의 `A 또는 B`처럼 명시적으로 대상을 하나로 정하지 않은 답변도 거절합니다. 사용자 답변만으로 텍스트의 진위까지 검사하는 것은 아닙니다.

### 증빙형

- classification_evidence: 기존 신고자료의 해당 행, 현재 건 적용 조건이 명시된 관할기관 회신 등.
- hazard_evidence: 실제 공정/원료/물질/시료 관련 자료. MSDS는 사용 후 폐기물 분석 대체 불가.
- 이 두 질문에 `phase2 answer "99-99-99"`처럼 답하여 분류번호를 채울 수 없습니다. documents로 자료 요청에 연결합니다.
- 첨부를 받으면 answered일 뿐 resolved가 아닙니다. 현재 건·현장·시료·시점 적용성은 미검토입니다. selected_code는 자동 설정하지 않습니다.

## 2. 사용법

### 가상 사례를 한 번에 실행

```bash
python -m pip install -r requirements.txt
python demo_phase2.py --out-dir output/my_phase2_run
python -m unittest discover -s . -p "test*.py" -v
```

Python 3.11 이상. 검증 환경은 Python 3.14입니다. tzdata는 Windows 등 시스템 시간대 DB가 없는 환경의 Asia/Seoul 날짜 검증을 위해 포함했습니다. 기존 시연 DB가 있으면 덮어쓰지 않으므로 다른 --out-dir을 쓰세요.

### 작성한 입력서를 먼저 저장

```bash
python db_intake.py ingest "작성한입력서.docx" --db output/my_intake.sqlite3
python db_intake.py inspect --db output/my_intake.sqlite3
python db_intake.py verify --db output/my_intake.sqlite3
python phase2.py next --db output/my_intake.sqlite3
```

가상 문서에만 `--synthetic`을 붙입니다. 여러 제출이 있는 DB에는 `--submission SUBMISSION_ID`를 지정합니다. 아래 CASE_ID/QUESTION_ID는 실제 출력된 ID로 바꿉니다.

```bash
python phase2.py next --db output/my_intake.sqlite3 --case CASE_ID --limit 3
python phase2.py answer --db output/my_intake.sqlite3 --case CASE_ID --request QUESTION_ID --revision 0 "2026-11-10"
python phase2.py history --db output/my_intake.sqlite3 --case CASE_ID --request QUESTION_ID
python phase2.py correct --db output/my_intake.sqlite3 --case CASE_ID --request QUESTION_ID --revision 1 --reason "실제 작업 기록 재확인" "2026-11-09"
python phase2.py status --db output/my_intake.sqlite3 --case CASE_ID
```

`--revision`은 전체 draft_revision이 아니라 **그 질문의 현재 답변 차수**입니다. 최초 답변은 0을 넣습니다. 다른 작업이 먼저 정정했다면 REVISION_CONFLICT로 거절하고 이력을 다시 읽어야 합니다. 같은 답변 재전송은 중복 기록하지 않습니다.

### 문서 확보 안내로 연결

```bash
python phase2.py documents --db output/my_intake.sqlite3 --case CASE_ID --request QUESTION_ID
python db_intake.py supply-answer --db output/my_intake.sqlite3 --request GUIDANCE_ID "자료 없음"
python db_intake.py supply-correct --db output/my_intake.sqlite3 --request GUIDANCE_ID "보관본을 찾아 제출합니다. 적용성 검토가 필요합니다." --file "기존신고자료.pdf"
python db_intake.py supply-history --db output/my_intake.sqlite3 --request GUIDANCE_ID
```

GUIDANCE_ID는 documents가 출력한 자료 요청 ID로, 최초 질문 ID와 구분합니다. 안내에는 사내 담당자/기관 경로, 준비정보, 대안, 공식 URL의 확인 범위가 있습니다. **신청서 초안 작성·기관별 실제 접수 절차 확인·자동 제출은 3단계 후속 구현**입니다. 공식 링크 확인 기록은 기존 2026-11-11 자료를 유지했으며 이번 코드 작업에서 새로 검증하지 않았습니다.

### 이미 답한 정보가 바뀌거나 처음 문서에서 모름이라고 썼던 경우

자동 반복 질문 대신 사용자의 명시적인 재확인 요청을 사용합니다.

```bash
python phase2.py ask --db output/my_intake.sqlite3 --case CASE_ID --field quantity --reason "추가 실측 결과를 확보하여 재확인"
```

같은 질문의 기존 답변이 있으면 기존 ID/차수가 나오며 correct를 사용합니다. 기존 답변이 없으면 answer를 사용합니다. 다른 건을 일괄 갱신하는 명령은 없습니다.

### 기존 0.2.2~0.2.4 질문과의 연결

next 결과의 source를 확인하세요.

- phase2: 새 answer/correct/history 사용. 증빙형은 documents로 연결.
- measurement: 기존 미답변 측정일 질문 재사용. `db_intake.py answer/correct/history` 사용.
- guidance: 기존 자료 요청 재사용. `db_intake.py supply-answer/supply-correct/supply-history` 사용.

기존 모름 답변도 같은 대상/수량 맥락이면 재요구하지 않습니다. 단, 새 단계에서 처리량이나 대상이 바뀌면 원래 DOCX 행에만 연결된 legacy 측정일의 적용성을 알 수 없으므로 재사용하지 않습니다. 이때는 새 phase2 질문으로 해당 수량·대상에 대한 날짜를 확인해야 합니다. 현재 맥락에 맞는 phase2 날짜 답변이 있으면 이후 legacy 명령을 실행해도 그 phase2 날짜를 덮어쓰지 않습니다.

## 3. 모름·자료 없음·정정 후 재확인

- 답변형의 `모름`, `자료 없음`, `잘 모르겠어요`, `잘 모르겠습니다`, `자료가 없어요` 등 명시된 미확인 표현은 null/unavailable로 보관합니다. 원문은 그대로 보존합니다.
- `없음`은 hazard_declaration에서 false라는 사용자 진술입니다. `없어요`처럼 의미가 모호한 선택형 답변은 자동 변환하지 않고 오류로 돌려줍니다.
- 자료 요청의 supply-answer는 기존 규격대로 `모름` 또는 `자료 없음`을 사용합니다.
- 같은 건·필드·맥락의 unavailable은 자동 재질문하지 않습니다. 대상이 계속 불명확하면 그 건은 보류 상태로 남으며 다른 건은 진행할 수 있습니다.
- **실제 의존 조건이 바뀐 경우는 같은 질문이 아닙니다.** 현장/이름이 바뀌면 관련 질문을 다시 검토하고, 수량을 다시 기록·정정하면 측정 방법/날짜를 재확인합니다. 혼합 형태가 바뀌면 재질·분리 가능성을 재확인합니다.
- 300kg→400kg→300kg처럼 되돌아와도 이전 날짜가 자동 부활하지 않습니다. 답변 근거의 차수까지 맥락에 포함합니다.
- 대상/공정/혼합 등 분류·유해성 자료의 맥락이 바뀌면 이전 자료 없음이 새 질문을 영구히 막지 않습니다. 기존 자료를 재사용할 수 있는지 적용성 확인을 요청하는 것이며, 새 발급/재분석을 무조건 요구한다는 뜻은 아닙니다.

## 4. DB 및 JSON 규칙

추가 테이블:

```text
phase2_requests : case_id, field, context, route, priority, rule_version, asked_reason
phase2_answers  : request_id, revision, raw_value, typed columns, status, reason, answered_at
phase2_sites    : 건별 현장 후보 및 원 답변/차수 연결
```

- 원본 DOCX/controls/case_candidates 등을 변경하지 않습니다. 새 답변 테이블에서 검토용 값을 합성합니다.
- 날짜·선택형·수량·단위를 코드로 검사하고 DB 저장 후 다시 해석해 원문과 정규화 값이 같은지 검사합니다.
- 최초 답변/정정은 트랜잭션과 질문 차수 검사로 보호합니다. invalid 입력은 반영하지 않습니다.
- 요청/검토/안내 조회는 draft_revision을 올리지 않습니다. 유효하게 저장한 답변/정정만 해당 건 차수에 합산합니다. 과거 이력도 합산하며 case_version=0/published=false를 유지합니다.
- 값은 basis=user_report, verification=unverified입니다. 날짜가 정확한 형식이어도 실제 측정일 진위를 인증한 것은 아닙니다.
- JSON evidence/assertions에 원문과 답변 근거를 보존합니다. 오래된 맥락의 질문은 활성 요청에서 제외하고 STALE_PHASE2_CONTEXT/STALE_GUIDANCE_CONTEXT 제한을 표시합니다. 원본 입력 경고는 감사용으로 유지됩니다.
- DBCheck.counts.sites는 원본 현장 수, phase2_sites는 실제 추가 테이블의 현장 후보 수입니다. 과거 정정 후보도 보존하므로 전체 sites에는 과거 후보가 포함될 수 있습니다. 현재 건의 site_id가 현재 연결입니다.

```bash
python db_intake.py export --db output/my_intake.sqlite3 --out output/phase2_review.json
python db_intake.py validate-json output/phase2_review.json
```

## 5. 범위와 미구현

이번 버전은 위 11개 답변 항목과 2개 자료 요청 규칙을 구현했습니다. 모든 법령·처리경로·자유문장 충돌을 판정하는 완성형 에이전트는 아닙니다.

아직 미구현: 최근 3개월 월별 발생량 레코드의 완전한 typed 입력/적용, 구성비·분석값 승인 반영, 지정 여부/분류번호 확정, 실제 경로 조건표, 신청서 작성/접수, 첨부 내용 자동 추출·시료 적용성 승인. 기존 자료 안내 주제는 계속 이용할 수 있지만 이번 자동 질문 규칙에 모두 포함되는 것은 아닙니다.

기존 supply/legacy 명령은 보존되며 그 명령 자체에 신규 optimistic revision 검사나 단계 게이트를 추가한 것은 아닙니다. 새 phase2 answer/correct의 차수 검사는 해당 명령 경로에 적용됩니다.

운영용 인증·회사별 권한·DB 암호화·악성 첨부 검사·감사서명·보관/삭제 정책·웹 화면은 별도 구현이 필요합니다. ZIP에 포함한 DB/첨부는 전부 가상 시연입니다. 실제 업체 DB를 ZIP으로 공유하지 마세요.
