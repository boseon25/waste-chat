# 파트 2 경로·매칭 엔진 개발 계획표

- **기준 문서:** 파트 2 공통 명세 v1.0 (2026.11.08 수정)
- **개발 방식:** 도메인별 모듈 분리 + 단계별 구현
- **핵심 원칙:** Python 규칙 엔진 중심, LangGraph는 실행 흐름 관리, LLM은 보조 역할
- **비용:** 계산하지 않음 (`cost_total = null`)
- **탄소:** 운송 및 공정 탄소 배출량 계산 포함

---

## 1. 전체 개발 순서

| 순서 | 개발 단계 | 주요 작업 | 핵심 산출물 | 우선순위 |
|---:|---|---|---|---|
| 1 | 프로젝트 기반 구축 | 폴더 구조, 환경 설정, 공통 설정 및 예외 처리 | 프로젝트 기본 구조 | 필수 |
| 2 | 데이터 스키마 | WasteCase, RouteProposal, PartnerFeedback 등 정의 | Pydantic 스키마 | 필수 |
| 3 | 데이터 저장소 | 규정, 재활용 사례, 업체, 탄소계수 조회 | Repository | 필수 |
| 4 | 폐기물 분류 | 지정폐기물, 재활용 금지, 분류번호 및 관문 판정 | Classification Engine | 필수 |
| 5 | 경로 생성 | 관문별 재사용·재활용·에너지 회수·처분 경로 생성 | Route Engine | 필수 |
| 6 | 경로 검증 | 기술적 적용 가능성, 법적 조건, 업체 수용 조건 검증 | Validation Engine | 필수 |
| 7 | 정보 요청 | 누락 정보 확인, 추가 분석 항목 및 질문 생성 | Information Engine | 필수 |
| 8 | 업체 매칭 | 업체 허가 범위, 처리 역할, 운반 및 처리 능력 검증 | Partner Engine | 필수 |
| 9 | 탄소 계산 | 운송 및 재활용 공정별 탄소 배출량 계산 | Carbon Engine | 필수 |
| 10 | 경로 비교 | 경로별 우선순위, 조건, 탄소 배출량 비교 | Comparison Engine | 필수 |
| 11 | 업체 회신 처리 | 업체 승인·거절·조건 변경 및 이력 관리 | Feedback Engine | 필수 |
| 12 | 근거 관리 | 법령, 재활용 사례, 공정 데이터의 근거 연결 | Evidence Engine | 필수 |
| 13 | LLM 연동 | 질문 정제, 경로 설명, 조건 누락 추출 | LLM Module | 후순위 |
| 14 | LangGraph 통합 | 전체 처리 단계 연결 및 조건별 분기 | Route Pipeline | 후순위 |
| 15 | 통합 테스트 | 명세서 기준 시나리오 검증 및 Part 1·3 연동 | 통합 시스템 | 필수 |

---

## 2. 폴더별 개발 계획

| 모듈 | 담당 기능 | 주요 파일 |
|---|---|---|
| `schemas/` | 입력·출력 데이터 구조 정의 | `waste_case.py`, `route.py`, `partner.py`, `feedback.py` |
| `repositories/` | CSV 및 데이터베이스 조회 | `rule_repository.py`, `partner_repository.py`, `carbon_repository.py` |
| `classification/` | 폐기물 분류 및 관문 판정 | `waste_classifier.py`, `hazardous_checker.py`, `prohibited_checker.py` |
| `route_engine/` | 재활용·처분 경로 생성 | `gate_router.py`, `route_builder.py`, `material_generator.py` |
| `validation/` | 경로별 기술·법적 조건 검증 | `technical_validator.py`, `regulatory_validator.py`, `permit_validator.py` |
| `information/` | 정보 부족 항목 및 질문 생성 | `missing_info_checker.py`, `question_builder.py` |
| `partner_engine/` | 업체 검색 및 역할별 매칭 | `partner_searcher.py`, `permit_matcher.py`, `capacity_checker.py` |
| `carbon_engine/` | 탄소 배출량 계산 | `carbon_calculator.py`, `transport_calculator.py`, `process_calculator.py` |
| `comparison/` | 경로 우선순위 및 비교 | `route_ranker.py`, `route_comparator.py` |
| `feedback/` | 업체 회신 및 경로 상태 갱신 | `feedback_processor.py`, `rejection_handler.py`, `idempotency.py` |
| `evidence/` | 법령·사례 근거 관리 | `evidence_searcher.py`, `evidence_validator.py` |
| `llm/` | 자연어 처리 보조 | `question_refiner.py`, `route_explainer.py` |
| `pipeline/` | 전체 실행 순서 및 분기 관리 | `route_pipeline.py`, `route_graph.py` |

