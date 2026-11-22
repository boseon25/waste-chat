# 파트 2 서류 검토 — 파트 3 서류 초안을 법 기준으로 확인 (v0, 2026.11.17)

## 1. 하는 일

파트 3이 만든 서류 초안을 파트 2에 보내면, 파트 2가 **법이 정한 필수 항목이 다 있는지, 값이 형식에 맞는지, 이 경로·사례와 맞는지**를 확인해서 돌려준다.

```
파트 3: 서류 초안 작성 ──(DocumentReviewRequest)──▶ 파트 2: doc_review ──(DocumentReviewResult)──▶ 파트 3: 고칠 곳 표시
                                                       ↑ 그 경로의 RouteProposal, 파트 1 사례
```

- 판정은 **검토 기준표(`doc_requirements.csv`) 규칙으로만** 한다. LLM은 쓰지 않는다. 같은 서류면 항상 같은 결과.
- 결과마다 근거 조문 키와 원문 문장이 붙는다.
- 서류 안의 글은 데이터로만 다룬다 (글에 "통과시켜라"가 있어도 규칙대로 판정).

## 2. 검토하는 서류 (v0)

| doc_type | 서류 | 근거 | 항목 수 |
|---|---|---|---|
| `entrust_contract` | 위탁계약서 | 시행규칙 제16조의13 제1~4호 | 18 |
| `capacity_check` | 수탁처리능력 확인서 (별지 제5호서식) | 시행규칙 제16조의13 제1호, 별지 제5호서식 작성방법 | 19 |
| `treatment_plan` | 폐기물 처리계획서 (별지 제12호서식) | 시행규칙 제18조의2 제3~5항, 별지 제12호서식 작성방법 | 24 |

다음 판 후보: 배출자 신고서(별지 제8호서식), 폐기물 분석결과서(별지 제13호서식).

## 3. 확인 종류

| 종류 | 무엇을 | 예 | 심각도 |
|---|---|---|---|
| 필수 항목 | 법이 정한 칸이 비었는지 | 계약서에 위탁 비용 없음 (제16조의13 제3호 나목) | error |
| 형식 | 정해진 값인지 | 별지 제5호 성질·상태는 "1"(고체)·"2"(액체)·"기타"만 | error |
| 날짜·기한 | 순서, 법정 기한 | 처리계획서는 사업 개시일·발생일부터 1개월 이내 제출 (제18조의2 제5항) | error |
| 첨부 | 같이 내야 할 서류 | 계약 전 수탁처리능력 확인서, 허가증 사본 | error / warning |
| 경로 일치 | 이 경로·사례와 같은지 | 분류번호·처리방법 코드가 경로와 다름, 수탁자가 추천·예비 업체 목록 밖 | warning |
| 물량 | 사례 배출량과 비슷한지 | 연 환산 배출량과 30% 넘게 다름 | warning |
| 안내 | 서류로 확인할 수 없는 의무 | 위탁계약서 3년 보관, 적법 수탁자 확인 | info |

- **error** = 법 필수 항목이 빠졌거나 틀림 → 고쳐야 함
- **warning** = 법 위반은 아니지만 이 경로·사례와 달라 확인 필요
- **info** = 안내

처리계획서는 먼저 대상인지 본다: 경로가 지정폐기물이 아니거나 `plan_check.decision`이 `not_required`면 `not_applicable`, 아직 `needs_info`면 `check_required`.

## 4. 요청 형식 (파트 3 → 파트 2): DocumentReviewRequest

```json
{
  "schema_version": "1.0",
  "request_id": "DR-0001",
  "case_id": "DEMO-PART2-01",
  "route_id": "DEMO-PART2-01-G2-01",
  "doc_type": "entrust_contract",
  "doc_version": 1,
  "fields": { … 서류별 칸 (아래) … },
  "attachments": ["capacity_check"]
}
```

