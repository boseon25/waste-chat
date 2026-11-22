"""tools/law_tools.py 테스트 — 실제 lawstore 코드 + 가짜 조문 (서버 데이터 없이)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_agent.tools import law_tools as lt
from law_agent.core.lawstore import Chunk, LawStore

SAMPLE_CHUNKS = Path(__file__).with_name("chunk_sample_WMA_RULE.jsonl")

BOX = (
    "[별표 4의3] 폐기물의 종류별 재활용 유형(제4조의3 관련)\n"
    "┌────────┬────────┬────────┐\n"
    "│51-03-01│폐합성수지류│R-3-2, R-3-3│\n"
    "├────────┼────────┼────────┤\n"
    "│51-04-01│폐고무│R-4-1│\n"
    "├────────┼────────┼────────┤\n"
    "│R-9-1 행│에너지 회수│R-9-1│\n"
    "└────────┴────────┴────────┘"
)
ART = "제16조의13(위탁ㆍ수탁의 기준 및 절차)\n① 사업장폐기물배출자는 다음 각 호를 지켜야 한다.\n1. 수탁자를 확인할 것\n2. 서면으로 계약할 것"


def ch(law, key, title, text="", typ="article", counts=None, eff="20261112", h="h0"):
    return Chunk(law, key, typ, title, text or title, eff, h, None, counts)


@pytest.fixture
def store():
    s = LawStore()
    for c in [
        ch("WMA", "2", "정의", "제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다."),
        ch("WMA", "13의2", "폐기물의 재활용 원칙 및 준수사항", "제13조의2(폐기물의 재활용 원칙 및 준수사항) 시행규칙 별표 4의3에 따른다."),
        ch("WMA", "25", "폐기물처리업"),
        ch("WMA_DEC", "B1", "지정폐기물의 종류(제3조 관련)", "지정폐기물의 종류", typ="annex"),
        ch("WMA_RULE", "B4", "폐기물의 분류번호(제4조의2 관련)", "분류번호 표", typ="annex"),
        ch("WMA_RULE", "B4의3", "폐기물의 종류별 재활용 유형(제4조의3 관련)", BOX, typ="annex"),
        ch("WMA_RULE", "14의3", "다른 조문 제목", "제14조의3(다른 조문 제목)"),
        ch("WMA_RULE", "14의4", "재활용의 금지ㆍ제한", "제14조의4(재활용의 금지ㆍ제한) 법 제13조의2제2항에 따라 영 별표 1의 폐기물"),
        ch("WMA_RULE", "16의13", "위탁ㆍ수탁의 기준 및 절차", ART, counts={"항": 1, "호": {"1": 2}}),
        ch("WMA_RULE", "16의13/항1/호1", "위탁ㆍ수탁의 기준 및 절차", "제16조의13\n①\n1. 수탁자를 확인할 것"),
        ch("WMA_RULE", "16의13/항1/호2", "위탁ㆍ수탁의 기준 및 절차", "제16조의13\n①\n2. 서면으로 계약할 것"),
        ch("WMA_RULE", "BF5", "폐기물 위탁 계약서 서식", "위탁 계약서 서식 수탁 확인", typ="form"),
        ch("WMA_RULE", "B17", "폐기물처리 신고자의 시설", typ="annex"),
        ch("WMA_RULE", "B17의2", "폐기물처리 신고자의 시설 기준", typ="annex"),
        ch("GUIDE_DECL", "§Ⅵ", "지정폐기물 해당 여부 확인", "Ⅵ. 지정폐기물 해당 여부 확인", typ="guide"),
        ch("ALLBARO_NOTICE", "전체", "", "폐기물 전자정보처리 프로그램 고시 전문", typ="whole"),
    ]:
        s.chunks[(c.law, c.key)] = c
    lt.set_store(s)
    yield s
    lt.set_store(None)


# ── find_by_title ─────────────────────────────────────────────

def test_find_by_number_with_annex_title_suffix(store):
    r = lt.find_by_title("WMA_RULE", "B4의3", "폐기물의 종류별 재활용 유형")
    assert r == {"law": "WMA_RULE", "key": "B4의3", "title": "폐기물의 종류별 재활용 유형(제4조의3 관련)",
                 "found_by": "no", "moved": False, "title_match": True}


def test_find_moved_article_by_title(store):
    # 체크리스트는 14의3 이라고 적었지만 개정으로 14의4 로 옮겨진 경우 (가운뎃점 종류도 다름)
    r = lt.find_by_title("WMA_RULE", "14의3", "재활용의 금지·제한")
    assert r["key"] == "14의4" and r["moved"] and r["found_by"] == "title"


def test_find_number_missing_title_found(store):
    r = lt.find_by_title("WMA", "13의3", "폐기물의 재활용 원칙 및 준수사항")
    assert r["key"] == "13의2" and r["moved"]


def test_find_title_only_same_kind(store):
    # 별표를 찾을 때 같은 제목의 조문으로 가지 않는다
    r = lt.find_by_title("WMA_RULE", "B99", "위탁ㆍ수탁의 기준 및 절차")
    assert r is None


def test_find_ambiguous_title_keeps_number_with_flag(store):
    # "폐기물처리 신고자의 시설" 은 B17·B17의2 둘 다에 걸림 → 고르지 않고 번호 결과 + title_match=False
    r = lt.find_by_title("WMA_RULE", "B17", "폐기물처리 신고자")
    assert r["key"] == "B17" and r["title_match"] is True        # 번호 조문 제목에 포함되므로 그대로
    r = lt.find_by_title("WMA_RULE", "B17", "보관시설 및 재활용시설")
    assert r["key"] == "B17" and r["title_match"] is False and not r["moved"]


def test_find_guide_roman_and_whole_notice(store):
    assert lt.find_by_title("GUIDE_DECL", "Ⅵ", "지정폐기물 해당 여부 확인")["key"] == "§Ⅵ"
    r = lt.find_by_title("ALLBARO_NOTICE", "", "폐기물 전자정보처리 프로그램 운영 및 사용 등에 관한 고시")
    assert r["key"] == "전체" and r["found_by"] == "whole"


def test_find_nothing(store):
    assert lt.find_by_title("CEA_DESIG", "B1", "순환자원 지정 대상") is None


# ── full_article / excerpt / search ───────────────────────────

def test_full_article_complete_and_incomplete(store):
    a = lt.full_article("WMA_RULE", "16의13/항1")                # 하위 키를 줘도 조문 통째
    assert a["key"] == "16의13" and a["complete"], a["problems"]
    assert a["ref"] == "WMA_RULE:16의13@20261112#h0"
    del store.chunks[("WMA_RULE", "16의13/항1/호2")]
    a = lt.full_article("WMA_RULE", "16의13")
    assert not a["complete"] and any("호" in p for p in a["problems"])
    assert lt.full_article("WMA_RULE", "999") is None


def test_full_article_annex(store):
    a = lt.full_article("WMA_RULE", "B4의3")
    assert a["key"] == "B4의3" and "51-03-01" in a["text"]


def test_excerpt_rows_exact_rows_only(store):
    r = lt.excerpt_rows("WMA_RULE", "B4의3", ["51-03-01", "99-99-99", "51-03-01"])
    assert r["found"] == ["51-03-01"] and r["missing"] == ["99-99-99"]
    assert "51-03-01" in r["text"] and "R-3-2" in r["text"] and "51-04-01" not in r["text"]
    assert r["ref"] == "WMA_RULE:B4의3@20261112#h0"
    assert lt.excerpt_rows("WMA_RULE", "B0", ["x"]) is None


def test_search_excludes_forms_and_adds_top_key(store):
    hits = lt.search("위탁 수탁 계약", k=10)
    assert hits and all(not h["key"].startswith("BF") for h in hits)
    assert all(h["top_key"] == h["key"].split("/")[0] for h in hits)
    assert all(h["law"] == "WMA" for h in lt.search("재활용", laws=["WMA"]))


def test_chunk_ref(store):
    assert lt.chunk_ref("WMA", "25") == "WMA:25@20261112#h0"
    assert lt.chunk_ref("WMA", "999") is None


# ── citations ────────────────────────────────────────────────

@pytest.mark.parametrize("text,from_law,want", [
    ("법 제13조의2제2항에 따른", "WMA_RULE", [("WMA", "13의2")]),
    ("영 별표 1에 따른 지정폐기물", "WMA_RULE", [("WMA_DEC", "B1")]),
    ("시행령 제7조 및 시행규칙 별표 4의3", "WMA", [("WMA_DEC", "7"), ("WMA_RULE", "B4의3")]),
    ("별표 4의3에 따른 재활용 유형", "WMA_RULE", [("WMA_RULE", "B4의3")]),
    ("이 규칙 제3조", "WMA_RULE", [("WMA_RULE", "3")]),
    ("법 제13조 및 제14조", "WMA_RULE", [("WMA", "13"), ("WMA", "14")]),
    ("법 제13조에 따라 처리하고, 별도로 제20조", "WMA_RULE", [("WMA", "13"), ("WMA_RULE", "20")]),
    ("「자원의 절약과 재활용촉진에 관한 법률 시행령」 제18조제1호", "WMA_RULE", [("RRA_DEC", "18")]),
    ("「건설폐기물의 재활용촉진에 관한 법률」 제2조 및 같은 법 시행령 제4조", "WMA",
     [("CWA", "2"), ("CWA_DEC", "4")]),
    ("법 제9조에 따른 순환자원", "CEA_DESIG", [("CEA", "9")]),
    ("처리방법 제3조", "WMA_RULE", [("WMA_RULE", "3")]),            # '방법'의 '법'은 수식어가 아님
    ("부칙 제2조에 따라", "WMA_RULE", []),
    ("부칙 제2조 및 제3조", "WMA_RULE", []),
    ("「없는 법률」 제3조 및 제4조", "WMA", []),
    ("법 제 13 조의 2", "WMA_RULE", [("WMA", "13의2")]),
])
def test_citations(text, from_law, want):
    assert lt.citations(text, from_law) == want


def test_citations_self_and_dedup():
    t = "제66조(폐기물처리 신고대상)\n③ 법 제46조제1항제2호에서 … 법 제46조제2항"
    assert lt.citations(t, "WMA_RULE", self_key="66/항3") == [("WMA", "46")]


def test_citations_real_chunk_sample():
    rows = [json.loads(l) for l in SAMPLE_CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    got = lt.citations(rows[0]["text"], "WMA_RULE", self_key=rows[0]["spec"])
    assert ("WMA", "46") in got and ("RRA_DEC", "18") in got
    assert all(law in lt.LAW_NAME for law, _ in got)


def test_resolve_citations_only_existing(store):
    text = store.get("WMA_RULE", "14의4").text
    got = lt.resolve_citations(text, "WMA_RULE", self_key="14의4")
    assert [(g["law"], g["key"]) for g in got] == [("WMA", "13의2"), ("WMA_DEC", "B1")]
    assert got[0]["ref"] == "WMA:13의2@20261112#h0"


def test_law_family():
    assert lt.law_family("WMA_RULE") == "WMA"
    assert lt.law_family("CEA_DESIG") == "CEA"
    assert lt.law_family("GUIDE_DECL") == "WMA"
    assert lt.law_family("RRA") == "RRA"


def test_get_store_uses_env(monkeypatch, tmp_path):
    lt.set_store(None)
    d = tmp_path / "chunks" / "WMA"
    d.mkdir(parents=True)
    (d / "20261112.jsonl").write_text(json.dumps(
        {"law_id": "WMA", "key": "25", "type": "article", "title": "폐기물처리업", "text": "제25조(폐기물처리업)",
         "effective": "20261112", "hash": "ab"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("WASTE_LAW_DIR", str(tmp_path / "chunks"))
    s = lt.get_store()
    assert s.get("WMA", "25") is not None and lt.get_store() is s     # 한 번만 로드
    lt.set_store(None)



# ── 괘선 표 행 꺼내기 (row_blocks) ────────────────────────────

NO_SEP = """┌──────┬──────┐
│분류번호│폐기물의 종류│
│04-02-00│폐유기용제   │
│      │(할로겐족)   │
│04-02-01│할로겐족 유기용제│
│06-01-00│폐유        │
│      │기계유 포함  │
└──────┴──────┘"""


def test_row_blocks_without_row_separators():
    # 행마다 경계선(├)이 없는 표: 다음 코드 행에서 멈춰야 한다 (예전 함수는 표 끝까지 읽음)
    rows = lt.row_blocks(NO_SEP, "04-02-00")
    assert rows == ["04-02-00 | 폐유기용제\n| (할로겐족)".replace("\n| ", "\n")] or rows[0].startswith("04-02-00")
    assert "04-02-01" not in rows[0] and "06-01-00" not in rows[0] and "(할로겐족)" in rows[0]
    assert lt.row_blocks(NO_SEP, "06-01-00")[0].endswith("기계유 포함")


def test_row_blocks_word_boundary_and_limits():
    assert lt.row_blocks(NO_SEP, "04-02") == []                        # 04-02-00 안에서 걸리지 않음
    assert lt.row_blocks(NO_SEP, "04-02-0") == []
    long = "│01-01-01│시작│\n" + "\n".join("│      │줄 %d│" % i for i in range(100))
    assert lt.row_blocks(long, "01-01-01")[0].count("\n") == lt.ROW_MAX_LINES - 1
    many = "\n".join("│R-3-2│행 %d│\n├──┤" % i for i in range(10))
    assert len(lt.row_blocks(many, "R-3-2")) == lt.ROW_MAX_HITS
    assert "R-3-2 | 행 0" == lt.row_blocks(many, "R-3-2")[0]


def test_relevant_parts(store):
    store.chunks[("WMA_RULE", "B5")] = lt.LawStore().chunks.get(("x", "y")) or store.get("WMA_RULE", "B4의3")
    from law_agent.core.lawstore import Chunk
    for k, t in [("B5:1", "생활폐기물 음식물"), ("B5:2", "지정폐기물 폐유기용제 소각"), ("B5:3", "건설폐기물")]:
        store.chunks[("WMA_RULE", k)] = Chunk("WMA_RULE", k, "annex_part", "처리 기준", t, "20261112", "h0")
    got = lt.relevant_parts("WMA_RULE", "B5", ["폐유기용제", "지정폐기물"], budget=10000)
    assert [p.key for p in got] == ["B5:2"]
    assert lt.relevant_parts("WMA_RULE", "B5", ["폐기물"], budget=3) == []      # 예산보다 크면 넣지 않음
    assert lt.relevant_parts("WMA", "13의2", ["x"], budget=100) == []          # 조각 없음


# ── 근거 글 정리 ─────────────────────────────────────────────

def test_clean_law_text():
    raw = ("■ 폐기물관리법 시행규칙 [별표 5] <개정 2026. 3. 26.>\n"
           "┌──────┬──────┐\n"
           "│1. 공통사항│보관할 것 <개정 2013.5.31, 2018.5.17>│\n"
           "├──────┼──────┤\n"
           "제14조의3(재활용 기준) ① 다음과 같다. <신설 2010.7.1>\n"
           "② 삭제 <삭제 2015.7.1>\n"
           "[본조신설 2008.8.4]   [제목개정 2013.5.31]\n"
           "\n\n   3.   폐유는    분리한다.  \n")
    out = lt.clean_law_text(raw)
    assert out == ("1. 공통사항 | 보관할 것\n"
                   "제14조의3(재활용 기준) ① 다음과 같다.\n"
                   "② 삭제 <삭제 2015.7.1>\n"
                   "3. 폐유는 분리한다.")
    assert lt.clean_law_text(out) == out                       # 두 번 해도 같음
    assert lt.clean_law_text("") == ""


# ── 줄 목록 별표의 분류 묶음 (별표 4) ─────────────────────────

LIST_ANNEX = """2. 사업장일반폐기물의 세부분류 및 분류번호
51 사업장일반폐기물
51-01 공정오니
51-01-01 유기성오니
51-03 폐합성고분자화합물
51-03-01 폐합성수지류(폐염화비닐수지류는
제외한다)
51-03-02 폐합성고무류
51-03-07 플라스틱폐포장재
51-04 광재
51-04-01 철광재"""


def test_code_group_lines_keeps_siblings_and_headers():
    out = lt.code_group_lines(LIST_ANNEX, "51-03-01")
    assert out.split("\n") == ["51 사업장일반폐기물", "51-03 폐합성고분자화합물",
                               "51-03-01 폐합성수지류(폐염화비닐수지류는", "제외한다)",
                               "51-03-02 폐합성고무류", "51-03-07 플라스틱폐포장재"]
    assert "51-04" not in out and "51-01" not in out
    assert lt.code_group_lines(LIST_ANNEX, "99-01-01") == ""
    assert lt.code_group_lines(LIST_ANNEX, "R-3-2") == ""
