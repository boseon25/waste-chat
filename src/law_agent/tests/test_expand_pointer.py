"""'다음 각 호/목' 머리말 인용 펼치기 (2026.11.20 시행령 제7조 제8호)."""
from law_agent.tools.law_tools import expand_pointer as E

ART = ("제7조(폐기물의 처리기준 등) ①처리 기준은 다음 각 호와 같다. 1. 구분하여 수집할 것. 다만, 다음 각 목의 경우에는 "
       "그러하지 아니하다. 가. 같은 시설에서 처리하는 경우 나. 혼합되어 발생된 경우 8. 두 종류 이상의 폐기물이 혼합되어 있어 "
       "분리가 어려우면 다음 각 목의 방법으로 처리할 것 가. 폐산이나 폐알칼리와 다른 폐기물이 혼합된 경우에는 중화처리할 것 "
       "나. 일반소각대상과 고온소각대상이 혼합된 경우에는 고온소각할 것 9. 재활용이 가능한 폐기물은 재활용할 것 ② 다음 항.")
Q8 = "8. 두 종류 이상의 폐기물이 혼합되어 있어 분리가 어려우면 다음 각 목의 방법으로 처리할 것"


def test_mok_until_next_ho():
    r = E(Q8, ART)
    assert "중화처리할 것" in r and "고온소각할 것" in r and "9. 재활용" not in r


def test_ho_until_next_hang():
    r = E("①처리 기준은 다음 각 호와 같다.", ART)
    assert "8. 두 종류" in r and "9. 재활용" in r and "② 다음 항" not in r


def test_not_pointer_or_not_found():
    assert E("9. 재활용이 가능한 폐기물은 재활용할 것", ART) is None
    assert E("다음 각 목의 방법으로 처리할 것", "전혀 다른 조문") is None


def test_long_keeps_heads():
    long = Q8 + " " + " ".join(f"{c}. 이런 경우에는 이렇게 처리할 것 " + "길게 " * 300 for c in "가나다")
    r = E(Q8, long, max_chars=500)
    assert r.endswith("…") and "가. 이런 경우" in r and len(r) < 800
