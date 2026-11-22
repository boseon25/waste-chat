# 단계 3·4 구현 설명 (0.4.0)

## 1. 처리 흐름

```text
기존 DOCX 원문 저장/재대조 → 기존 phase2 현장값 질문
                       ↓
필요 항목 선택 → 건·현장·시료·목적별 현재 맥락 계산
                       ↓
이미 원문 대조한 항목 제외 → 문서 종류별 부족값 묶기
                       ↓
기관 회신 묶음 / 동일 시료 시험성적서 묶음
                       ↓
공식 안내·실제 접수 URL·현재 상태·준비서류·전체 절차 표시
                       ↓
입력서/채팅 정보 반영 DOCX·Markdown 준비 초안 (미제출)
                       ↓
사용자가 기관과 협의 후 실제 접수 → 접수 참조 수기 기록
                       ↓
같은 건·묶음에 원문 첨부 (미검토) → 위치/적용성/값 대조
                       ↓
대조된 문서값만 목록에 반영 → 빠진 값만 보완 묶음
```

## 2. 현재 지원 범위

| 필요 항목 ID | 표시 항목 | 묶음 |
|---|---|---|
| classification.reported_code | 기관 회신의 분류번호(검토 후보) | 기관 회신 |
| classification.applicability | 현재 건 적용 여부 | 기관 회신 |
| classification.conditions | 적용 조건·추가 확인사항 | 기관 회신 |
| designated_waste_status | 지정폐기물 해당 여부에 대한 기관 확인 내용 | 기관 회신 |
| analysis.heating_value | 발열량 | 시험성적서 |
| analysis.moisture | 수분 | 시험성적서 |
| analysis.chlorine | 염소 | 시험성적서 |

이 목록 전체를 모든 폐기물에 요구하지 않습니다. 기본 요청은 분류 회신 3항목이며, 실제 필요한 시험값은 `--need`로 선택합니다. 현재 판단 목적에 어떤 시험이 법적으로 필수인지 자동 판정하는 기능은 없습니다. 다른 개별 유해물질·월 발생량·전 품목 서식은 아직 이 신규 묶음 규칙의 대상이 아닙니다. 기존 안내 카탈로그는 유지됩니다.

한 회신/성적서로 받을 수 있는 값은 한 초안에 묶습니다. 다만 실제 기관의 회신 범위, 시험방법, 동일 시료 여부, 한 성적서 발급 가능성은 상담으로 확인합니다. 시료/목적이 다르면 대조된 값을 공유하지 않습니다. 사용자가 모든 필요한 항목을 선택해야 하며 '이 항목들만으로 법적으로 충분하다'는 뜻은 아닙니다.

## 3. 받은 서류에서 부족한 값만 남기기

첨부를 등록하는 순간 요청 전체를 해결하지 않습니다. 현재 버전은 **문서 본문 OCR/자동 추출이 아니라 원문 대조 결과를 기록하는 단계**입니다. Timely 또는 담당자가 문서를 읽고, 페이지/표/행 등 위치와 적용성을 대조하여 항목별로 기록합니다. 실제 운영에서 자동 추출을 추가하더라도 추출 후보와 확정 과정을 분리해야 합니다.

```bash
python phase3.py attach --db my_intake.sqlite3 --case "<건ID>" --packet "<묶음ID>" received.docx --issuer "회신 기관명" --date 2026-11-14
python phase3.py review --db my_intake.sqlite3 --case "<건ID>" --packet "<묶음ID>" --document "<문서ID>" --field classification.reported_code --value "<원문의 XX-XX-XX>" --locator "2쪽 결과표 분류번호 행" --reviewer "검토자" --reason "현재 현장·품목·기간 적용성과 원문 대조" --confirm-applicability
```

위 분류번호 값은 실제 원문으로 바꿔야 합니다. 코드 모양만 검사하며 해당 코드의 법적 타당성을 자동 승인하지 않습니다. `--confirm-applicability`는 **검토자의 자기기재 확인**이지 시스템의 법적 인증/독립 검증이 아닙니다. 없으면 `held`, 있으면 조건을 통과한 값만 `accepted_candidate`로 문서값 목록에 기록합니다. 핵심 도메인의 `classification.selected_code`는 변경하지 않습니다.

시험값은 문서와 묶음의 시료 식별자 일치, 발급기관/문서일, 단위, 시험법, 건조·습윤 등 기준까지 기록해야 합니다.

```bash
python phase3.py attach --db my_intake.sqlite3 --case "<건ID>" --packet "<시험묶음ID>" report.pdf --issuer "실제 시험기관" --date 2026-11-14 --sample "CASE-A-S1"
python phase3.py review --db my_intake.sqlite3 --case "<건ID>" --packet "<시험묶음ID>" --document "<문서ID>" --field analysis.moisture --value 11.0 --unit % --basis wet --method "성적서에 적힌 시험방법" --locator "결과표 수분 행" --reviewer "검토자" --reason "원문 수치·시료·현장·기간·시험 조건 대조" --confirm-applicability
```

