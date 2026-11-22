# WasteCase Part 1 · 기능 패키지 0.5.5

폐기물 기초 입력서를 SQLite에 저장하고, 건별 추가 질문과 자료 확인을 거쳐 Part 2에 전달할 검토용 JSON을 만드는 로컬 Python 패키지입니다. Python 3.11 이상을 지원하며, 아래 실행 예시는 macOS의 Python 3.12 기준입니다.

## 바로 열 수 있는 문서

- `fixtures/wastecase_intake_050.docx`는 사용자 작성용 기초 입력서입니다.
- `official_forms/`에는 기관에서 내려받은 원본 12개가 있습니다. 사업장폐기물배출자 신고·변경신고서, 경북 보건환경연구원 제출용 시험의뢰서와 작성 안내, KTR 택배접수 신청서와 동의서, 순환자원 인정·재인정 신청서, 관련 기준표와 온라인 등록 매뉴얼이 포함됩니다.
- `official_forms/catalog.json`에 배포처, 원본 다운로드 주소, 파일 확인일과 용도가 있습니다. 터미널에서 `python part1.py forms`로 목록을 보고, `--topic hazard`처럼 필요한 주제만 조회할 수 있습니다.

분류번호가 불명확하면 별표 4와 입력서의 현장·공정·원료·성상을 대조한 뒤 기관 질의 초안을 사용합니다. 신고가 필요한 사업장은 분류번호를 확인한 뒤 신고서의 해당 칸에 적습니다. 경북 보건환경연구원과 KTR 양식은 해당 기관용입니다. 순환자원 지정고시는 첨부된 매뉴얼에 따라 시스템에서 등록하고, 개별 인정은 공식 신청서를 관할 유역·지방환경청에 제출합니다.

## 설치와 가상 시연

압축을 푼 `wastecase_part1` 폴더에서 실행합니다.

```zsh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo_phase3.py --out-dir output/part1_demo
```

가상 입력서 → SQLite → 추가 답변 → 기관 문의·시험 의뢰 Word 초안 → 일부 값을 담은 가상 회신 첨부 및 검토 → 건별 JSON 순서로 실행됩니다. 결과는 `output/part1_demo/case_document_review.json`과 같은 폴더의 `authority_initial/request.docx`, `lab_initial/request.docx`, `cli-check.txt`에서 확인합니다. 다시 실행하는 경우 새 출력 폴더를 지정합니다.

## 내 입력서로 진행

1. **입력서 접수.** `fixtures/wastecase_intake_050.docx`를 복사해 Word에서 작성합니다. 한 입력서에 여러 폐기물이 있으면 품목마다 표를 하나씩 작성합니다. 원료·약품·재질·월별 발생량·기존 이력은 보유 기록과 출처를 적습니다. 이 예시는 입력서 한 부마다 새 DB 파일을 사용합니다. 가상 입력서 `fixtures/intake_records_example.docx`로 아래 흐름을 연습할 경우에만 `--synthetic`을 추가합니다.

   ```zsh
   DB=output/intake.sqlite3
   python db_intake.py ingest '작성한_입력서.docx' --db "$DB"
   python db_intake.py verify --db "$DB"
   python phase3.py cases --db "$DB"
   ```

2. **현장·폐기물과 누락값 확인.** 목록에서 해당 폐기물의 `case_id`를 복사해 `CASE`에 넣습니다. `next` 결과의 질문 ID와 안내에 따라 답합니다. 아래는 최초 측정일 답변의 예입니다. 증빙이 필요한 질문은 문서 확인으로 이어집니다.

   ```zsh
   CASE='폐기물 건 ID'
   python phase2.py next --db "$DB" --case "$CASE" --limit 1
   python phase2.py answer --db "$DB" --case "$CASE" --request '질문 ID' --revision 0 '2026-11-09'
   python part1.py status --db "$DB" --case "$CASE"
   ```

3. **기존 신고자료·기관 회신 확인.** 보유 여부를 묻고, 있다면 현재 건과 현장·공정·원료·혼합 상태가 일치하는 자료를 첨부합니다. 파일은 해당 건의 답변에 기록됩니다.

   ```zsh
   python part1.py ask-evidence --db "$DB" --case "$CASE" --topic authority
   python part1.py answer-evidence --db "$DB" --case "$CASE" --topic authority '자료 있음' --file '기존_신고자료.pdf'
   python part1.py prepare --db "$DB" --case "$CASE" --topic authority
   PACKET='문서 묶음 ID'
   python part1.py import-evidence --db "$DB" --case "$CASE" --packet "$PACKET" --issuer '실제 발급기관' --date '2026-11-10'
   ```

   회사가 보유한 신고서·인계서·처리실적을 먼저 보관하려면 `part1.py attach-history`를 실행합니다. 과거 신고서만 `part1.py link-history`로 해당 건의 분류 질문에 연결할 수 있습니다.