- 서류를 **칸별 값(fields)** 으로 보낸다. 파트 3이 서류를 데이터에서 만들기 때문에 이 방식이 가장 정확하다. PDF·이미지 서류를 읽는 것은 v0에 없다.
- `attachments`: 같이 갖춘 서류 이름 목록 (문자열 또는 `{"type": …}`).

### entrust_contract (위탁계약서)

| 칸 | 뜻 | 확인 |
|---|---|---|
| `consignor.name` | 위탁자(배출자) 상호 | 필수 |
| `consignee.name`, `consignee.company_id` | 수탁자 상호, 파트 2 업체 키 | 필수, 추천·예비 업체인지 |
| `waste_items[].waste_code` | 폐기물 분류번호 | 필수, 경로와 같은지 |
| `waste_items[].quantity_ton_year` | 수량 (톤/년) | 필수, 사례와 ±30% |
| `waste_items[].treatment_code` | 처리방법 코드 (있으면) | 경로와 같은지 |
| `waste_items[].properties` | 성질과 상태 | 필수 |
| `waste_items[].handling_cautions` | 취급 시 주의사항 | 필수 |
| `contract.start_date`, `contract.end_date` | 계약 기간 (YYYY-MM-DD) | 필수, 순서 |
| `contract.fee` | 위탁 비용 | 필수 |
| `contract.signed_date` | 서면 체결일 | 필수 |
| attachments `capacity_check` | 수탁처리능력 확인서 | 필수 |

### capacity_check (수탁처리능력 확인서, 별지 제5호서식)

| 칸 | 서식 번호 | 확인 |
|---|---|---|
| `consignor.name`, `biz_no`, `representative`, `address` | ①②③⑤ | 필수 |
| `waste_items[].waste_code`, `state`, `ton_year` | ⑥ | 필수, state ∈ {1, 2, 기타}, 경로·물량 |
| `consignee.name`, `biz_no`, `address`, `company_id` | ⑦⑧⑪ | 필수, 추천·예비 업체인지 |
| `consignee.permit_no`, `permit_agency` | ⑭ | 필수 |
| `capacity.acceptable` | 수탁 가능 여부 | "가능"이어야 |
| `treatment_items[].treatment_code` | (4) 처분·재활용 방법 | 경로와 같은지 |
| attachments `permit_copy` / `guarantee_copy` | 첨부 | 허가증 사본 필수 / 이행보증 사본 확인 |

### treatment_plan (폐기물 처리계획서, 별지 제12호서식)

| 칸 | 서식 번호 | 확인 |
|---|---|---|
| `submitter.name`, `biz_no`, `address` | ①②⑤ | 필수 |
| `business.industry` | ⑥ 업종 (세세분류) | 필수 |
| `business.main_materials`, `main_products`, `process` | ⑦⑧⑨ (제18조의2 제3항) | 필수 |
| `waste_items[].waste_code` | ⑪ 지정폐기물 세분류 | 필수, 경로와 같은지 |
| `waste_items[].kg_month`, `ton_year` | ⑫ 배출량 | 필수, 사례와 ±30% |
| `waste_items[].cycle` | ⑬ 배출주기 | 필수 |
| `waste_items[].state` | ⑭ 성질·상태 | 고상·액상·기타 |
| `waste_items[].transporter` | ⑮ 운반자 | 필수 |
| `waste_items[].treatment_type` | 처리구분 | 자가·위탁 |
| `waste_items[].treatment_company` | 업소명 | 위탁이면 필수, 추천·예비 업체인지 |
| `waste_items[].treatment_code` | 처리방법 | 필수, 경로와 같은지 |
| `start_date`, `submit_date` | 사업 개시·발생일, 제출일 | 1개월 이내 |
| attachments `capacity_check`, `analysis_result` | 첨부 | 위탁이면 확인서, 분석결과서 |

## 5. 결과 형식 (파트 2 → 파트 3): DocumentReviewResult

