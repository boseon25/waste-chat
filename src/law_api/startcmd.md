# 법령 API 받기·정제·저장 확인 명령 (2026-11-16)

## 0. 준비 (새 터미널마다)

```bash
source "/home/a202192012/5.Agent_System/프로젝트/waste_agent/env.sh"
read -s -p "LAW_OC: " LAW_OC && export LAW_OC && echo     # API를 새로 부를 때만 (받기·check)
cd "$ROOT/src/law_api"
echo $ROOT                                                 # waste_agent 로 끝나면 정상
```

## 1. 받기 (API → 원문 + 조각)

```bash
python3 law_chunk.py WMA_RULE        # 시행규칙만
python3 law_chunk.py                 # 대상 22개 전부
```

결과
- 원문: `$ROOT/data/law/raw/WMA_RULE/{시행일}_{일련번호}.json`
- 조각: `$ROOT/data/law/chunk_v2/WMA_RULE/{시행일}.jsonl`

## 2. API 원문 구조 보기

```bash
python3 law_watch.py inspect WMA_RULE
```

## 3. 정제된 조각 보기

```bash
# 제18조의2 조문 전체 (항·호 개수 counts 포함)
grep '"key": "18의2",' "$ROOT/data/law/chunk_v2/WMA_RULE/"*.jsonl | tail -1 | cut -d: -f2- | python3 -m json.tool

# 제18조의2 제1항 제1호 조각 하나
grep '"key": "18의2/항1/호1"' "$ROOT/data/law/chunk_v2/WMA_RULE/"*.jsonl | tail -1 | cut -d: -f2- | python3 -m json.tool

# 조각 종류별 개수 (최신 시행일 파일)
F=$(ls "$ROOT/data/law/chunk_v2/WMA_RULE/"*.jsonl | tail -1)
python3 -c "import json,sys,collections;print(collections.Counter(json.loads(l)['type'] for l in open(sys.argv[1])))" "$F"
```

`grep`에 파일이 여러 개 걸리면 줄 앞에 파일 이름이 붙어요. `cut -d: -f2-`가 그 부분을 떼어 줘요.

## 4. 감시 (기준선·변경 보고)

```bash
python3 law_watch.py baseline        # 처음 한 번, 또는 targets.py를 바꾼 뒤
python3 law_watch.py check           # 주 1회
cat "$ROOT/data/law/reports/$(date +%F)_check.md"
```

## 5. 발췌 (판정 표가 읽는 원문)

```bash
python3 law_watch.py extract WMA_RULE
head -1 "$ROOT/data/law/watched/WMA_RULE.jsonl" | python3 -m json.tool
less "$ROOT/data/law/watched/WMA_RULE.md"
```

## 6. 저장된 파일 한눈에

```bash
ls "$ROOT/data/law"
ls "$ROOT/data/law/chunk_v2/WMA_RULE" "$ROOT/data/law/raw/WMA_RULE"
ls "$ROOT/data/law/watched" "$ROOT/data/law/reports"
```

## 7. 판정 표 근거 키 점검

```bash
python3 law_watch.py refs
cat "$ROOT/data/law/reports/$(date +%F)_refs.md"
```

## 주의

- `raw/`와 `registry.json`은 공유·업로드하지 않는다.
- 인증키는 `read -s`로만 넣는다. 파일·채팅에 쓰지 않는다.
- "원문 없음"이 나오면 `echo $ROOT`로 waste_agent인지 먼저 확인한다.