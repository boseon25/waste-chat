# DOCX → DB 확인 → JSON 확인 → 코드 테스트

버전 0.2.2 / 로컬 SQLite 저장 검증 단계 (M2 일부)

**JSON 입력 파일을 먼저 만들 필요가 없습니다.** 원본 DOCX와 추출 필드를 DB에 저장한 다음 DB에서 다시 읽어 확인합니다. 사용자가 export 명령을 실행하기 전에는 JSON 파일이 생성되지 않습니다.

## 1. 설치

ZIP을 풀고 `wastecase` 폴더에서 실행합니다. Python 3.11 이상 권장(검증 환경 3.14). SQLite는 Python 표준 라이브러리이므로 별도 DB 서버 설치가 필요 없습니다.

```bash
cd wastecase
python -m pip install -r requirements.txt
```

## 2. 먼저 문서만 DB에 넣기

첨부 문서는 스스로 모든 정보가 가상이라고 밝힌 테스트 문서이므로 `--synthetic`을 지정합니다. 실제 문서에는 이 옵션을 사용하지 마세요.

```bash
python db_intake.py ingest fixtures/intake_checkable_example.docx --db output/my_check.sqlite3 --synthetic
```

처리 내용:

1. 지원 대상 DOCX인지 검사하고 콘텐츠 컨트롤·체크 상태·표/셀·문단 추출.
2. SHA-256과 원본 DOCX 바이트를 `submissions.original_docx` BLOB에 보존.
3. 회사·현장·폐기물 후보·원문 필드를 각각 관계형 테이블에 저장.
4. 하나의 트랜잭션 안에서 저장 내용을 확인한 뒤 commit.
5. DB 연결을 닫고 다시 열어 재조회·대조.

이 단계는 JSON을 읽거나 JSON 파일을 생성하지 않습니다. DB에도 JSON payload 칼럼을 사용하지 않습니다. 수량은 부동소수 오차를 피하려고 Decimal의 문자열로 저장합니다.

## 3. DB에 제대로 들어갔는지 보기

```bash
python db_intake.py inspect --db output/my_check.sqlite3
python db_intake.py verify --db output/my_check.sqlite3
```

제공된 예시의 확인값:

| 항목 | 기대값 |
|---|---|
| 업체 | 가상순환산업 (가상 업체) |
| 업체/현장/폐기물 후보 수 | 1 / 2 / 2 |
| 콘텐츠 컨트롤 | 51개 |
| 체크박스 | 선택 6개 / 미선택 14개 |
| 폐기물 1 | 제품 포장용 PE 필름 자투리, 200kg |
| 폐기물 1 현장 | A동 자재창고, 공통 현장 상속 |
| 폐기물 2 | 기계 세척 후 남은 세척액, 100L |
| 폐기물 2 현장 | B동 세척실, 개별 현장 |
| 유해물질 | 두 건 모두 모름(null) |
| 작성 기준일 | 2026-11-10 |
| 실제 측정일 | 미확인(null) |
| 실제 첨부 사진/분석서 | 없음. 파일명·서술로 첨부를 인정하지 않음 |

`verify`는 SQLite 무결성/외래키, 원본 해시, DB 원문 행 전체, 재추출된 회사·현장·수량·체크값과 DB 정규화 행을 비교합니다. 값이 다르면 실패합니다. 같은 파서를 재사용하므로 파서의 의미 해석 오류까지 증명하지는 못하며, 별도의 정답값 테스트도 함께 제공합니다. 악의적인 관리자 변조를 막는 서명/권한 시스템은 아닙니다.

같은 문서의 재업로드는 원본 해시+추출기 버전으로 멱등 처리하여 기존 제출/건 ID를 재사용합니다. 같은 문서의 synthetic 옵션을 바꾸면 충돌로 거절합니다. 수정된 문서는 새 제출이 됩니다. 여러 제출이 있는 DB에서는 출력된 ID를 `--submission ID`로 지정해야 합니다.

## 4. DB 확인 후 JSON 만들기

```bash
python db_intake.py export --db output/my_check.sqlite3 --out output/my_check.json
python db_intake.py validate-json output/my_check.json
```

export는 실행할 때마다 실제 DB를 다시 대조하고, 같은 읽기 트랜잭션에서 결과를 구성합니다. 실패하면 새 JSON을 만들지 않고 기존 출력 파일도 덮어쓰지 않습니다.

내보내는 파일은 단일 WasteCase가 아니라 아래 **검토용 묶음**입니다.

```json
{
  "export_kind": "db_verified_review_draft",
  "schema_version": "2.0",
  "published": false,
  "database_verification": {"status": "PASS", "...": "DB 대조 결과"},
  "company": {"...": "업체 정보와 필드별 근거"},
  "sites": [{"...": "현장 정보와 근거"}],
  "evidence": [{"...": "원문·태그·표/셀 또는 문단 위치"}],
  "cases": [{"...": "WasteCase 2.0 초안, 총 2건"}]
}
```

전체 스키마는 `schemas/DBReviewExport.schema.json`입니다. 가상 실행 결과는 `python db_intake.py demo --out-dir output/demo`로 생성하고 `output/demo/db_export.json`에서 확인합니다.

- 각 `cases[]`는 기존 WasteCase 모델로 검증합니다.
- `case_version=0`, `published=false`: 사용자 승인·공식 발행이 아닙니다.
- `quantity.value`는 JSON 문자열 `"200"`, `"100"`입니다.
- `hazard_declaration.value=null`, `raw_value="모름"`을 유지합니다.
- 입력서의 측정 방법 문장은 사용자 진술로 보존합니다. 계량표 검증으로 표시하지 않습니다.
- 라벨 기준 재질 문구는 원문 그대로 후보로 둡니다. 제품 라벨을 실제 첨부·검증했다고 하지 않습니다.
- 연락처·사업자번호의 실제 등록 검증은 하지 않습니다.