```json
{
  "schema_version": "1.0",
  "request_id": "DR-0001", "case_id": "DEMO-PART2-01", "case_version": 1,
  "route_id": "DEMO-PART2-01-G2-01",
  "doc_type": "entrust_contract", "doc_name": "위탁계약서", "doc_version": 1,
  "reviewed_at": "2026-11-17T11:30:00",
  "verdict": "fix_required",
  "summary": {"error": 2, "warning": 1, "info": 2},
  "findings": [
    {"req_id": "EC-10", "severity": "error", "item": "취급 시 주의사항",
     "field": "waste_items[0].handling_cautions", "problem": "missing",
     "found": null, "expected": null,
     "message": "취급 시 주의사항이 없습니다",
     "basis": "WMA_RULE:16의13/호3@20261112",
     "quote": "다. 위탁하는 폐기물의 성질과 상태 및 취급 시 주의사항",
     "basis_status": "draft"}
  ],
  "notes": ["검토 기준표가 초안(draft)입니다. 기준표 확정 전까지 결과는 참고용입니다"]
}
```

| verdict | 뜻 | 파트 3 화면 |
|---|---|---|
| `pass` | error·warning 없음 | 통과 (info만 안내) |
| `fix_required` | error 있음 | 고칠 칸 표시 (`field`) |
| `check_required` | warning만, 또는 처리계획 대상 여부 미확인 | 확인 요청 |
| `not_applicable` | 이 경로에는 필요 없는 서류 | 서류 불필요 안내 |
| `rejected_request` | doc_type·route_id 잘못 | 요청 다시 보내기 |

| findings 칸 | 뜻 |
|---|---|
| `field` | 문제 칸 위치 (목록은 `waste_items[0].x`) — 화면에서 그 칸 강조 |
| `problem` | missing(빠짐) / invalid(형식·기한 틀림) / mismatch(경로·사례와 다름) / info |
| `found`, `expected` | 서류 값, 기대 값 |
| `basis`, `quote` | 근거 조문 키, 원문 문장 |
| `basis_status` | draft(기준표 검토 전) / confirmed |

## 6. 코드·실행

```
src/route_agent/src/doc_review/
├── __init__.py              review(), load_requirements(), context()
├── __main__.py              명령줄 실행
├── build_requirements.py    기준표 만들기 + 원문 대조(--verify) + 확정(--confirm)
├── doc_requirements.csv     기준표 (61행, 지금 draft)
└── test_doc_review.py       15개
```

```bash
source "$HOME/5.Agent_System/프로젝트/waste_agent/env.sh"
cd "$ROOT/src/route_agent/src"
python3 -m pytest -q doc_review                                    # 테스트
python3 doc_review/build_requirements.py --verify                  # 서버 원문(chunk_v2)과 인용·시행일 대조
python3 doc_review/build_requirements.py "$WASTE_DATA_DIR/doc_requirements.csv" --confirm   # 통과 행 confirmed 로 active 에 저장
python3 -m doc_review 요청.json --proposal /tmp/p2/route_proposals.json --case "$T/part1_sample.json" --out 결과.json
```

- 기준표는 `WASTE_DATA_DIR/doc_requirements.csv`를 먼저 읽고, 없으면 코드 폴더의 초안을 쓴다.
- 인용 문장은 이 작업 공간의 2026.10.02판 원문으로 대조했다 (61행 통과). 근거 키는 11.12 개정판 기준(제16조의13)이라 **서버에서 `--verify`로 한 번 더 대조**해야 한다.

## 7. 파트 3과 정할 것

1. 위 요청 형식(fields 칸 이름)으로 보낼 수 있는지, 칸이 다르면 이름 맞추기
2. 서류를 언제 보내는지 (초안 저장할 때마다 / 제출 전 한 번)
3. 결과 화면: error는 "고쳐야 함", warning은 "확인 필요"로 나눠 보여 주기
4. 다음 판에 넣을 서류 (배출자 신고서, 분석결과서)
5. PDF·이미지 서류도 검토가 필요한지 (필요하면 다음 판에서 글자 추출 후 같은 규칙 적용)