4. **신규·자료 없음·적용 불명확.** 보유 여부에 `자료 없음` 또는 `모름`을 기록합니다. `part1.py status`의 `classification_table`과 `classification_inputs`에서 별표 4 링크와 대조할 입력값을 확인합니다. 근거와 번호를 찾으면 `classification supported --code … --reference …`로 후보를 기록합니다. 판단이 불명확하면 다음처럼 문의 초안을 만듭니다.

   ```zsh
   python part1.py answer-evidence --db "$DB" --case "$CASE" --topic authority '자료 없음'
   python part1.py classification --db "$DB" --case "$CASE" unresolved
   python part1.py prepare --db "$DB" --case "$CASE" --topic authority
   PACKET='문서 묶음 ID'
   python part1.py status --db "$DB" --case "$CASE" --packet "$PACKET"
   python part1.py draft --db "$DB" --case "$CASE" --packet "$PACKET" --out-dir output/inquiry_01
   ```

   `request.docx`에 확보한 입력값, 확인할 미확인 항목, 기관과 접수 경로, 준비 자료, 제출 순서를 담습니다. 초안과 같은 출력 폴더의 `official_forms/`에 해당 주제의 기관 원본도 복사됩니다. 공식 양식에 필요한 값은 초안을 참고해 사용자가 기입합니다.

5. **받은 회신·성적서 검토 후 전달.** 사용자에게 받은 문서를 `phase3.py attach`로 묶음에 붙입니다. `phase3.py review`에서 원문 값·페이지나 표·검토자를 기록하고, 해당 현장·품목·시료·시점과 대조한 항목에 `--confirm-applicability`를 사용합니다. 요청값을 찾을 수 없었던 문서는 `part1.py no-values`에 확인 내용을 기록합니다.

   ```zsh
   python phase3.py attach --db "$DB" --case "$CASE" --packet "$PACKET" '회신.pdf' --issuer '실제 발급기관' --date '2026-11-12'
   python phase3.py review --db "$DB" --case "$CASE" --packet "$PACKET" \
     --document '문서 ID' --field classification.conditions --value '회신 원문에 적힌 적용 조건' \
     --locator '회신에서 값을 확인한 위치' --reviewer '검토자' --reason '현재 건과 대조한 근거' \
     --confirm-applicability
   python phase3.py export --db "$DB" --case "$CASE" --out output/case_document_review.json
   ```

   JSON에는 건별 입력값, 추가 답변의 반영 결과, 문서 근거와 대조 기록이 포함됩니다. 첨부만 한 자료는 검토 대기로 보관합니다. 분류 후보와 확인되지 않은 값은 확정값으로 올리지 않습니다. DB 전체의 검토용 export는 `python db_intake.py export`를 사용합니다.

## 외부 확인 주제

`part1.py ask-evidence`와 `answer-evidence`는 아래 주제를 지원합니다.

| `--topic` | 검토하거나 요청할 자료 | `prepare`에 필요한 주요 입력 |
|---|---|---|
| `authority` | 신고서·신고증명서 또는 관할기관 서면 회신 | 별표 4 대조 결과 또는 기존 자료 보유 여부 |
| `hazard` | 공정·원료·오염 자료 또는 제출용 폐기물 분석결과서 | `--mode existing\|new`, `--reason`, 시료가 있으면 `--sample` |
| `lab` | 발열량·수분·염소 등 활용 목적 시험성적서 | 보유 여부와 `--reason`, 필요한 항목마다 `--need`, 시료가 있으면 `--sample` |
| `circular_registration` | 순환자원 지정고시 등록확인서 | `--mode existing\|new`, `--reason` |
| `circular_recognition` | 순환자원 인정서 | `--mode existing\|new`, `--reason` |

시험항목 예시: `analysis.moisture`, `analysis.chlorine`, `analysis.heating_value_low`, `analysis.heating_value_high`. 접수 경로는 패키지의 공식 링크와 확인 기록을 표시하며 실제 접수와 회신 수령은 사용자가 진행합니다.

## 파일과 검증

| 파일 | 역할 |
|---|---|
| `db_intake.py` | DOCX 접수, 원본·정규화 값 검증, 전체 검토용 JSON |
| `phase2.py` | 추가 질문, 답변·정정·이력 |
| `part1.py` | 건별 자료 보유 확인, 기존 문서 연결, 별표 4 대조 기록, 문의·의뢰 준비 |
| `phase3.py` | 검토 묶음 조회, 회신·성적서 첨부 및 대조, 건별 검토용 JSON |
| `fixtures/` · `schemas/` | 입력서 원본 양식, 가상 예시·검증 자료, 데이터 계약 스키마 |

```zsh
python -m unittest discover -s . -p 'test*.py'
```

상세 흐름은 [DOCX·DB 검증](docs/DB_FIRST.md), [추가 질문](docs/PHASE2.md), [문서 검토](docs/PHASE3.md), [접수 경로 확인 기록](docs/ROUTE_VERIFICATION.md)을 참고합니다.