위 수치는 명령 형식 예시일 뿐 실제 값이 아닙니다. `0`은 측정값으로 보존할 수 있습니다. `불검출`/`ND`는 0으로 바꾸지 않고 원문을 보존하며, 검출한계 등을 별도 확인해야 하는 보류 값으로 남깁니다. 사용자 진술 `false`는 기존 phase2에 그대로 남으며 시험상 불검출과 합치지 않습니다.

같은 목적·시료·맥락으로 `plan`을 다시 실행하면 대조 완료된 항목을 제외한 나머지만 문서 묶음에 들어갑니다. `모름/자료 없음`으로 기록한 항목은 미해결이지만 같은 요청을 반복 생성하지 않습니다. 불검출도 수치를 다시 요구하지 않고 한계정보·판단 가능 범위를 검토하도록 남깁니다. 나중에 같은 묶음으로 새 근거를 첨부하고 해당 항목을 정정할 수 있습니다.

## 4. 이력·격리·정정

- `external_packets`: 폐기물 건·목적·시료·맥락 해시·문서 종류
- `external_packet_fields`: 그 문서에 요청한 필요 항목만 저장
- `external_documents`: 원문 BLOB+SHA256, 발급기관·문서일·시료·접수시각
- `external_values`: 원문값·정규화값·단위·위치·검토자·적용성 확인 결과를 append-only 저장
- `external_drafts`: 실제 생성한 DOCX/Markdown 원본과 해시
- `external_events`: 준비 초안 생성·사용자 접수 진술·첨부 수령 이력

다른 건의 묶음/첨부 ID를 입력하면 거절합니다. 현장·폐기물·공정·수량·측정 맥락·구성 등 변경 시 이전 묶음은 `stale`로 표시합니다. 늦게 온 회신 원문은 과거 묶음에 보존할 수 있지만, 현재 사실로 새롭게 대조 채택하려면 현재 맥락을 다시 확인해야 합니다. 다른 폐기물 건의 수정은 해당 건의 묶음을 낡게 만들지 않습니다.

값 정정은 같은 `review` 명령에 최신 `--expected-review <검토기록번호>`를 지정합니다. 낡은 번호는 거절하고 이전 값은 지우지 않습니다. 발급기관/문서일/시료 메타데이터 정정은 새 첨부 기록으로 남습니다. 기존 검토값의 철회가 필요하면 최신 검토번호를 사용해 `--value 미확인`으로 정정 이력을 남긴 후 새 근거를 대조하세요. 단순히 새 문서를 첨부했다고 과거 대조결과가 자동 철회되는 것은 아닙니다.

```bash
python phase3.py submitted --db my_intake.sqlite3 --case "<건ID>" --packet "<묶음ID>" --reference "사용자가 확인한 실제 접수번호" --actor "기록자"
python phase3.py extract --db my_intake.sqlite3 --case "<건ID>" --packet "<묶음ID>" --document "<문서ID>" --out output/retrieved_original.docx
python phase3.py export --db my_intake.sqlite3 --case "<건ID>" --out output/case_document_review.json
python db_intake.py verify --db my_intake.sqlite3
```

`submitted`는 전송 기능이 아니라 사용자의 접수 진술 기록입니다. 기존 `db_intake.py verify`에도 새 원문 해시·연결 검증을 연결했습니다. 원래 입력서 저장 테이블과 원문 대조 방식은 바꾸지 않았습니다.

## 5. 버전·보안·한계

- 기존 `case_version=0`, `published=false`를 유지합니다. 새 문서값 목록은 핵심 사실/법적 분류 승인과 별도입니다.
- 입력서/phase2에 대한 `draft_revision`과 문서 워크플로에 대한 `workflow_revision`을 구분합니다. 첨부만으로 원래 사실 초안 차수를 올리거나 사실을 바꾸지 않습니다.
- `phase3.py export`는 기존 계약 JSON을 대체하는 것이 아니라 건별 문서 워크플로 검토본입니다. 기존 `db_intake.py export`만으로 신규 문서 워크플로 전체가 출력되지는 않습니다.
- 현장 질문은 기존 phase2 규칙과 미확인 응답 억제를 재사용하며 한 번에 1~3개입니다.
- 파일은 크기/확장자와 해시만 검사하며 실행하지 않습니다. 악성코드 탐지·암호화·회사별 권한·운영 인증은 미구현입니다.
- 사이트 접수, 공식 양식 완전 자동 작성, 최종 제출/서명/결제, 수신함 자동 수집은 구현하지 않았습니다.
- 관할기관 자동 확정 및 모든 시험기관 자동 선정은 미구현입니다. 현재는 국민신문고 접수 경로와 KTR 접수 경로 예시를 연결합니다. 기관·지점·개별 항목 수행 가능성은 협의가 필요합니다.
- 입력서/채팅 출처를 준비 초안에 표시하지만, 데이터의 실재성은 자동 인증하지 않습니다.
- 배포 DB와 문서는 합성 사례만 사용합니다. 실제 DB·개인정보·인증키는 배포물에 넣지 마세요.
