# 법령 API → 판정표 → 경로 판정 사용법 (2026.11.16)

국가법령정보 API로 법 원문을 받아, 판정에 쓰는 표(CSV)를 만들고, 사람이 확인한 뒤 경로 판정 프로그램이 읽게 하기까지의 구조와 실행 방법.

---

## 1. 한눈에 보기

```
① 받기        law_chunk.py             국가법령정보 API → 원문(raw) + 조문 조각(chunk_v2)
② 감시        law_watch.py check       시행일·내용 바뀜, 조문 번호 이동 → 보고서
③ 발췌        law_watch.py extract     필요한 조항만 → watched/
④ 표 만들기   build_tables.py build    숫자·목록이 분명한 원문 → 표 (코드)         ─┐
              draft_tables.py draft    문장으로 된 원문 → 표 초안 (LLM)            ─┤→ staged/{시행일}/  (모두 draft)
⑤ 대조        review_tables.py         표 ↔ 원문 자동 대조 → 통과 행 confirmed      ─┘
⑥ 비교·반영   build_tables.py diff / promote   staged → active (사람 승인)
⑦ 사용        pipline/pipeline.py      active 표 + chunk_v2 원문으로 경로 판정 → 파트 3·1에 전달
```

**원칙**
- 판정표는 사람이 승인(promote)하기 전에는 판정 코드에 들어가지 않는다 (판정 코드는 active만 읽음).
- 법 기준 표는 confirmed 행만 확정 판정에 쓴다. draft 행은 "확인 필요"로만 나간다.
- 인증키(LAW_OC, TIMELY_API_KEY)는 실행할 때 입력한다. 파일·문서·채팅에 쓰지 않는다.

---

## 2. 폴더

```
(MS)/
├── env.sh                                   경로 환경변수 (키는 넣지 않음)
├── src/law_api/              ①~⑥ 법령·표 도구
│   ├── law_core/        targets.py(대상 법령 22개·감시 조문) api.py chunker.py parts.py store.py
│   ├── law_chunk.py     ① 받기
│   ├── law_watch.py     ②③ 감시·발췌 (baseline / check / inspect / extract / refs)
│   ├── build_tables.py  ④⑥ 코드로 표 만들기, diff, promote
│   ├── draft_tables.py  ④ LLM 표 초안
│   ├── llm_client.py    LLM 호출 (Anthropic 직접 또는 타임리 AI)
│   ├── review_tables.py ⑤ 자동 대조
│   ├── config/          설계값 표 (사람이 적음): plan_check_items, energy_recovery_items, prohibited_rules, treatment_design
│   └── fewshot/         LLM 예시
├── src/route_agent/src/               ⑦ 경로 판정 (pipline/pipeline.py)
└── data/
    ├── law/
    │   ├── raw/{법령}/{시행일}_{일련번호}.json    API 원문 (OC 포함 가능 → 공유·업로드 금지)
    │   ├── chunk_v2/{법령}/{시행일}.jsonl         조문 조각 (판정·검증이 읽는 원문)
    │   ├── watched/                               감시 조항 발췌, history/
    │   ├── registry.json                          감시 기준선 (공유 금지)
    │   ├── reports/                               받기·감시 보고서
    │   └── llm_cache/                             LLM 응답 캐시
    ├── tables/
    │   ├── staged/{시행일}/   새로 만든 표 (draft) + build_report.md, diff_report.md
    │   ├── staged/review_{날짜}/   자동 대조 결과 + confirmed 판
    │   ├── active/            판정 코드가 읽는 표 (업체 표 등 다른 표도 여기)
    │   └── old/{날짜_시각}/   promote 전 백업
    └── reference/             시군구 중심점, 탄소 계수
```

---

## 3. 준비 (새 터미널마다)

```bash
source "/home/a202192012/5.Agent_System/프로젝트/산업자원_순환·재활용_에이전트(MS)/env.sh"
read -s -p "LAW_OC: " LAW_OC && export LAW_OC && echo            # 법령 API 인증키 (①②에 필요)
# LLM 초안을 만들 때만
export LLM_PROVIDER=timely
export TIMELY_BASE_URL=https://hello.timelyai.io/api/v2/chat/bridge/openai
read -s -p "TIMELY_API_KEY: " TIMELY_API_KEY && export TIMELY_API_KEY && echo
cd "$ROOT/src/law_api"
```

