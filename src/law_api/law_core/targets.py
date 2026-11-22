"""대상 법령 목록 (law_chunk·law_watch 공통, 파트2_법령_감시목록.md 기준)

kind     law = 현행법령(시행일, target=eflaw) / admrul = 행정규칙
grade    1 = 판정표·서류 근거로 직접 씀 (개정되면 판정 표 검토)
         2 = 참고 (원문만 받아 둠. 감시 조문이 없으면 "감시 외 변경"으로만 보고)
articles 감시 조문 (조문번호 기준, "13의2" = 제13조의2)
         목록 ["13의2", …] 또는 번호: 제목 {"16의13": "위탁ㆍ수탁의 기준 및 절차", …}
         제목을 적어 두면 check가 "그 번호의 지금 제목"과 비교해 번호 이동을 잡는다.
         제목이 ""이면 registry.json에 저장된 제목과만 비교한다.
         지금 청크의 제목을 이 형식으로 뽑기: python law_watch.py titles WMA_RULE
annexes  감시 별표 (별표번호 기준, "4의3" = 별표 4의3 / "F5" = 별지 제5호서식 / "*제목" = 제목 포함)
         조문과 같이 목록 또는 {번호: 제목}
parts    실제로 필요한 부분만 (추출 단위)
  조문      "17" 제17조 전체 / "17/항5" 제5항 / "17/항1/호3" 제1항 제3호 / "2/호4" 항 없는 조문의 제4호
  별표      "B4의3" 전체 / "B5:4.나.6)" 제4호 나목 6) / "BF5@처리구분" 그 글자가 나오는 줄부터 끝까지
  행정규칙  "*" 본문 전체 / "11" 제11조 / "§Ⅷ" "§Ⅷ.1" 목차형 지침의 장·절

2026-11-16 추가
- 폐기물관리법 시행규칙: 제3조(에너지 회수기준), 제9조(운반 기준), 제14조의3(PCB), 제16조의13(위탁·수탁),
  제17조의2(지정폐기물 확인), 제18조(배출자 신고), 제18조의2(처리계획 확인), 별표 17의2
  (위탁·수탁 기준은 2026.11.12 개정으로 제16조의12 → 제16조의13. 새 제16조의12는 음식물류 공동처리)
- 폐기물관리법 시행령 제7조, 폐기물관리법 제45조
- 2등급 참고: 자원재활용법(법·시행령), 건설폐기물법·전기전자제품 자원순환법·화학물질관리법(각 법·시행령·시행규칙)
"""

