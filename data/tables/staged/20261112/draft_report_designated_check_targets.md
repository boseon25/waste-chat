# LLM 초안 보고서 — designated_check_targets
생성: 2026-11-16 14:20  모델: anthropic/claude-sonnet-5.5  프롬프트: v3  퓨샷: 1개

| 항목 | 값 |
|---|---|
| 청크 | 2 (캐시 재사용 2, API 호출 0) |
| 토큰 | 입력 0 (캐시 읽기 0) / 출력 0 |
| 통과 행 | 12 (confidence low 0) |
| 탈락 행 | 0 → designated_check_targets_rejected.csv |

## 사람이 볼 것
- confidence low 행, 탈락 행
- 조건 칸이 원문 뜻과 같은지
- 승인한 행만 남기고 build_tables.py diff → promote