---

## 3. 단계별 상세 구현 계획

### 1단계. 프로젝트 기반 구축 및 데이터 스키마

| 작업 | 구현 내용 | 완료 기준 |
|---|---|---|
| 프로젝트 구조 | 모듈별 디렉터리 구성 | 기본 실행 가능 |
| 공통 설정 | 환경변수, 경로, 설정값 관리 | 설정 중복 제거 |
| 예외 처리 | 공통 예외 및 오류 코드 정의 | 예외 처리 일관성 확보 |
| WasteCase | Part 1 입력 데이터 구조 정의 | 입력 검증 가능 |
| RouteProposal | 경로 결과 데이터 구조 정의 | 명세 필드 반영 |
| PartnerFeedback | 업체 회신 데이터 구조 정의 | 회신 검증 가능 |
| Evidence | 근거 데이터 구조 정의 | 근거 연결 가능 |

### 2단계. 데이터 저장소 구축

| 데이터 | 파일 | 주요 용도 |
|---|---|---|
| 지정폐기물 기준 | `designated_waste.csv` | 지정폐기물 판정 |
| 재활용 금지 기준 | `prohibited_waste.csv` | 관문 진입 제한 |
| 재활용 규칙 | `recycling_rules.csv` | 재활용 가능 조건 확인 |
| 관문 규칙 | `gate_rules.csv` | 관문별 처리 순서 |
| R 코드 매핑 | `r_code_tier_map.csv` | 재활용 유형 및 단계 매핑 |
| 재활용 사례 | `case_library.csv` | 유사 사례 검색 |
| 재활용 업체 | `partners_recycling.csv` | 재활용 업체 검색 |
| 운반 업체 | `partners_transport.csv` | 운반 업체 검색 |
| 처분 업체 | `partners_disposal.csv` | 소각·매립 업체 검색 |
| 운송 탄소계수 | `transport_factors.csv` | 운송 탄소 계산 |
| 공정 탄소계수 | `process_factors.csv` | 공정 탄소 계산 |
| 기존 처리 코드 | `treatment_code_map.csv` | 기존 처리 경로 탄소 비교 |

### 3단계. 폐기물 분류 및 관문 판정

| 순서 | 기능 | 처리 내용 |
|---:|---|---|
| 1 | 입력 검증 | 필수 입력 및 누락 정보 확인 |
| 2 | 지정폐기물 판정 | 지정폐기물 여부 및 의심 여부 확인 |
| 3 | 재활용 금지 판정 | 금지 대상 여부 확인 |
| 4 | 분류번호 판정 | 폐기물 분류번호 후보 확인 |
| 5 | 관문 판정 | 관문 1~4 진입 여부 결정 |
| 6 | 판정 결과 생성 | 판정 근거 및 불확실성 기록 |

**핵심 규칙**

- 재활용 금지 폐기물은 관문 1~3을 건너뛰고 관문 4만 검토합니다.
- 지정폐기물 의심 시 지정폐기물 기준으로 업체 후보를 검색합니다.
- 분류번호 1순위 확신도가 0.7 미만이면 2순위도 검토합니다.
- 2순위 기반 경로는 규제 검토 상태를 `conditional`로 유지합니다.

### 4단계. 재활용 및 처분 경로 생성

