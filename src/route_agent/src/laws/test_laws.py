"""근거 키·법령 이름 → 법령 목록 테스트. 판정표 없이 돌아간다.

실행: $PY -m pytest -v laws/test_laws.py
"""
import pytest

from laws import collect, parse


@pytest.mark.parametrize("basis, law, article, date", [
    ("WMA_RULE:B4의3@20261002#7aeb303d9fe8dcf0", "폐기물관리법 시행규칙", "별표 4의3", "2026-10-02"),
    ("WMA_RULE:B5:3@20261002#8b91", "폐기물관리법 시행규칙", "별표 5 제3호", "2026-10-02"),
    ("WMA:13의2/항2@20260918#d3", "폐기물관리법", "제13조의2 제2항", "2026-09-18"),
    ("WMA:17/항1/호3@20260918", "폐기물관리법", "제17조 제1항 제3호", "2026-09-18"),
    ("WMA_DEC:B4의3@20260326", "폐기물관리법 시행령", "별표 4의3", "2026-03-26"),
    ("CEA_DESIG:B2@20260514#3d", "순환자원 지정 등에 관한 고시", "별표 2", "2026-05-14"),
    ("CEA_RECOG:*@20251001", "순환자원 인정 절차 및 방법 등에 관한 고시", "전체", "2025-10-01"),
    ("GUIDE_DECL:§Ⅷ.1@20251231", "사업장폐기물 배출자신고 및 처리계획 확인 업무처리지침", "Ⅷ.1", "2025-12-31"),
    ("WMA_RULE:BF5@처리구분@20261002#e8", "폐기물관리법 시행규칙", "별지 제5호서식 처리구분", "2026-10-02"),
    ("WMA_RULE:66/항5@20261002", "폐기물관리법 시행규칙", "제66조 제5항", "2026-10-02"),
    ("폐기물관리법 제25조", "폐기물관리법", "제25조", None),
    ("폐기물관리법 시행규칙 별표 4의2 R-10", "폐기물관리법 시행규칙", "별표 4의2 R-10", None),
])
def test_parse(basis, law, article, date):
    p = parse(basis)
    assert (p["law"], p["article"], p["effective_date"]) == (law, article, date)


def test_parse_unknown():
    assert parse("") is None and parse("아무 글") is None


def test_collect_merges_same_article():
    route = {
        "title": "재생이용",
        "evidence_ids": ["WMA_RULE:B4의3@20261002#aa"],
        "conditions": [{"text": "허가 업체에 위탁", "basis": "폐기물관리법 제25조"}],
        "r_code_detail": [{"r_code": "R-3-3", "description": "원료 제조", "basis": "WMA_RULE:B4의3@20261002#aa"}],
        "required_tests": [{"test": "사전 분석", "r_codes": ["R-3-3"], "basis": "폐기물관리법 시행규칙 별표 4의3 (사전분석확인)"}],
    }
    laws = collect(route)
    b43 = [l for l in laws if l["article"] == "별표 4의3"]
    assert len(b43) == 1 and len(b43[0]["used_for"]) == 3            # 판정, 유형, 시험이 한 항목으로
    assert b43[0]["key"] == "WMA_RULE:B4의3@20261002#aa"
    assert any(l["article"] == "제25조" for l in laws)


def test_collect_reports_unknown_basis():
    laws = collect({"title": "t", "conditions": [{"text": "x", "basis": "알 수 없는 근거"}]})
    assert laws[-1]["law"] is None and "알 수 없는 근거" in laws[-1]["used_for"][0]
