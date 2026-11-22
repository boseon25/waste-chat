"""법령 누락 점검 (개발용). 실행 중 판정에는 쓰지 않는다.

판정표가 근거로 쓰는 조문에서 출발해
  1. 인용 그래프: 조문 안의 "제N조", "법 제N조", "영 제N조", "별표 N"을 따라가서
     1~2단계 이어지는데 판정표가 쓰지 않는 조문 → 누락 후보
  2. 절차 점검표: 처리 흐름 단계(배출 전 → 보관 → 계약 → 운반 → 인계 → 처리·재활용 → 사후)마다
     제목·본문 키워드로 관련 조문을 찾고, 판정표가 쓰는지 표시
결과는 사람이 보고 판정표·의무 문구 표에 넣을지 정한다 (자동 수정 없음).

  python -m law_audit --chunks <data/law/chunks> --tables <data/tables/active> --out <보고서 폴더>
"""
from .audit import audit, load_chunks, used_keys

__all__ = ["audit", "load_chunks", "used_keys"]