| 관문 | 경로 유형 | 구현 내용 |
|---|---|---|
| 관문 1 | 지정 순환자원 활용 | 별도 처리 없이 판매·활용하는 경로 |
| 관문 2-① | 재사용·수리 | 재사용 및 수리 경로 생성 |
| 관문 2-② | 재생이용 | 한 재질, 분리 처리, 혼합 상태 가공 경로 생성 |
| 관문 2-③ | 에너지 회수 | 재생이용이 곤란한 경우 검토 |
| 관문 3 | 조건부 개별 인정 | 장기 검토 후보로 생성 |
| 관문 4 | 처분 | 소각 및 매립 경로 생성 |

**핵심 구현 사항**

- 관문 2의 처리 순서를 유지합니다.
- 혼합물의 구성비가 확인되지 않으면 가능한 경로를 조건부로 생성합니다.
- 관문 3은 즉시 실행 가능한 경로로 취급하지 않습니다.
- 관문 4에는 소각·매립만 포함합니다.

### 5단계. 경로별 검증

| 검증 유형 | 주요 확인 사항 | 결과 상태 |
|---|---|---|
| 기술 검증 | 성분, 오염도, 물성, 공정 적용 가능성 | `confirmed`, `conditional`, `needs_info`, `rejected` |
| 규제 검증 | 폐기물 분류, 법적 기준, 허가 범위 | 동일 |
| 수용 검증 | 업체 처리 가능 여부, 수탁 용량, 조건 | 동일 |

세 가지 검증 상태는 독립적으로 관리합니다. 한 항목이 `confirmed`라고 해서 전체 경로를 확정하지 않습니다.

### 6단계. 정보 요청 생성

| 작업 | 구현 내용 |
|---|---|
| 누락 정보 확인 | 경로별 필수 조건과 입력값 비교 |
| 추가 분석 확인 | 성분 분석, 오염도, 구성비 등 필요한 시험 확인 |
| 질문 생성 | 사용자 및 업체에 전달할 질문 생성 |
| 정보 갱신 | 추가 정보 입력 후 해당 경로 재검증 |

정보가 부족하더라도 모든 경로의 실행을 중단하지 않고, 독립적으로 검토할 수 있는 경로는 계속 처리합니다.

### 7단계. 업체 매칭

| 업체 역할 | 주요 기능 |
|---|---|
| `transporter` | 폐기물 운반 |
| `preprocessor` | 선별·세척·파쇄 등 전처리 |
| `recycler` | 재활용 공정 수행 |
| `treatment` | 소각·매립 등 처분 |
| `residue_handler` | 공정 잔재물 처리 |
| `end_user` | 재생제품 최종 활용 |

**핵심 구현 규칙**

- 운반·전처리·재활용·처분 업체를 각각 검색합니다.
- 한 업체가 모든 공정을 수행한다고 가정하지 않습니다.
- 업체가 직접 운반하면 운반 단계를 제거합니다.
- 혼합물은 구성된 모든 분류번호에 대해 허가 범위를 확인합니다.
- 수탁처리능력이 초과되면 `conditional`로 처리합니다.
- 지정폐기물 의심 시 일반폐기물 업체를 후보에서 제외합니다.
- 업체 검색 결과가 없으면 가짜 업체를 생성하지 않고 정보 요청을 생성합니다.

### 8단계. 탄소 계산

| 계산 항목 | 계산 방식 |
|---|---|
| 운송 탄소 | 운송 거리 × 물량 × 운송 배출계수 |
| 공정 탄소 | 공정 활동량 × 공정 배출계수 |
| 전체 탄소 | 운송 탄소 + 공정 탄소 |
| 탄소 절감량 | 기존 경로 탄소 − 후보 경로 탄소 |

- 근거가 있는 항목만 계산합니다.
- 필요한 계수가 없으면 `carbon_total = null`로 반환합니다.
- 탄소 절감량이 음수인 경우에도 그대로 표시합니다.
- 비용 계산은 수행하지 않으며 `cost_total = null`로 유지합니다.

### 9단계. 경로 비교 및 업체 회신

