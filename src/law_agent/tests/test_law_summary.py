"""근거 원문 쉬운 요약 — 화면용 (2026.11.20)."""
from law_agent.output import law_summary as L
from law_agent.output.cards_html import _basis

SRC = "가. 재활용 과정에서 분말상으로 유출될 우려가 있는 경우에는 용기에 넣어야 한다. 다만, 30일 이내 처리하는 경우는 제외한다."


def test_make_and_number_check():
    ok = L.make(SRC, "별표 5의3", call=lambda s, u: {"summary": "가루가 날리면 용기에 담아야 합니다. 30일 안에 처리하면 예외입니다."})
    assert ok["ok"] and ok["bad_numbers"] == []
    bad = L.make(SRC, "별표 5의3", call=lambda s, u: {"summary": "60일 안에 처리하면 예외입니다."})
    assert not bad["ok"] and bad["bad_numbers"] == ["60"]
    assert L.make(SRC, "", call=lambda s, u: None) is None


def test_lookup_only_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("LAW_AGENT_DATA_DIR", str(tmp_path))
    L.reset()
    t = {L.key_of(SRC): L.make(SRC, "x", call=lambda s, u: {"summary": "용기에 담아야 합니다."}),
         L.key_of("다른 원문 7일"): L.make("다른 원문 7일", "y", call=lambda s, u: {"summary": "9일"})}
    L.save(t)
    L.reset()
    assert L.lookup("  " + SRC.replace(" ", "  ") + " ")["summary"] == "용기에 담아야 합니다."   # 공백만 달라도 같은 열쇠
    assert L.lookup("다른 원문 7일") is None                     # 숫자 검사 실패 → 화면에 안 씀
    assert L.lookup("없는 원문") is None
    L.main(["approve", "all"])
    L.reset()
    assert L.lookup(SRC)["reviewed"] is True
    L.reset()


def test_html_summary_first_and_draft_tag():
    b = [{"where": "시행규칙 별표 5의3", "quote": SRC, "source": SRC, "plain": {"summary": "용기에 담아야 합니다.", "reviewed": False}}]
    h = _basis(b)
    assert h.index("용기에 담아야") < h.index("원문 보기") < h.index("유출될 우려")
    assert "검수 전" in h
    b[0]["plain"]["reviewed"] = True
    assert "검수 전" not in _basis(b)
    assert "plain" not in _basis([{"where": "w", "quote": SRC}])      # 요약 없으면 예전 그대로


def test_target_prefers_quote_and_date_not_in_prompt():
    q = "R-10-1 유형의 재활용 기준: 별표 4의3에 따른 폐기물의 종류별 재활용 가능 유형에 적합한 폐기물을 사용하여야 한다. " * 2
    assert L.target({"quote": q, "source": "앞 항목 연료 기준 … " + q}) == L.norm(q)    # 원문 창이 아니라 인용
    assert L.target({"source": "원문만 있음"}) == ""
    seen = {}
    L.make("법 제2조에 따른 것은 별표 1과 같다.", "시행령 제3조 (2026.11.12 시행)",
           call=lambda s, u: seen.setdefault("u", u) and {"summary": "별표 1을 보세요."})
    assert "2026" not in seen["u"]


def test_capacity_todo_basis_is_16_13():
    from law_agent.output.plain import TODO
    assert TODO["capacity"]["ref"] == "WMA_RULE:16의13/호1"


def test_target_rules():
    long_ok = "가. " + "재활용 과정에서 오염을 막는 시설을 설치하고 운영하여야 하며 기록을 보존 " * 3 + "하여야 한다."
    assert L.target({"quote": "04-02-00 그 밖의 폐유기용제"}) == ""                     # 짧으면 요약 안 함
    assert L.target({"quote": long_ok}) == L.norm(long_ok)                              # 끝까지 있는 문장
    cut = long_ok[:-8]                                                                  # 중간에 잘림
    assert L.target({"quote": cut, "ref": "R"}, full=lambda r: "") == ""
    assert L.target({"quote": cut, "ref": "R"}, full=lambda r: long_ok) == L.norm(long_ok)
    assert L.target({"quote": cut, "ref": "R"}, full=lambda r: "가" * 3000) == ""        # 전체가 너무 길면 안 함
    assert L.target({"quote": cut, "whole": True}) == L.norm(cut)                         # 할 일 근거: 호 전체
    assert L.target({"quote": long_ok + "…", "whole": True}) == ""


def test_number_in_law_name_allowed():
    assert L.check("별표 4의 04-02-00 코드입니다", "04-02-00 그 밖의 폐유기용제", "시행규칙 별표 4 (2026.11.12 시행)") == []
    assert L.check("2026년 기준", "원문", "시행규칙 별표 4 (2026.11.12 시행)") == ["2026"]