TARGETS = [
    # ── 1등급 ──────────────────────────────────────────────────────────
    dict(law_id="WMA", kind="law", grade=1, name="폐기물관리법",
         articles=["2", "13의2", "17", "18", "25", "45", "46"], annexes=[],
         parts=["2/호4", "13의2/항2", "17/항1/호1", "17/항1/호3", "17/항5", "18/항1", "18/항3", "25/항5",
                "45", "46/항1", "46/항5"]),
    dict(law_id="WMA_DEC", kind="law", grade=1, name="폐기물관리법 시행령",
         articles=["3", "5", "7", "7의2", "7의3"], annexes=["1", "3", "4의2", "4의3"],
         parts=["3", "5", "7", "7의2", "7의3", "B1", "B3", "B4의2", "B4의3"]),
    dict(law_id="WMA_RULE", kind="law", grade=1, name="폐기물관리법 시행규칙",
         articles={"3": "", "9": "", "14의3": "", "16의13": "위탁ㆍ수탁의 기준 및 절차",
                   "17의2": "", "18": "", "18의2": "", "66": ""},
         annexes=["1", "4", "4의2", "4의3", "5", "5의3", "5의4", "16", "17", "17의2", "F5"],
         parts=["3/항1", "9", "14의3/항3", "16의13", "17의2", "18", "18의2",
                "66/항3", "66/항4", "66/항5", "66/항6",
                "B1", "B4", "B4의2", "B4의3", "B5:3", "B5:4", "B5:6.가.7)", "B5의3", "B5의4",
                "B16", "B17", "B17의2", "BF5@처리구분"]),
    dict(law_id="CEA", kind="law", grade=1, name="순환경제사회 전환 촉진법",
         articles=["3", "21", "23"], annexes=[],
         parts=["3/호4", "21/항1", "23/항1"]),
    dict(law_id="CEA_DEC", kind="law", grade=1, name="순환경제사회 전환 촉진법 시행령",
         articles=["11"], annexes=[], parts=["11"]),
    dict(law_id="CEA_RULE", kind="law", grade=1, name="순환경제사회 전환 촉진법 시행규칙",
         articles=["12"], annexes=[], parts=["12/항1", "12/항2"]),
    dict(law_id="RRA_RULE", kind="law", grade=1, name="자원의 절약과 재활용촉진에 관한 법률 시행규칙",
         articles=["13", "20의2"], annexes=["*고형연료제품의 품질"],
         parts=["13", "20의2", "B*고형연료제품의 품질"]),
    dict(law_id="CEA_DESIG", kind="admrul", grade=1, name="순환자원 지정 등에 관한 고시",
         articles=[], annexes=["*"], parts=["B1", "B2"]),

    # ── 2등급 (기존) ───────────────────────────────────────────────────
    dict(law_id="CEA_RECOG", kind="admrul", grade=2, name="순환자원 인정 절차 및 방법 등에 관한 고시",
         articles=[], annexes=[], parts=["*"]),
    dict(law_id="ALLBARO_NOTICE", kind="admrul", grade=2, name="폐기물 전자정보처리 프로그램 운영 및 사용 등에 관한 고시",
         articles=["11", "13"], annexes=[], parts=["11", "13"]),
    dict(law_id="GUIDE_DECL", kind="admrul", grade=2, name="사업장폐기물 배출자신고 및 처리계획 확인 업무처리지침",
         articles=[], annexes=[], parts=["§Ⅵ", "§Ⅶ.2", "§Ⅷ.1"]),

    # ── 2등급 참고 (2026-11-16 추가, 판정표에서 아직 안 씀) ──────────────
    dict(law_id="RRA", kind="law", grade=2, name="자원의 절약과 재활용촉진에 관한 법률",
         articles=["12의3", "13", "16", "18"], annexes=[], parts=["12의3", "13", "16", "18"]),
    dict(law_id="RRA_DEC", kind="law", grade=2, name="자원의 절약과 재활용촉진에 관한 법률 시행령",
         articles=["18"], annexes=[], parts=["18"]),
    dict(law_id="CWA", kind="law", grade=2, name="건설폐기물의 재활용촉진에 관한 법률",
         articles=[], annexes=[], parts=[]),
    dict(law_id="CWA_DEC", kind="law", grade=2, name="건설폐기물의 재활용촉진에 관한 법률 시행령",
         articles=[], annexes=[], parts=[]),
    dict(law_id="CWA_RULE", kind="law", grade=2, name="건설폐기물의 재활용촉진에 관한 법률 시행규칙",
         articles=[], annexes=[], parts=[]),
    dict(law_id="EWA", kind="law", grade=2, name="전기ㆍ전자제품 및 자동차의 자원순환에 관한 법률",
         articles=[], annexes=[], parts=[]),
    dict(law_id="EWA_DEC", kind="law", grade=2, name="전기ㆍ전자제품 및 자동차의 자원순환에 관한 법률 시행령",
         articles=[], annexes=[], parts=[]),
    dict(law_id="EWA_RULE", kind="law", grade=2, name="전기ㆍ전자제품 및 자동차의 자원순환에 관한 법률 시행규칙",
         articles=[], annexes=[], parts=[]),
    dict(law_id="CMA", kind="law", grade=2, name="화학물질관리법",
         articles=[], annexes=[], parts=[]),
    dict(law_id="CMA_DEC", kind="law", grade=2, name="화학물질관리법 시행령",
         articles=[], annexes=[], parts=[]),
    dict(law_id="CMA_RULE", kind="law", grade=2, name="화학물질관리법 시행규칙",
         articles=[], annexes=[], parts=[]),
]

BY_ID = {t["law_id"]: t for t in TARGETS}


def ids(v) -> list[str]:
    """articles·annexes가 목록이든 {번호: 제목}이든 번호 목록으로"""
    return list(v.keys()) if isinstance(v, dict) else list(v or [])


def title_of(v, key) -> str:
    """기록해 둔 제목 (목록이면 "")"""
    return (v.get(key) or "") if isinstance(v, dict) else ""


def pick(law_ids: list[str] | None) -> list[dict]:
    """명령줄에서 고른 법령만. 없는 ID면 오류."""
    if not law_ids:
        return TARGETS
    bad = [x for x in law_ids if x not in BY_ID]
    if bad:
        raise SystemExit(f"없는 법령 ID: {bad}\n가능: {list(BY_ID)}")
    return [BY_ID[x] for x in law_ids]