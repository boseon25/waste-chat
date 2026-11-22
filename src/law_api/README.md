# law_watch · 법령 감시 스크립트

국가법령정보 OPEN API로 감시 대상 11개(파트2_법령_감시목록.md 1·2등급)의 원문을 받아 저장하고, 개정 여부를 감지해 보고서를 만듭니다.
**판정 표(CSV)는 수정하지 않습니다.** 원문 저장 + 보고서까지만 합니다.

## 준비
- Python 3.9 이상, 추가 설치 없음 (표준 라이브러리만 사용)
- 인증키를 환경변수로 설정 (코드·문서에 적지 않음)
  - macOS/Linux: `export LAW_OC=발급받은_키`
  - Windows (PowerShell): `$env:LAW_OC="발급받은_키"`

## 실행
```
python law_watch.py baseline          # 처음 1회: 원문 저장 + 기준선(data/registry.json)
python law_watch.py check             # 이후 주 1회: 기준선과 비교, 보고서 생성
python law_watch.py inspect WMA_DEC   # 저장된 원문의 키 구조 확인 (파서 점검)
python law_watch.py extract           # 필요한 부분(parts)만 뽑아 data/watched/에 저장
python law_watch.py extract WMA "17/항5"   # 특정 부분만
```

## 흐름
1. `baseline` 1회 → `extract` 1회: 현재 원문과 필요한 부분 발췌를 만든다.
2. 매주 `check`: 조문·별표 단위로 비교. 바뀐 법령이 있으면
   - 보고서에 바뀐 조문과 **그 안에서 바뀐 항·호**를 적고
   - 그 법령의 발췌(`data/watched/{law_id}.md`)를 **자동으로 새 원문으로 갱신**한다.
   - 이전 시행일 발췌는 `data/watched/history/{law_id}_{시행일}.md`에 남는다 → 두 파일을 나란히 비교
3. 판정 표(CSV)는 자동으로 바꾸지 않는다. 보고서와 발췌를 보고 사람이 staged 표를 만든다.

## 청크 구조
- `article` / `annex`: 조문·별표 전체 (본문만, 메타정보 제외) → **변경 감지 단위**
- `article_part`: 항·호 단위 하위 청크 (조문 제목 줄 + 항 첫 문장 포함) → 검색·인용용
- `annex_part`: 별표의 최상위 호(1., 2., …) 단위 하위 청크
- 키 예: `13의2/항2/호4`, `2/호4`, `B5:4.`
호출 사이 1.2초 간격이 들어가 있어 baseline은 1분 안쪽으로 끝납니다.

## 만들어지는 파일
```
data/
├─ raw/{law_id}/{시행일}_{일련번호}.json   원문 그대로 (덮어쓰지 않음)
├─ chunks/{law_id}/{시행일}.jsonl         조문·별표 청크 (chunk_key, 정규화 해시, 본문)
├─ watched/{law_id}.md, .jsonl           필요한 부분 발췌 (최신)
├─ watched/history/{law_id}_{시행일}.md   시행일별 발췌 (덮어쓰지 않음)
├─ registry.json                         기준선: 시행일·번호·감시 대상 해시
└─ reports/{날짜}_{baseline|check}.md     결과 보고서
```

## 보고서 읽는 법
| 표시 | 뜻 | 할 일 |
|---|---|---|
| 변경 없음 | 시행일·일련번호 같음 | 없음 |
| 번호만 변경 | 새 일련번호지만 본문 해시 같음 | 기록만 |
| 명칭·감시 외 변경 | 감시하지 않는 조문만 바뀜, 또는 부처명 등 명칭만 바뀜 | 기록만 |
| 내용 변경 (감시 대상) | 감시 조문·별표의 정규화 해시가 바뀜 | 신구조문대비표 확인 → staged 표 작성 → 회귀 테스트 |
| ⚠️ 못 찾은 감시 대상 | 감시하려는 조문·별표를 응답에서 못 찾음 | `inspect`로 키 구조 확인 후 파서 수정 |

