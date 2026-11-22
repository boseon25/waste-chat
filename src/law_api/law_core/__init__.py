"""법령 수집·감시 공통 모듈 (law_chunk.py · law_watch.py가 같이 씀)

targets  대상 법령 목록
api      국가법령정보 OPEN API (인증키 LAW_OC, mask)
chunker  원문 JSON → 청크
parts    필요한 부분 고르기 · 감시 대상 판단
store    저장 위치(data/law)와 형식
"""