| 기능 | 구현 내용 |
|---|---|
| 경로 비교 | 경로별 검증 상태 및 조건 비교 |
| 우선순위 | 명세서에 정의된 경로 순서 반영 |
| 업체 승인 | 업체 수용 상태 갱신 |
| 업체 거절 | 거절 사유 기록 및 대안 경로 탐색 |
| 운반 조건 변경 | 운반 단계 재구성 |
| 중복 회신 방지 | 동일 `feedback_id` 중복 반영 방지 |
| 버전 관리 | 경로 변경 및 이전 결과 이력 보존 |

### 10단계. LLM 및 LangGraph 통합

| 구분 | 담당 기능 | 제한 사항 |
|---|---|---|
| LLM | 질문 문장 정제 | 판정 결과 변경 금지 |
| LLM | 경로 설명 생성 | 근거 없는 내용 생성 금지 |
| LLM | 누락 조건 추출 | 검증 엔진의 결과를 대체하지 않음 |
| LLM | 업체 회신 필드 추출 | 추출 결과 별도 검증 |
| LangGraph | 실행 단계 연결 | 규칙 자체를 결정하지 않음 |
| LangGraph | 조건별 분기 | Python 판정 결과에 따라 분기 |

---

## 4. 최종 프로젝트 디렉터리

```text
project/
│
├── src/
│   ├── part1_waste/
│   │
│   ├── part2_routes/
│   │   ├── schemas/
│   │   ├── repositories/
│   │   ├── classification/
│   │   ├── route_engine/
│   │   ├── validation/
│   │   ├── information/
│   │   ├── partner_engine/
│   │   ├── carbon_engine/
│   │   ├── comparison/
│   │   ├── feedback/
│   │   ├── evidence/
│   │   ├── llm/
│   │   ├── pipeline/
│   │   ├── services/
│   │   └── utils/
│   │
│   ├── part3_partners/
│   │
│   └── main.py
│
├── data/
│   ├── raw/
│   │   ├── regulations/
│   │   ├── recycling_cases/
│   │   ├── partners/
│   │   └── carbon_factors/
│   │
│   ├── processed/
│   │   ├── rules/
│   │   ├── recycling_cases/
│   │   ├── partners/
│   │   ├── carbon_factors/
│   │   └── mappings/
│   │
│   ├── samples/
│   └── migrations/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── fixtures/
│
├── scripts/
│   ├── import_regulations.py
│   ├── import_recycling_cases.py
│   ├── import_partners.py
│   └── validate_datasets.py
│
├── docs/
│   ├── architecture.md
│   ├── data_dictionary.md
│   ├── api_contract.md
│   ├── route_rules.md
│   ├── partner_matching.md
│   └── carbon_calculation.md
│
├── .env
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── uv.lock
```

# Part 2 프로젝트 디렉터리 구조

