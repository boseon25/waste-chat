"""tools/bm25.py 테스트."""
from __future__ import annotations

from fake_law import build_store
from law_agent.tools import law_tools
from law_agent.tools.bm25 import BM25Index, bigrams, get_index, rrf


def test_bigrams_same_as_lawstore():
    from law_agent.core.lawstore import _bigrams
    for t in ["폐합성수지류 R-3-2", "제13조의2(재활용)", "51-03-01"]:
        assert bigrams(t) == _bigrams(t)


def test_index_excludes_whole_forms_and_annex_body_with_parts():
    idx = BM25Index.build(build_store())
    keys = set(idx.docs)
    assert ("ALLBARO_NOTICE", "전체") not in keys and ("WMA_RULE", "BF5") not in keys
    assert ("WMA_RULE", "B4의3") not in keys and ("WMA_RULE", "B4의3:2") in keys      # 본체 대신 행
    assert ("WMA_RULE", "B4의2") in keys                                               # 행이 없는 별표는 본체


def test_search_finds_row_and_orders_deterministically():
    idx = BM25Index.build(build_store())
    hits = idx.search("04-02-00 폐유기용제", k=5)
    assert (hits[0]["law"], hits[0]["key"], hits[0]["top_key"]) == ("WMA_RULE", "B4의3:2", "B4의3")
    assert hits == idx.search("04-02-00 폐유기용제", k=5)
    assert all(h["score"] > 0 for h in hits)
    assert idx.search("ㅋㅋㅋ") == []
    assert all(h["law"] == "WMA" for h in idx.search("폐기물", laws=["WMA"]))


def test_length_normalization_prefers_short_focused_chunk():
    idx = BM25Index.build(build_store())
    top = idx.search("기계유", k=3)
    assert top[0]["key"] in ("B4의3:3", "40")                  # 기계유가 들어간 짧은 조각


def test_get_index_cached_per_store():
    s = build_store()
    assert get_index(s) is get_index(s)
    assert get_index(build_store()) is not None


def test_rrf():
    a, b, c = ("L", "a"), ("L", "b"), ("L", "c")
    out = rrf([[a, b], [a, c]])
    assert out[0] == (a, 1.0)                                  # 두 목록 모두 1위 = 1.0
    assert [d for d, _ in out] == [a, b, c]                    # b·c 같은 점수 → (law, key) 순
    assert rrf([]) == []


def test_search_chunks_and_long_annex():
    s = build_store()
    assert law_tools.is_long_annex("WMA_RULE", "B4의3:1", s) and law_tools.is_long_annex("WMA_RULE", "B4의3", s)
    assert not law_tools.is_long_annex("WMA_RULE", "B4의2", s) and not law_tools.is_long_annex("WMA", "13의2", s)
    assert not law_tools.is_long_annex("WMA_RULE", "BF5", s)
    assert law_tools.search_chunks("04-02-00 폐유기용제", store=s)[0]["key"] == "B4의3:2"
