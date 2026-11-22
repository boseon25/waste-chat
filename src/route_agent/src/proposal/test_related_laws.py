"""관련 조문 붙이기 테스트. 가짜 조문 폴더로 돈다.

실행: $PY -m pytest -v proposal/test_related_laws.py
"""
import json

import pytest

from lawstore import LawStore
from proposal.related_laws import applies, locate, related_laws


def _art(law, key, title, text, typ="article"):
    return {"type": typ, "key": key, "title": title, "text": text, "hash": "h", "effective": "20261112", "law_id": law}


@pytest.fixture
def store(tmp_path):
    rows = {
        "WMA_RULE": [
            _art("WMA_RULE", "16의12", "음식물류 폐기물의 공동처리 등", "제16조의12(음식물류 폐기물의 공동처리 등)\n① 공동처리"),
            _art("WMA_RULE", "16의13", "위탁ㆍ수탁의 기준 및 절차",
                 "제16조의13(위탁ㆍ수탁의 기준 및 절차) 다음 각 호를 준수해야 한다.\n1. 수탁처리능력 확인서\n2. 적법 수탁자 확인"),
            _art("WMA_RULE", "16의13/호1", "", "1. 수탁처리능력 확인서", "article_part"),
            _art("WMA_RULE", "16의13/호2", "", "2. 적법 수탁자 확인", "article_part"),
            _art("WMA_RULE", "18의2", "지정폐기물 처리계획의 확인", "제18조의2(지정폐기물 처리계획의 확인)\n① 처리계획"),
            _art("WMA_RULE", "9", "폐기물 수집ㆍ운반업자 등의 운반기준", "제9조(운반기준)\n① 운반"),
            _art("WMA_RULE", "5의3", "폐기물의 재활용 기준(제14조의3제1항 관련)", "가" * 7000, "annex"),
            _art("WMA_RULE", "B5의3:1.", "", "1. 재사용", "annex_part"),
        ],
    }
    for law, rs in rows.items():
        d = tmp_path / law
        d.mkdir()
        (d / "20261112.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rs), encoding="utf-8")
    return LawStore.load(tmp_path)


ROUTE = {"gate": 2, "tier": "material_recycle", "waste_status": "general", "waste_codes": ["51-03-01"],
         "title": "재생이용", "steps": [{"role": "recycler"}, {"role": "transporter"}], "partners": {"steps": []}}


def test_locate_follows_title_when_number_moved(store):
    key, note = locate(store, "WMA_RULE", "16의12", "위탁ㆍ수탁의 기준 및 절차")
    assert key == "16의13" and "번호 이동: 16의12 → 16의13" in note
    assert locate(store, "WMA_RULE", "16의13", "위탁ㆍ수탁의 기준 및 절차") == ("16의13", None)


def test_applies():
    assert applies("transport", ROUTE) and applies("recycle", ROUTE)
    assert not applies("designated", ROUTE) and not applies("energy", ROUTE)
    assert not applies("waste", {**ROUTE, "gate": 1})                              # 순환자원은 폐기물 아님
    assert applies("designated", {**ROUTE, "waste_codes": ["06-01-00"]})
    assert not applies("handover", ROUTE, exempt_codes={"51-03-01"})               # 폐지·고철 전자인계 제외


def test_related_laws_rules(store):
    out = related_laws(ROUTE, store)
    by = {(e["law"], e["key"]): e for e in out if e["source"] == "rule"}
    ent = by[("WMA_RULE", "16의13")]
    assert ent["status"] == "needs_review" and ent["complete"] and ent["text"].startswith("제16조의13")
    assert ("WMA_RULE", "9") in by                                                # 운반 단계 있음
    ann = by[("WMA_RULE", "B5의3")]
    assert ann["text"] is None and "원문 생략" in ann["text_basis"] and ann["parts"] == ["B5의3:1"]
    assert ("WMA_RULE", "18의2") not in by                                       # 일반폐기물
    missing = [e for e in out if e["text_basis"] == "조문 없음"]
    assert all(not e["complete"] for e in missing)                                 # 없는 조문은 빠짐으로 표시


def test_no_store_and_gate1():
    assert related_laws(ROUTE, None) == []


def test_gate1_no_rules(store):
    assert related_laws({**ROUTE, "gate": 1, "steps": [{"role": "end_user"}]}, store) == []