**주의:** `validate-json`은 JSON 구조와 내부 참조만 검사합니다. 파일 안의 `status=PASS`를 믿어 실DB 검증을 대체하지 않습니다. DB 확인은 `verify` 명령을 실행하세요. 기존 `cli.py validate`는 단일 WasteCase용이므로 이 묶음에는 `db_intake.py validate-json`을 사용합니다.

## 실제 측정일 보완

`inspect`에서 폐기물 ID를 복사합니다. 아래의 `폐기물_ID`와 `질문_ID`는 실제 값으로 바꿉니다.

```bash
python db_intake.py question --db output/my_check.sqlite3 --case 폐기물_ID
python db_intake.py answer --db output/my_check.sqlite3 --request 질문_ID 2026-11-09
python db_intake.py inspect --db output/my_check.sqlite3
python db_intake.py verify --db output/my_check.sqlite3
python db_intake.py export --db output/my_check.sqlite3 --out output/my_check.json
python db_intake.py validate-json output/my_check.json
```

기존 SQLite 파일에서도 첫 질문을 저장할 때 필요한 테이블을 자동으로 추가합니다. 같은 건에 질문을 다시 요청하면 같은 질문 ID를 반환합니다. 답변은 `YYYY-MM-DD` 또는 `모름`입니다. `모름`이면 질문은 `unavailable`로 기록되고 측정일은 미확인 상태로 유지됩니다. 같은 답변의 재입력은 기존 기록을 재사용합니다.

날짜 답변은 해당 건의 `quantity.measured_at`과 사용자 답변 근거에 연결되고, 질문은 `answered`가 됩니다. 작성 기준일과 다른 폐기물 건에는 영향을 주지 않습니다. DB 검증은 답변 원문과 저장 날짜의 일치도 확인합니다. 측정일의 근거는 사용자의 진술이며 JSON은 검토용 초안입니다.

### 측정일 답변 정정

첫 답변을 저장한 질문에 `correct`를 실행하면 다음 차수의 답변이 추가됩니다. `history`는 이전 답변과 현재 답변을 시간순으로 표시합니다.

```bash
python db_intake.py correct --db output/my_check.sqlite3 --request 질문_ID 2026-11-08
python db_intake.py history --db output/my_check.sqlite3 --request 질문_ID
python db_intake.py export --db output/my_check.sqlite3 --out output/my_corrected.json
```

첫 정정은 해당 건의 `draft_revision`을 2로 올립니다. JSON의 측정일과 연결 근거는 현재 답변을 가리킵니다. 이전 답변은 SQLite에 남으며 `history`로 확인합니다. 첫 답변이 있는 기존 SQLite 파일에서 그대로 실행할 수 있습니다.

## 5. 코드 테스트

```bash
python -m unittest discover -s . -p "test*.py" -v
```

현재 101개 테스트(기존 98개와 측정일 정정 3개). 원본 바이트 일치, 한글 저장, 수량·체크·현장 정답값, 재연결, 재업로드, 동시 중복 접수, 삽입 실패 롤백, 외래키, DB 값 불일치 시 출력 차단, 기존 파일 보존, JSON 모델 검증, 복사 태그·미확인·복수 체크 등을 포함합니다.

## 빠른 전체 시연

```bash
python db_intake.py demo --out-dir output/new_demo
```

저장 → 재조회/대조 → 텍스트 확인 결과 저장 → JSON 생성 순서로 실행됩니다. `intake.sqlite3`, `db_check.txt`, `db_export.json`이 생성됩니다. 단계별로 직접 확인하려면 위 ingest/inspect/verify/export 순서를 사용하세요.

## DB 테이블

- submissions: 제출 ID·해시·추출기 버전·synthetic·접수 시간·원본 BLOB
- controls: 태그·원문·체크 상태·placeholder·표/셀/문단 위치
- cells / paragraphs: 컨트롤 밖 안내문까지 원문 보존
- companies: 업체/번호/담당자/연락처 (제출별 독립, 업체 자동 병합 없음)
- sites: 공통/별도 현장과 원문 컨트롤 연결
- case_candidates: 폐기물별 초안 값·수량·체크·현장 상속·경고
- measurement_date_requests: 폐기물별 실제 측정일 질문 ID·첫 답변 원문·저장 날짜·기록 시각
- measurement_date_corrections: 질문별 2차 이후 정정 원문·저장 날짜·기록 시각

직접 DB 조회를 원하는 경우 `docs/DB_QUERIES.sql`의 SELECT를 SQLite 조회 도구에서 사용할 수 있습니다. DB 쓰기/수정은 이 버전의 정식 사용자 수정 API가 아닙니다. 수동 수정하면 원문 대조가 실패하도록 설계했습니다.

## 운영 범위

이것은 **로컬 단일 사용자 검증용 SQLite 구현**입니다. PostgreSQL 통합 검증, 인증/업체별 접근권한, 범용 질문 응답/수정 승인/불변 발행/outbox, OCR, 법령 검수는 아직 없습니다. 형식 밖 문서는 자동 해석하지 않습니다. 테스트 DB에는 가상 문서만 포함되어 있습니다. 실제 자료 사용 시 파일 패널/외부 공유 전 개인정보와 저장 정책을 검토해야 합니다.
