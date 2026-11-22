"""조문 통째 가져오기 + 빠짐 검증 테스트 (가짜 조문 폴더로, 실제 조문이 없어도 돈다).

실행: $PY -m pytest -v lawstore/test_articles.py
"""
import json
import os

import pytest

from lawstore import LawStore
from lawstore.articles import parse_article

HEAD = "제18조의2(지정폐기물 처리계획의 확인)"
HANG1 = "① 다음 각 호의 어느 하나에 해당하는 자를 말한다."
HOS = ["1. 오니를 월 평균 500킬로그램 이상 배출하는 사업자", "2. 폐농약 등을 배출하는 사업자",
       "3. 폐유 등을 배출하는 사업자", "3의2. 폐석면을 월 평균 20킬로그램 이상 배출하는 사업자",
       "4. 폴리클로리네이티드비페닐 함유폐기물을 배출하는 사업자", "5. 폐유독물질을 배출하는 사업자",
       "6. 의료폐기물을 배출하는 사업자", "가. 세목 줄", "7. 수은폐기물을 배출하는 사업자",
       "8. 천연방사성제품폐기물을 배출하는 사업자", "9. 고시하는 폐기물을 배출하는 사업자"]
HANG2 = "② 처리계획서는 별지 제12호서식에 따른다."
HO_NOS = ["1", "2", "3", "3의2", "4", "5", "6", "7", "8", "9"]


def _rows(drop=(), counts=True, cut_text=False):
    text = "\n".join([HEAD, HANG1] + (HOS[:6] if cut_text else HOS) + [HANG2])
    art = {"type": "article", "key": "18의2", "title": "지정폐기물 처리계획의 확인", "text": text,
           "hash": "h0", "effective": "20261112", "law_id": "WMA_RULE"}
    if counts:
        art["counts"] = {"항": 2, "호": {"1": 10}}
    rows = [art]
    body = [h for h in HOS if not h.startswith("가.")]
    for no, line in zip(HO_NOS, body):
        if no in drop:
            continue
        rows.append({"type": "article_part", "key": f"18의2/항1/호{no}", "title": "", "parent": "18의2",
                     "text": f"{HEAD}\n{HANG1}\n{line}", "hash": "x", "effective": "20261112", "law_id": "WMA_RULE"})
    rows.append({"type": "article_part", "key": "18의2/항1", "parent": "18의2", "text": HANG1, "hash": "y",
                 "effective": "20261112", "law_id": "WMA_RULE"})
    return rows


def _store(tmp_path, rows):
    d = tmp_path / "chunks" / "WMA_RULE"
    d.mkdir(parents=True)
    (d / "20261112.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return LawStore.load(tmp_path / "chunks")


def test_parse_article_keeps_branch_numbers():
    hangs = parse_article("\n".join([HEAD, HANG1] + HOS + [HANG2]))
    assert [h["hang"] for h in hangs] == ["1", "2"]
    assert [x["no"] for x in hangs[0]["hos"]] == HO_NOS
    assert "가. 세목 줄" in hangs[0]["hos"][6]["text"]          # 목은 호 글에 붙는다


def test_complete(tmp_path):
    a = _store(tmp_path, _rows()).article("WMA_RULE", "18의2")
    assert a["complete"], a["problems"]
    assert a["basis"].startswith("counts")
    assert [x["no"] for x in a["hangs"][0]["hos"]] == HO_NOS
    assert a["hangs"][0]["hos"][4]["key"] == "18의2/항1/호4"
    assert a["ref"] == "WMA_RULE:18의2@20261112#h0"


def test_missing_parts_named(tmp_path):
    a = _store(tmp_path, _rows(drop=("5", "6", "7", "8", "9"))).article("WMA_RULE", "18의2/항1/호3")
    assert not a["complete"]
    assert "제1항: 호 조각 5개, 원문 기준 10개" in a["problems"]
    assert "제1항: 조각에 없는 호 ['5', '6', '7', '8', '9']" in a["problems"]


def test_truncated_text_detected(tmp_path):
    a = _store(tmp_path, _rows(cut_text=True)).article("WMA_RULE", "18의2")
    assert not a["complete"]
    assert any("본문 잘림 의심" in p for p in a["problems"])


def test_old_chunks_without_counts(tmp_path):
    a = _store(tmp_path, _rows(counts=False)).article("WMA_RULE", "18의2")
    assert a["complete"] and a["basis"].startswith("본문 번호")
    b = _store(tmp_path / "b", _rows(counts=False, drop=("3의2",))).article("WMA_RULE", "18의2")
    assert "제1항: 조각에 없는 호 ['3의2']" in b["problems"]


def test_find_articles_returns_whole_article(tmp_path):
    hits = _store(tmp_path, _rows()).find_articles("폐석면 월 평균 배출", k=1)
    assert hits and hits[0]["key"] == "18의2" and hits[0]["complete"]
    assert len(hits[0]["hangs"][0]["hos"]) == 10                 # 검색에 걸린 호 하나가 아니라 조문 전체


@pytest.fixture(scope="module")
def real():
    d = os.environ.get("WASTE_LAW_DIR")
    if not d or not os.path.isdir(d):
        pytest.skip("WASTE_LAW_DIR (data/law/chunks) 없음")
    return LawStore.load(d)


def test_real_18_2(real):
    a = real.article("WMA_RULE", "18의2")
    assert a["complete"], a["problems"]
    assert len(a["hangs"][0]["hos"]) == 10