```text
project/
│
├── src/
│   │
│   ├── part1_waste/
│   │
│   ├── part2_routes/
│   │   │
│   │   ├── schemas/                       # 데이터 구조
│   │   │   ├── waste_case.py
│   │   │   ├── route.py
│   │   │   ├── partner.py
│   │   │   ├── feedback.py
│   │   │   └── common.py
│   │   │
│   │   ├── data/                          # 데이터 조회
│   │   │   ├── rule_repository.py
│   │   │   ├── partner_repository.py
│   │   │   ├── case_repository.py
│   │   │   └── carbon_repository.py
│   │   │
│   │   ├── preliminary/                   # 기본 검토
│   │   │   ├── preliminary_checker.py     # 기본 검토 실행
│   │   │   ├── prohibited_checker.py      # A. 금지·제한 검사
│   │   │   └── hazardous_checker.py       # B. 지정폐기물 판정
│   │   │
│   │   ├── gates/                         # 관문별 경로 생성
│   │   │   │
│   │   │   ├── gate1.py                   # 관문 1: 지정 순환자원 활용
│   │   │   │
│   │   │   ├── gate2/                    # 관문 2: 재활용
│   │   │   │   ├── processor.py           # 전체 처리 및 순서 관리
│   │   │   │   ├── reuse.py               # ① 재사용·수리
│   │   │   │   ├── material.py            # ② 재생이용
│   │   │   │   └── energy.py              # ③ 에너지 회수
│   │   │   │
│   │   │   ├── gate3.py                   # 관문 3: 순환자원 인정
│   │   │   └── gate4.py                   # 관문 4: 소각·매립
│   │   │
│   │   ├── validation/                    # 경로 검증
│   │   │   ├── technical.py               # 기술 검증
│   │   │   ├── regulatory.py              # 법령·규제 검증
│   │   │   └── condition.py               # 처리 조건 검증
│   │   │
│   │   ├── matching/                      # 업체 매칭
│   │   │   ├── partner_matcher.py         # 업체 검색 및 역할별 매칭
│   │   │   ├── permit_checker.py          # 허가·신고 범위 확인
│   │   │   ├── transport.py               # 운반 업체 연결
│   │   │   └── capacity.py                # 처리 용량 검증
│   │   │
│   │   ├── information/                   # 추가 정보 요청
│   │   │   ├── missing_info.py            # 누락 정보 확인
│   │   │   └── question_builder.py        # 사용자·업체 질문 생성
│   │   │
│   │   ├── carbon/                        # 탄소 계산
│   │   │   ├── calculator.py              # 전체 탄소 계산
│   │   │   └── treatment_mapper.py        # 기존 처리 코드 매핑
│   │   │
│   │   ├── comparison/                    # 경로 비교
│   │   │   └── route_comparator.py
│   │   │
│   │   ├── feedback/                      # 업체 회신
│   │   │   ├── feedback_processor.py
│   │   │   └── route_updater.py
│   │   │
│   │   ├── evidence/                      # 근거 및 법령 버전 관리
│   │   │   ├── evidence_manager.py
│   │   │   └── law_version_checker.py
│   │   │
│   │   ├── llm/                           # LLM 보조 기능
│   │   │   ├── question_refiner.py
│   │   │   └── route_explainer.py
│   │   │
│   │   ├── pipeline/                      # 전체 실행 흐름
│   │   │   ├── route_pipeline.py
│   │   │   └── route_graph.py
│   │   │
│   │   ├── utils/                         # 공통 기능
│   │   │   ├── enums.py
│   │   │   ├── constants.py
│   │   │   └── exceptions.py
│   │   │
│   │   └── main.py
│   │
│   ├── part3_partners/
│   │
│   └── main.py
│
├── data/
│   │
│   ├── raw/                               # 원본 데이터
│   │   ├── regulations/
│   │   ├── recycling_cases/
│   │   ├── partners/
│   │   ├── recognized/
│   │   └── carbon_factors/
│   │
│   ├── processed/                         # 정제 데이터
│   │   ├── rules/
│   │   │   ├── prohibited_restricted.csv
│   │   │   ├── hazardous_codes.csv
│   │   │   ├── recycling_types_4_3.csv
│   │   │   ├── rcode_detail_4_2.csv
│   │   │   ├── gate3_rules.csv
│   │   │   ├── disposal_methods_5.csv
│   │   │   ├── incineration_rules.csv
│   │   │   ├── condition_texts.csv
│   │   │   └── rules_config.yaml
│   │   │
│   │   ├── gate1/
│   │   │   ├── designated_resources.csv
│   │   │   ├── designated_common_rules.csv
│   │   │   └── demand_designated.csv
│   │   │
│   │   ├── gate2/
│   │   │   ├── case_library.csv
│   │   │   └── srf_quality.csv
│   │   │
│   │   ├── gate3/
│   │   │   ├── recognized.csv
│   │   │   └── recognized_precedents.csv
│   │   │
│   │   ├── partners/
│   │   │   ├── partners_recycling_2024.csv
│   │   │   ├── reporter_scope_16.csv
│   │   │   ├── reporter_scope_66.csv
│   │   │   ├── partners_transport.csv
│   │   │   ├── partners_disposal.csv
│   │   │   └── partner_capacity.csv
│   │   │
│   │   ├── carbon/
│   │   │   ├── treatment_code_map_v1.csv
│   │   │   ├── transport_factors.csv
│   │   │   └── process_factors.csv
│   │   │
│   │   └── mappings/
│   │       ├── waste_code_map.csv
│   │       └── r_code_tier_map.csv
│   │
│   ├── samples/
│   │   ├── waste_cases.json
│   │   ├── feedback.json
│   │   ├── expected_routes.json
│   │   ├── input_samples_50.json
│   │   └── virtual_allbaro_scenarios.xlsx
│   │
│   └── migrations/
│
├── monitoring/                            # 법령 변경 감시
│   ├── law_versions.csv
│   ├── law_watchlist.md
│   ├── law_change_plan.md
│   └── chunk_mapping.csv
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── fixtures/
│
├── scripts/
│   ├── import_regulations.py
│   ├── import_recycling_cases.py
│   ├── import_partners.py
│   ├── import_carbon_factors.py
│   └── validate_datasets.py
│
├── docs/
│   ├── architecture.md
│   ├── data_dictionary.md
│   ├── api_contract.md
│   ├── route_rules.md
│   ├── partner_matching.md
│   └── carbon_calculation.md
│
├── .env
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── uv.lock
```