## 첫 실행 후 확인할 것
API 응답의 JSON 키 이름(조문단위, 별표단위, 조문번호 등)은 활용방법의 XML 태그 이름을 기준으로 추정해 만들었습니다. baseline 결과에 ❌나 "⚠️ 못 찾은 감시 대상"이 나오면 해당 law_id로 `inspect`를 실행해 출력 앞부분을 공유해 주세요. 파서를 실제 구조에 맞추겠습니다.

## 테스트 모드
`LAW_MOCK_DIR=폴더` 를 지정하면 네트워크 대신 그 폴더의 JSON을 읽습니다 (가짜 응답으로 동작 확인용).

---

# build_tables.py · 원문 → 판정 표(CSV) 자동 생성

```
python build_tables.py build            # data/watched/WMA_RULE.jsonl → data/staged/{시행일}/*.csv + build_report.md
python build_tables.py diff  [시행일]   # staged 와 active 비교 → diff_report.md (추가·삭제·변경 행)
python build_tables.py promote 시행일   # 사람이 diff·회귀 테스트 확인 후: active → old/{오늘}, staged → active
```

## 만드는 표 (코드 파서, LLM 없음)
| 표 | 원문 | 내용 |
|---|---|---|
| waste_codes_4.csv | 시행규칙 별표 4 | 분류번호 전체, 구분(지정·사업장일반·생활), 상위, 명칭, 말단 여부 |
| hazardous_codes.csv | 별표 4 제1호 | 지정폐기물 말단 분류번호 |
| rcode_detail_4_2.csv | 시행규칙 별표 4의2 | R코드 52개 설명 + tier(설계 규칙) |
| recycling_types_4_3.csv | 시행규칙 별표 4의3 | 분류번호 × R코드 한 줄씩, 묶음 번호, 사전분석·확인(해당/해당 없음), 재활용 금지 |
| not_in_4_3.csv | 별표 4 − 4의3 | 4의3에 유형이 없는 분류번호 → 비고 2(재활용환경성평가)만 가능 |
| treatment_code_map.csv | 별지 제5호서식 참고란 | 처리방법 코드 69개 + config/treatment_design.csv 의 설계 칸 |

## 법 칸과 설계 칸
- 원문에서 나오는 칸(분류번호, 명칭, R코드, 해당 여부, 처리방법 코드)은 빌더가 매번 새로 만든다.
- tier·탄소계수 키·운반 필요 여부 같은 **설계 판단**은 `config/*.csv`에 사람이 적는다. 빌더는 붙이기만 하고,
  원문에 새 코드가 생기면 보고서에 "설계값 없음"으로 띄운다.

## 자동 검증 (build_report.md "확인 필요")
- 4의3 분류번호가 별표 4에 있는지, R코드가 4의2에 있는지
- 묶음마다 사전분석 여부가 "해당/해당 없음"인지, R코드 칸에 다른 글자가 없는지
- 별표 4와 4의3 명칭 차이(참고용)

## 주간 흐름
```
law_watch.py check  →(변경 있으면 watched 자동 갱신)→  build_tables.py build
→ build_report.md 경고 확인 → build_tables.py diff → 회귀 테스트 50건 → 승인 → 시행일에 promote
```

## prohibited_restricted.csv (관문 A, LLM 없음)
- 규칙(무엇을 막는지, action, 질문)은 `config/prohibited_rules.csv`에 사람이 적고, **분류번호는 빌더가 별표 4의3 "재활용 금지"와 별표 4에서 채운다.**
- `분류번호_선택`: `금지:07`(4의3 재활용 금지 중 07로 시작), `전체:10-12-06`, `미정:*`(4의3에 유형 없음), 비우면 입력 기반 규칙.
- 모든 규칙의 `인용` 문구가 data/watched 원문에 실제로 있는지 검사 → `인용_검증` 칸. 개정으로 문구가 바뀌면 "불일치"로 뜬다.
- PR-R0: 4의3에서 재활용 금지인 모든 코드를 막는 안전망. 개별 규칙이 없는 금지 코드는 보고서에 뜬다.

