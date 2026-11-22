"""법령 저장소 테스트. 실제 조문 폴더(WASTE_LAW_DIR = data/law/chunks)가 있을 때만 실행.

실행: WASTE_LAW_DIR=<data/law/chunks> $PY -m pytest -v lawstore/test_lawstore.py
"""
import os

import pytest

from lawstore import LawStore, box_table_rows, norm_key


def test_norm_key():
    assert norm_key({"type": "annex", "key": "4의3"}) == "B4의3"
    assert norm_key({"type": "annex_part", "key": "B5:3."}) == "B5:3"
    assert norm_key({"type": "annex_part", "key": "B5:3._2"}) == "B5:3~2"
    assert norm_key({"type": "article_part", "key": "17/항1/호3"}) == "17/항1/호3"


def test_box_table_rows():
    text = "\n".join([
        "  │51-03     │폐합성고분자화합물        │                │          │",
        "  ├─────┼──────────────┬───────────┬───────┤",
        "  │51-03-01  │폐합성수지류(폐염화비닐수   │R-1, R-2, R-3-2,      │해당 없음     │",
        "  │          │지류는 제외한다)            │R-3-3                 │              │",
        "  │          │                            ├───────────┼───────┤",
        "  │          │                            │R-8-1, R-10           │해당          │",
        "  ├─────┼──────────────┼───────────┼───────┤",
        "  │51-03-02  │폐합성고무류                │R-1-1                 │해당 없음     │"])
    rows = box_table_rows(text, "51-03-01")
    assert rows == ["51-03-01 | 폐합성수지류(폐염화비닐수지류는 제외한다) | R-1, R-2, R-3-2, R-3-3 | 해당 없음\n"
                    "R-8-1, R-10 | 해당"]


@pytest.fixture(scope="module")
def store():
    d = os.environ.get("WASTE_LAW_DIR")
    if not d or not os.path.isdir(d):
        pytest.skip("WASTE_LAW_DIR (data/law/chunks) 없음")
    return LawStore.load(d)


def test_real_annex_and_article_keys_do_not_collide(store):
    assert store.get("WMA_RULE", "B5").type == "annex"
    assert store.get("WMA_RULE", "5").type == "article"


def test_real_excerpt_row(store):
    eff = store.get("WMA_RULE", "B4의3").effective                 # 저장된 최신 시행일 (개정되면 바뀜)
    e = store.excerpt(f"WMA_RULE:B4의3@{eff}", codes=["51-03-01"])
    assert e["text_basis"] == "분류번호 행 발췌" and e["text"].startswith("51-03-01 | 폐합성수지류")
    assert "R-3-3" in e["text"] and e["same_effective"] is True


def test_real_excerpt_article_part(store):
    e = store.excerpt(f'WMA:17/항1/호3@{store.get("WMA", "17").effective}')
    assert e["name"] == "폐기물관리법 제17조 제1항 제3호" and "위탁" in e["text"]


def test_real_search(store):
    hits = store.search("지정폐기물 처리계획 확인 대상", k=5, forms=False)
    assert any("처리계획" in h["title"] for h in hits)
    assert not any(h["key"].startswith("BF") for h in hits)


def test_real_find_by_name(store):
    eff = store.get("WMA", "25").effective
    assert store.find_by_name("폐기물관리법", "제25조") == f"WMA:25@{eff}"
    assert store.find_by_name("폐기물관리법", "제13조의2") == f"WMA:13의2@{eff}"
    assert store.find_by_name("폐기물관리법", "제17조 제1항 제3호") == f"WMA:17/항1/호3@{eff}"
    assert store.find_by_name("없는법", "제1조") is None