`env.sh`가 잡는 값:

| 변수 | 값 | 누가 읽나 |
|---|---|---|
| ROOT | 프로젝트 폴더 (env.sh 위치로 자동) | 명령 경로 |
| WASTE_DATA_DIR | `$ROOT/data/tables/active` | 판정 코드. 법령 도구는 이 값이 active여도 `data`로 올려 씀 |
| WASTE_REFERENCE_DIR | `$ROOT/data/reference` | 업체 거리·탄소 |
| WASTE_LAW_DIR | `$ROOT/data/law/chunk_v2` | 원문 조회·검증 |

---

## 4. 단계별 사용법

### ① 받기 — `law_chunk.py`

```bash
python3 law_chunk.py              # 대상 22개 전부 (1~2분)
python3 law_chunk.py WMA WMA_RULE # 고른 법령만
```
- 대상 목록: `law_core/targets.py` (1등급 = 판정에 씀, 2등급 = 참고)
- 결과: `chunk_v2/{법령}/{시행일}.jsonl`. 같은 시행일이면 덮어쓰고, 새 시행일이면 새 파일 (옛 판 보존)
- 조각 종류: article(조문 전체), article_part(항·호), annex(별표), annex_part(별표의 호), doc_part(지침 장·절)
- 조문 조각에는 원문 기준 항·호 개수(`counts`)가 들어간다 → 빠진 호 검증에 씀

### ② 감시 — `law_watch.py`

```bash
python3 law_watch.py baseline     # 처음 한 번 (또는 감시 목록을 바꾼 뒤): 기준선 만들기
python3 law_watch.py check        # 주 1회: 바뀐 것만 보고서
python3 law_watch.py refs         # 판정표 근거 키가 지금 원문과 맞는지 점검
python3 law_watch.py inspect WMA  # API 응답 구조 보기 (파서 고칠 때)
```
check 보고서 읽는 법:

| 표시 | 뜻 | 할 일 |
|---|---|---|
| 변경 없음 | 시행일·번호 같음 | 없음 |
| 번호만 변경 / 명칭·감시 외 변경 | 감시 조문 내용은 그대로 | 기록만 |
| 내용 변경 (감시 대상) | 감시 조문의 해시가 바뀜, 바뀐 항·호 표시 | ③~⑥ 다시 |
| 번호 이동 의심 | 감시 번호의 조문 제목이 달라짐 (예: 2026.11.12 위탁·수탁 기준 제16조의12 → 제16조의13) | targets.py 번호 고치기 → baseline |

### ③ 발췌 — `law_watch.py extract`

```bash
python3 law_watch.py extract              # 감시 조항 전부 → watched/{법령}.jsonl, .md
python3 law_watch.py extract WMA_RULE     # 한 법령만
```
④의 표 만들기는 watched에서 원문을 읽는다. ①이나 targets.py를 바꿨으면 extract부터 다시.

### ④ 표 만들기

**코드로 (숫자·목록이 분명한 원문)**
```bash
python3 build_tables.py build
```
| 표 | 원문 |
|---|---|
| waste_codes_4, hazardous_codes | 시행규칙 별표 4 (분류번호) |
| rcode_detail_4_2 | 별표 4의2 (재활용 유형) |
| recycling_types_4_3, not_in_4_3 | 별표 4의3 (분류번호별 허용 재활용 유형) |
| treatment_code_map | 별지 제5호서식 (처리방법 코드) + config/treatment_design.csv |
| designated_resources | 순환자원 지정 고시 별표 2 |
| prohibited_restricted | config/prohibited_rules.csv + 별표 4·4의3 |
| plan_check_targets | 시행규칙 제18조의2 (처리계획 확인 대상) + config/plan_check_items.csv |
| energy_recovery_criteria | 시행규칙 제3조 제1항 (에너지 회수기준) + config/energy_recovery_items.csv |

