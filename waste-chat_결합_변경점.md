# waste_agent.zip 과 waste-chat 결합 (2026-11-22)

이 폴더는 `waste_agent.zip`(2026-11-21)을 그대로 풀고, 아래만 바꾼 것이다. zip 새 판을 받으면 아래 항목만 다시 적용하면 된다.

## 실행
- 플랫폼: `./waste-chat.sh` (= `cd src/waste_chat && python app.py`)
- 엔진 점검(명령줄): `./waste-admin.sh status | detail | questions | test …` (화면 없음)
- 가상환경: `.venv` → `/Users/boseon/code/agent/.venv-waste` 바로가기

## 바꾼 것
| 위치 | 내용 |
|---|---|
| `src/waste_chat/` | zip 의 waste_chat(= waste-chat 3 판)을 **waste-chat 4** 로 교체. 법령 엔진 사본(`engine/agent`)은 없애고 이 폴더의 `src/law_agent`·`src/part1`·`data/` 를 바로 쓴다(`services/paths.py`). |
| `src/law_agent/web/` (admin·user·api) | **삭제** — 로그인 없는 관리자·사용자 웹 화면. 사용자 화면은 waste-chat 이 대신한다. |
| `src/law_agent/stack.py` | **삭제** — 웹 화면·감시·waste-chat 을 한꺼번에 켜던 도구. |
| `src/law_agent/bridge/docx_codes.py` | **추가** — 입력서 분류번호 칸 읽기(원래 `web/user.py` 안의 함수 그대로). `cli.py`·waste-chat 이 쓴다. |
| `src/law_agent/cli.py` | `web` 명령 제거, `docx_codes` 가져오는 곳만 바꿈. |
| `src/law_agent/launch.py` | `up/down/ps/demo/logs`(stack) 제거, 키 파일 점검(`key_warnings`)을 stack 에서 옮김. |
| `waste-admin.sh`, `src/law_agent/bin/waste-admin.sh` | `web`·`up …` 줄 제거. |
| `*.sh` | 실행 권한 복원(zip 풀 때 빠짐). |
| `waste-chat.sh`, 이 문서 | 추가. |

## 뺀 파일 (zip 풀 때)
- 키가 든 `.env` 4개(`src/law_agent/llm`, `src/law_api`, `src/waste_chat`, `src/카카오_모빌리티`), `.swp`, `law_agent_step*.zip` 백업, `__pycache__`·`.pytest_cache`.
- 플랫폼 키는 `src/waste_chat/.env`(waste-chat 4 것). 명령줄 엔진(`./waste.sh send` 등)으로 실제 모델을 부르려면 `src/law_agent/llm/.env` 에 `TIMELY_API_KEY` 가 따로 있어야 한다.

## 흐름
1. 입력: Word 기초 입력서 → 파트 1(`db_intake` → `phase2` 확인 질문 → `part1 classification` → `part1 attach-history` → `phase3 export`) → law_agent `part1_v2.convert`. 또는 분류 에이전트 전달 파일(.json).
2. 판단: `law_agent.bridge.run_one` (별도 프로세스). 실제 판단은 `data/law_agent`(캐시·감사 기록)를 함께 쓰고, 시험용 답(키 없음·`WASTE_DRY=1`)은 앱 데이터 폴더에 따로 둔다.
3. 그 뒤(업체 요청·거래방·서류 작성·채팅·진행판)는 waste-chat 4 그대로.
