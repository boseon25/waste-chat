# 폐기물 순환 거래 작업실 (waste-chat 5)

폐기물을 내보내는 업체(배출업체)가 **기초 입력서**를 올리면, 법령을 근거로 처리 경로와 맡길 업체를 추천하고, 그 업체와 **거래방**에서 대화하며 필요한 **서류를 함께 작성**하는 웹 서비스입니다.

- **입력**: Word 기초 입력서(.docx) 또는 분류 에이전트 전달 파일(.json)
- **법령 판단·업체 추천**: 법령 원문을 근거로 여러 AI 모델이 분류·지정폐기물 여부·처리 경로·필요한 서류를 판단하고, 공공자료 업체 중 허가 범위가 맞는 곳을 가까운 순으로 고릅니다. 업체마다 확인표와 거르기 과정을 보여 줍니다.
- **거래 이후**: 거래 요청 → 거래방(채팅·AI 도우미) → 서류 작성(서류 비서, 원본 나눠 넣기, 질문 모드) → 절차 안내

## 빠른 시작 (macOS · Linux)

Python 3.10 이상이 필요합니다(3.12 권장).

```bash
git clone https://github.com/boseon25/waste-chat.git
cd waste-chat
./waste-chat.sh setup      # 처음 한 번: 가상환경(.venv)과 패키지 설치, 키 파일 준비
./waste-chat.sh            # 실행 → 브라우저에서 http://127.0.0.1:8080
```

- 첫 화면의 **예시 업체로 둘러보기**에서 가상 업체를 눌러 바로 들어가 볼 수 있습니다.
- 다른 주소로 열려면 `./waste-chat.sh --port 8090`, 같은 네트워크의 다른 컴퓨터에서 접속하려면 `./waste-chat.sh --lan`.

### Windows

```bat
cd waste-chat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r src\waste_chat\requirements.txt
copy src\waste_chat\.env.example src\waste_chat\.env
cd src\waste_chat
..\..\.venv\Scripts\python app.py
```

## AI 키

`src/waste_chat/.env` 에 Timely 키를 넣습니다(이 파일은 git에 올라가지 않습니다).

```
TIMELYGPT_API_KEY=발급받은_키
```

키가 없어도 실행됩니다. 이때 법령 판단은 **시험용 답**(형식만 확인)으로 돌고, 채팅의 AI 답변은 기본 안내로 바뀝니다.

## 써 보기

1. 배출업체로 들어가 **새 거래 찾기** → `src/part1/fixtures/intake_records_example.docx`(예시 입력서)나 `src/handoff.json`(예시 전달 파일)을 올립니다.
2. 확인 질문에 답하고 **업체 찾기 + 법령 판단**을 누르면 30~90초 뒤 오른쪽에 **법령 기준 추천**이 나옵니다.
3. 추천 업체에 **거래 요청** → 상대 업체(예시 업체로 로그인)가 승인하면 거래방이 열립니다.
4. 거래방 **서류** 탭에서 서류를 작성합니다.

## 폴더

```
.
├─ waste-chat.sh            플랫폼 준비·실행
├─ src/waste_chat/          웹 서비스(화면·서류 작성·거래방). 자세한 기능은 src/waste_chat/README.md
├─ src/law_agent/           법령 판단·업체 추천 엔진
├─ src/part1/               기초 입력서 접수·확인 질문
├─ data/                    법령 원문·업체 표·엔진 기록
└─ waste-chat_결합_변경점.md   waste_agent 원본과 달라진 점
```

엔진만 명령줄로 점검할 때는 `./waste-admin.sh help`.

## 점검

```bash
cd src/waste_chat
for t in check_intake check_handoff check_desk check_prep check_link check_delete check_engine check_platform; do
  WASTE_DRY=1 ../../.venv/bin/python tools/$t.py | tail -1
done
```

## 알아 둘 점

- 법령 판단과 업체 추천은 법령 원문과 공공자료에 기댄 **사전 검토**입니다. 업체의 실제 허가 범위·반입 가능 여부와 신고 대상 여부는 해당 업체와 관할 기관에 확인해야 합니다.
- 예시 업체와 예시 입력서·전달 파일은 모두 **가상의 자료**입니다.
- 앱 데이터(가입 계정·거래 기록)는 `src/waste_chat/data/`에 저장되며 git에 올라가지 않습니다.