- 결과: `staged/{시행일}/` + `build_report.md` ("확인 필요" 목록)
- active에서 confirmed였던 행은 원문·값이 그대로면 confirmed를 이어받는다. 바뀐 행만 draft로 돌아가고 보고서에 뜬다.

**LLM으로 (문장으로 된 원문)**
```bash
python3 draft_tables.py list                              # 표·모델·원문 범위
python3 draft_tables.py draft designated_check_targets    # 표 하나
python3 draft_tables.py draft all
```
| 표 | 원문 |
|---|---|
| condition_texts | 법 제17·18조, 올바로 고시, 배출자신고 지침 (의무·서류 안내 문구) |
| reporter_scope | 법 제46조, 시행규칙 제66조·별표 16 (신고 업체 범위) |
| disposal_methods_5 | 시행규칙 별표 5 제3·4호 (처분 방법) |
| designated_check_targets | 시행규칙 제17조의2, 지침 Ⅵ.1 (지정폐기물 사전 확인) |
| gate3_rules | 순환경제법 (관문 3, 지금 안 씀) |

- 결과: 최신 staged 폴더에 `{표}.csv`(통과), `{표}_rejected.csv`(인용이 원문에 없거나 없는 분류번호), `draft_report_{표}.md`
- 같은 원문·프롬프트면 캐시를 써서 다시 부르지 않는다 (비용 0). 프롬프트를 고치면 `PROMPT_VERSION`을 올린다.
- 타임리 연결: 모델 `anthropic/claude-sonnet-5.5`. 함수 호출 강제를 받지 않는 모델이라 자동으로 `auto`로 바꿔 부른다 (보고서 stop_reason에 표시).

### ⑤ 대조 — `review_tables.py`

```bash
python3 review_tables.py              # 대조만
python3 review_tables.py --confirm    # 통과 행을 confirmed로 바꾼 판도 만들기
```
- 대상: plan_check_targets, energy_recovery_criteria, designated_check_targets
- 확인: 근거 조각이 지금 원문에 있는지·해시가 같은지, 표 문장이 원문에 글자 그대로 있는지, 숫자가 원문에 있는지, 분류번호가 별표 4에 있는지(명칭은 경고), 호 개수, 중복
- 결과: `staged/review_{날짜}/review_report.md` + confirmed 판 CSV
- 경고 행은 사람이 본다 (예: 폐페인트 → 05-01 폐유성·05-02 폐수성 페인트는 맞는 대응)

### ⑥ 비교·반영

```bash
cp "$ROOT/data/tables/staged/review_$(date +%Y%m%d)"/*.csv "$ROOT/data/tables/staged/{시행일}/"   # ⑤를 했으면
python3 build_tables.py diff            # staged ↔ active: 행 추가·삭제·변경
python3 build_tables.py promote {시행일}
```
- promote: active 전체를 `old/{날짜_시각}/`에 복사해 두고, staged의 CSV만 active에 덮어쓴다. 업체 표·condition_texts처럼 staged에 없는 파일은 그대로 둔다 (끝에 "그대로 둔 파일" 목록이 나와야 정상).
- 순서 주의: ⑤의 confirmed 판을 staged로 **복사한 뒤** promote. 복사를 빼면 draft 판이 올라간다.
- 되돌리기: `cp old/{날짜_시각}/*.csv active/`

### ⑦ 사용 — 경로 판정

```bash
cd "$ROOT/src/route_agent/src"
T="$ROOT/src/route_agent/data/test_data/start"
python3 -m pytest -q --ignore=preliminary/test_preliminary_e2e.py          # 회귀 테스트 (표를 바꾸면 꼭)
PYTHONPATH=.:pipline python3 pipline/pipeline.py "$T/part1_sample.json" --out /tmp/p2
PYTHONPATH=.:pipline python3 pipline/pipeline.py "$T/part1_sample.json" --agent --out /tmp/p2   # 에이전트 포함
```
| 옵션 | 뜻 |
|---|---|
| `--out` | 결과 폴더 (사례마다 JSON 1개) |
| `--no-partners` | 업체 매칭 없이 관문까지만 |
| `--top N` | 단계마다 업체 상위 N개 (기본 5) |
| `--feedback 파일` | 파트 3 업체 회신 반영 |
| `--agent` | 업체 미확보 경로를 LLM 에이전트로 대안 조사 (TIMELY_API_KEY 필요, 테스트는 `AGENT_MOCK=1`) |

