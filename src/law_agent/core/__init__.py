"""route_agent 에서 복사해 온 공용 코드 (법령 조회, 사례 자료형, 경로 자료형, 업체 매칭, 탄소, 추천, 출력 도우미).

route_agent 는 비교 기준(정답)이라 import 하지 않는다. 복사한 파일마다 머리에 원본 경로와 바꾼 점을 적었다.
판정표(settings.yaml blocked_tables)를 읽는 코드는 가져오지 않았다:
  gates/tables.py · gate1~4.py (판정표로 경로 생성)   → agents/composer.py (판단 결과로)
  proposal/documents.py (condition_texts·plan_check)   → output/proposal.py (G·H 판단으로)
  proposal/related_laws.py (적용 법령 목록이 코드에 있음) → 판단의 인용 조문
  law_tables/ (energy·designated_check·plan_check)     → E-1·A-3·H-2 판단
  preliminary/ 사전검증                                  → A·B 판단
"""