---

## 4-2. 핵심 모듈 역할

| 모듈 | 담당 기능 |
|---|---|
| `preliminary/` | 금지·제한 검사와 지정폐기물 판정 |
| `gates/` | 관문 1~4 경로 생성 |
| `validation/` | 기술·규제·처리 조건 검증 |
| `matching/` | 재활용·전처리·운반·처분 업체 연결 |
| `information/` | 누락 정보 및 추가 질문 생성 |
| `carbon/` | 운송·공정·처분 탄소 계산 |
| `comparison/` | 경로별 조건 및 우선순위 비교 |
| `feedback/` | 업체 회신 반영 및 경로 재판정 |
| `evidence/` | 법령·사례 근거 및 버전 관리 |
| `llm/` | 질문 정제 및 경로 설명 |
| `pipeline/` | 전체 실행 순서 및 조건별 분기 |

---

## 4-3. 전체 실행 흐름

```text
WasteCase (Part 1)
       │
       ▼
[기본 검토]
       │
       ├── A. 금지·제한 검사
       │
       └── B. 지정폐기물 판정
       │
       ▼
[관문별 경로 생성]
       │
       ├── 관문 1: 지정 순환자원 활용
       │
       ├── 관문 2:
       │      ① 재사용·수리
       │      ② 재생이용
       │      ③ 에너지 회수
       │
       ├── 관문 3: 순환자원 인정 장기 후보
       │
       └── 관문 4: 소각·매립
       │
       ▼
[공통 검증 및 업체 매칭]
       │
       ├── 기술·규제 검증
       ├── 업체 허가·신고 범위 확인
       ├── 운반 업체 연결
       ├── 처리 용량 확인
       ├── 조건 문구 연결
       └── 탄소 계산
       │
       ▼
[경로 비교]
       │
       ▼
RouteProposal
       │
       ▼
Part 3 업체 연결
       │
       ▼
PartnerFeedback
       │
       ▼
경로 재판정
```

---

## 4-4. 개발 우선순위

| 순서 | 구현 대상 | 목표 |
|---:|---|---|
| 1 | `schemas/`, `data/` | 입력·출력 및 데이터 조회 기반 |
| 2 | `preliminary/` | 금지·제한 및 지정폐기물 판정 |
| 3 | `gates/gate1.py` | 지정 순환자원 경로 |
| 4 | `gates/gate2/` | 재사용·재생이용·에너지 회수 |
| 5 | `gates/gate3.py`, `gate4.py` | 인정 후보 및 처분 경로 |
| 6 | `validation/`, `information/` | 조건 검증 및 추가 정보 요청 |
| 7 | `matching/` | 업체 및 운반 연결 |
| 8 | `carbon/`, `comparison/` | 탄소 계산 및 경로 비교 |
| 9 | `feedback/`, `evidence/` | 회신 및 근거 관리 |
| 10 | `llm/`, `pipeline/` | LLM 및 LangGraph 통합 |
| 11 | `tests/` | 전체 통합 및 회귀 테스트 |