---

# draft_tables.py + llm_client.py · 문장 원문 → 표 초안 (LLM)

```
uv add anthropic
$env:ANTHROPIC_API_KEY="발급받은_키"        # 코드·문서에 적지 않음
uv run python draft_tables.py list               # 표·모델·원문 범위
uv run python draft_tables.py draft gate3_rules  # 하나씩 (작은 표부터)
uv run python draft_tables.py draft all
uv run python draft_tables.py eval gate3_rules gold\gate3_rules.csv   # 골드셋 비교
```
- `llm_client.py`: API 호출만 담당 (도구 호출로 JSON 강제, temperature 0, 프롬프트 캐시, 재시도). 모델 이름은 여기 `MODELS`.
- `draft_tables.py`: 표별 원문 범위·스키마·지시(`TASKS`), 청크 분할, 검증, 캐시, 보고서.
- 검증: 근거_인용이 원문에 글자 그대로 있는지 / 분류번호·R코드·처리코드가 실제 표에 있는지 → 실패 행은 `{표}_rejected.csv`.
- 캐시: `data/llm_cache/{표}/` — 원문·프롬프트·모델이 같으면 다시 부르지 않음 → 개정 시 바뀐 청크만 호출.
- 퓨샷: `fewshot/{표}.jsonl` ({"source": 원문 일부, "rows": [정답 행]}). 인용이 현행 원문에 없으면 경고.
- 모델 바꾸기: `$env:LLM_MODEL_CONDITION_TEXTS="haiku"` (표 이름 대문자). 모델을 바꾸면 캐시도 새로 만듦.
- 테스트(API 안 부름): `$env:LLM_MOCK="1"`.
- 결과는 최신 `data/staged/{시행일}/`에 들어가고 status=draft. 사람이 검토 후 `build_tables.py diff` → `promote`.

---

# regress_tables.py · 회귀 테스트 (판정결과_50)

```
python3 regress_tables.py staged   # promote 전: 최신 staged 표로 검사
python3 regress_tables.py          # data/active 표로 검사
```
- 사례 50건: `tests/regress_cases.csv` (판정결과_50.xlsx 의 기대값 + 사례별 사실: 품목 해당, 조건, 상대 종류·범위, 일 용량)
- 법 판단은 표에서만: 관문A prohibited_restricted / 관문1 designated_resources / 관문2 recycling_types_4_3 + reporter_scope
- 결과: `{표 폴더}/regress_report.md`, 실패가 있으면 종료 코드 1 → 실패 0건일 때만 promote
- 개정으로 기대값이 정말 바뀐 경우에만 regress_cases.csv 를 고친다.

주간 흐름: `check → build → (draft) → diff → regress_tables.py staged → promote`

---

# build_partners.py · 비법령 데이터(상대 기업) 정제

```
python3 build_partners.py recycling "폐기물 재활용실적 및 업체현황(2024년).xlsx"
python3 build_partners.py demand    "관문1 수요기업 후보.xlsx"
```
- 결과는 최신 `data/staged/{시행일}/`에 법령 표와 함께 → diff / regress / promote 그대로
- `partners_recycling.csv`: 업체 × 폐기물코드 × 처리방법코드. 처리방법 "(2003)원료 제조(재)(위탁)" → 코드·R코드·자가/위탁. `위탁가능=N`(자가처리만)은 상대 후보 제외
- `waste_code_crosswalk.csv`: 업체현황의 4자리(5103)·2자리(06) 코드 → 분류번호 말단 목록
- `demand_designated.csv`: 관문 1 수요기업. 품목·분류번호·용도를 지정 고시 표와 대조 (`검증` 칸)
- 업체_키 = 법인 표기·공백 제거한 이름 + 시군구 ("(주) 승지" = "(주)승지")
- 회귀 테스트는 `partners_recycling.csv`가 있으면 상대 기업을 데이터에서 직접 조회
