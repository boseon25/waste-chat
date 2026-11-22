"""비슷하게 쓴 값을 틀렸다고 하지 않기 (2026.11.20) — review/rules.py _num · _date · _enum"""
from law_agent.review.rules import _date, _enum, _num


def test_enum_accepts_sentences_but_not_negation():
    a = ["가능", "불가능"]
    assert _enum("가능합니다", a) == "가능" and _enum("불가능해요", a) == "불가능"
    assert _enum("불가능", ["가능"]) is None             # '불가능' 안의 '가능'은 맞은 게 아님
    assert _enum(" 가능. ", ["가능"]) == "가능" and _enum("예", a) is None


def test_number_with_units():
    assert _num("1톤") == 1.0 and _num("월 1.2 t") == 1.2 and _num("1,200") == 1200.0
    assert _num("1~2톤") is None and _num("모름") is None


def test_date_formats():
    for s in ("2026-12-01", "2026.12.1.", "2026년 12월 1일", "2026/12/01", "20261201"):
        assert str(_date(s)) == "2026-12-01", s
    assert _date("12월") is None and _date("2026-13-01") is None