**설계 원칙:** 기능별 독립성은 유지하되, 파일을 지나치게 쪼개지 않습니다. 하나의 기능이 복잡해지거나 여러 모듈에서 재사용될 때만 별도 파일로 분리합니다.

---

## 5. 개발 진행 관리표

| 단계 | 작업 | 선행 단계 | 완료 여부 |
|---:|---|---|---|
| 1 | 프로젝트 기본 구조 생성 | - | [ ] |
| 2 | 데이터 스키마 정의 | 1 | [ ] |
| 3 | 데이터 저장소 구현 | 2 | [ ] |
| 4 | 폐기물 분류 엔진 구현 | 3 | [ ] |
| 5 | 관문별 경로 생성 구현 | 4 | [ ] |
| 6 | 기술·규제 검증 구현 | 5 | [ ] |
| 7 | 정보 요청 생성 구현 | 6 | [ ] |
| 8 | 업체 검색 및 매칭 구현 | 7 | [ ] |
| 9 | 탄소 계산 구현 | 8 | [ ] |
| 10 | 경로 비교 구현 | 9 | [ ] |
| 11 | 업체 회신 및 버전 관리 구현 | 10 | [ ] |
| 12 | 근거 관리 구현 | 5 | [ ] |
| 13 | LLM 보조 기능 연결 | 7, 11, 12 | [ ] |
| 14 | LangGraph 파이프라인 연결 | 13 | [ ] |
| 15 | Part 1·3 통합 테스트 | 14 | [ ] |

---

## 6. 개발 시 반드시 지켜야 할 원칙

1. **규칙 엔진과 LLM 분리:** 법적·기술적 판정은 Python 규칙 엔진에서 수행합니다.
2. **근거 추적:** 모든 경로는 `evidence_ids` 및 `evidences`로 근거를 추적할 수 있어야 합니다.
3. **불확실성 보존:** 미확인 정보는 임의로 추정하지 않고 `null`, `needs_info`, `conditional`로 관리합니다.
4. **업체 역할 분리:** 운반·전처리·재활용·처분 단계를 독립적으로 매칭합니다.
5. **비용 계산 제외:** `cost_total = null`로 유지합니다.
6. **탄소 계산 포함:** 근거가 있는 운송·공정 배출량만 계산합니다.
7. **동일 입력 동일 판정:** 규칙 엔진은 결정론적으로 동작해야 합니다.
8. **변경 이력 보존:** 업체 회신 및 경로 변경은 버전과 함께 기록합니다.

---

## 7. 권장 개발 전략

처음부터 모든 파일을 생성하기보다는 핵심 엔진부터 순차적으로 구현합니다.

| 개발 구간 | 구현 범위 | 목표 |
|---|---|---|
| 1차 | 스키마 + Repository + Classification | 입력 및 폐기물 판정 |
| 2차 | Route Engine + Validation | 관문별 경로 생성 및 검증 |
| 3차 | Information Engine | 부족한 정보와 추가 질문 생성 |
| 4차 | Partner Engine | 업체 검색 및 역할별 매칭 |
| 5차 | Carbon Engine + Comparison | 탄소 계산 및 경로 비교 |
| 6차 | Feedback + Evidence | 업체 회신 및 근거 관리 |
| 7차 | LLM + LangGraph | 자연어 보조 및 전체 파이프라인 통합 |
| 8차 | Integration Test | Part 1·3 연동 및 명세 검증 |

**최종 목표:** Part 1에서 전달된 폐기물 정보를 바탕으로 적합한 처리 경로를 생성하고, 기술·규제·업체 조건을 검증한 뒤 탄소 배출량과 함께 비교하여 Part 3으로 전달하는 독립적인 경로·매칭 엔진을 구축합니다.