판정표가 출력에 들어가는 곳:

| 표 | RouteProposal 칸 (→ 파트 3) |
|---|---|
| recycling_types_4_3, disposal_methods_5 등 | 경로, checks.regulatory, evidences |
| plan_check_targets | document_triggers[].plan_check (처리계획 서류 필요·불필요, 서류, 기한) |
| energy_recovery_criteria | requirements (발열량 충족·미충족, 업체 확인 항목) |
| condition_texts | document_triggers[].text (안내 문구) |
| designated_check_targets | InformationRequest designated_check (→ 파트 1, 배출업체 분석 요청) |
| chunk_v2 원문 | evidences[].text (발췌), related_laws (조문 원문 전체 + 빠짐 검증) |

---

## 5. 상황별 순서

**매주 (법 개정 확인)**
```bash
python3 law_watch.py check
# "변경 없음"이면 끝. 바뀐 게 있으면 ↓
python3 law_chunk.py {바뀐 법령}
python3 law_watch.py extract {바뀐 법령}
python3 build_tables.py build
python3 review_tables.py --confirm
# 보고서 확인 → 복사 → diff → promote → pytest
```

**조문 번호가 옮겨졌을 때 (예: 16의12 → 16의13)**
1. check 보고서에서 "번호 이동" 확인
2. `law_core/targets.py`의 번호 수정 → `law_watch.py baseline` → `extract`
3. 판정 코드의 관련 조문(`proposal/related_laws.py`)은 번호 + 제목으로 찾아서 자동으로 따라감. `law_watch.py refs`로 판정표 근거 키 점검

**표 하나만 LLM으로 다시 만들 때**
```bash
python3 draft_tables.py draft {표}
# rejected·보고서 확인, 필요하면 중복 행 정리 → status를 confirmed로 → staged/{시행일}/에 두고 diff → promote
```

---

## 6. 자주 나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `원문 없음: …/watched/WMA_RULE.jsonl` | extract를 안 함, 또는 데이터 경로가 다른 폴더 | `source env.sh` → `law_watch.py extract` |
| 결과가 `src/…/data/law_chunks`나 `_MS` 폴더에 생김 | 환경변수 없이 실행 / 폴더 이름 혼동 | `source env.sh`, `echo $ROOT`로 `(MS)` 확인 |
| promote 후 업체 매칭이 "파일 없음" | 예전 promote(폴더 통째 교체)를 씀 | `cp -n old/{최근}/* active/` 복구, build_tables.py 최신판 사용 |
| pipeline 결과가 계속 "확인 필요" | 표가 draft (confirmed 판을 staged로 복사하지 않고 promote) | `grep -c ',confirmed' active/{표}.csv` 확인 → ⑤⑥ 다시 |
| `No module named 'gates'` / `'pipeline'` | 실행 위치·모듈 경로 | `cd …/src` 후 `PYTHONPATH=.:pipline` |
| `tool_choice … not supported` | 모델이 함수 호출 강제를 안 받음 | llm_client.py 최신판 (자동으로 auto) |
| 제25조 등 "빠짐(complete=false)" | API가 신설 항 번호를 `<16>`로 줌 | chunker `norm_no`에서 `<>` 제거됨 → `law_chunk.py WMA` 다시 |
| 테스트가 옛 시행일(2026-10-02)로 실패 | 테스트가 날짜를 고정 | 테스트 최신판 (날짜 대신 형식 확인) |

---

## 7. 보안

- `data/law/raw/`, `registry.json`: API 요청 주소에 인증키(OC)가 들어갈 수 있어 공유·업로드 금지 (.gitignore)
- 오류 메시지·보고서에는 OC 값을 `OC=***`로 가린다
- 키를 채팅이나 문서에 붙였으면 바로 재발급
