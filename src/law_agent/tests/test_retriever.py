"""agents/retriever.py 테스트 — 실제 lawstore + 가짜 조문."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_agent.agents import retriever as rt
from law_agent.agents.retriever import RetrieverConfig, build_query, node, retrieve
from law_agent.state import AgentState, MinLaw, Question, Verdict, initial_state
from law_agent.tools import law_tools
from fake_law import build_store

CFG = RetrieverConfig(max_articles=12, citation_depth=2, min_score=0.01)
CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]


def q(qid="D-1", mins=(("WMA_RULE", "B4의3", "폐기물의 종류별 재활용 유형"),), text="허용된 재활용 유형", **kw):
    return Question(id=qid, stage=qid[0], text=text, answer_type="list",
                    min_laws=[MinLaw(*m) for m in mins], **kw)


@pytest.fixture
def store():
    return build_store()


def run(store, cfg=CFG):
    return rt._Run(store, cfg)


def test_min_laws_first_in_order_and_moved(store):
    qq = q("B-1", mins=(("WMA", "13의2", "폐기물의 재활용 원칙 및 준수사항"),
                        ("WMA_RULE", "14의3", "재활용의 금지·제한"),          # 14의4 로 옮겨짐
                        ("WMA_DEC", "B1", "지정폐기물의 종류")), text="재활용 금지")
    cands, summ = retrieve(qq, CASES[0], {}, run(store))
    assert [(c.law, c.key, c.source) for c in cands[:3]] == [
        ("WMA", "13의2", "min_laws"), ("WMA_RULE", "14의4", "min_laws"), ("WMA_DEC", "B1", "min_laws")]
    assert cands[1].moved and summ["moved"] == ["WMA_RULE:14의4"]
    assert all(c.score == 1.0 for c in cands[:3])


def test_missing_min_law_reported(store):
    qq = q("C-1", mins=(("CEA_DESIG", "B1", "순환자원 지정 대상"),), text="순환자원")
    st = AgentState(questions=[qq], verdicts={}, case=CASES[0], facts={})
    out = node(st, store=store, cfg=CFG)
    assert out["audit"][0]["output"]["C-1"]["missing_min_laws"] == ["CEA_DESIG B1 순환자원 지정 대상"]
    assert out["errors"][0]["question_id"] == "C-1" and "최소 조문" in out["errors"][0]["message"]


def test_title_mismatch_not_forced_title_used_as_query(store):
    """B안: 번호는 있는데 제목이 다르면 강제하지 않고, 그 제목을 검색어로만 쓴다."""
    qq = q("I-2", mins=(("WMA_RULE", "B4", "보관시설 및 재활용시설"),))
    cands, summ = retrieve(qq, CASES[0], {}, run(store))
    assert summ["title_mismatch"] and summ["min_laws"] == 0
    assert not any(c.source == "min_laws" for c in cands)
    assert "보관시설 및 재활용시설" in summ["query"]


def test_missing_min_law_title_used_as_query(store):
    # 번호로도, 제목으로도(정확히) 못 찾음 → 제목을 검색어로 → 비슷한 조문(40 폐유의 보관 기준)이 검색으로 들어옴
    qq = q("B-1", mins=(("WMA_RULE", "77", "폐유 보관에 관한 기준"),), text="zzzz")
    cands, summ = retrieve(qq, {}, {}, run(store), wide=True)
    assert summ["missing_min_laws"] and "폐유 보관에 관한 기준" in summ["query"]
    assert ("WMA_RULE", "40") in {(c.law, c.key) for c in cands if c.source == "search"}   # 제목 검색으로 찾음


def test_citations_two_levels(store):
    # 14의4 → (WMA 13의2, WMA_DEC B1) → 13의2 → WMA_RULE B4의3
    qq = q("B-1", mins=(("WMA_RULE", "14의4", "재활용의 금지ㆍ제한"),), text="zzzz")   # 검색어가 아무것도 안 걸리게
    cfg = RetrieverConfig(max_articles=12, citation_depth=2, min_score=0.99)
    cands, _ = retrieve(qq, {}, {}, run(store, cfg), wide=True)
    got = {(c.law, c.key): c for c in cands}
    assert got[("WMA", "13의2")].source == "citation" and got[("WMA", "13의2")].via == "WMA_RULE:14의4"
    assert got[("WMA", "13의2")].score == 0.5
    assert got[("WMA_RULE", "B4의3")].via == "WMA:13의2" and got[("WMA_RULE", "B4의3")].score == 0.25
    cfg1 = RetrieverConfig(max_articles=12, citation_depth=1, min_score=0.99)
    keys1 = {(c.law, c.key) for c in retrieve(qq, {}, {}, run(store, cfg1), wide=True)[0]}
    assert ("WMA", "13의2") in keys1 and ("WMA_RULE", "B4의3") not in keys1


def test_search_excludes_forms_whole_and_low_score(store):
    qq = q("G-1", mins=(), text="위탁 수탁 계약 폐유 고시")
    cands, _ = retrieve(qq, {}, {}, run(store))
    keys = {c.key for c in cands}
    assert "BF5" not in keys and "전체" not in keys
    cands_hi, _ = retrieve(qq, {}, {}, run(store, RetrieverConfig(12, 0, 1.01)))
    assert cands_hi == []


def test_max_articles_keeps_min_laws(store):
    mins = (("WMA", "13의2", "폐기물의 재활용 원칙 및 준수사항"), ("WMA_DEC", "B1", "지정폐기물의 종류"),
            ("WMA_RULE", "B4의3", "폐기물의 종류별 재활용 유형"))
    cands, summ = retrieve(q("B-1", mins=mins, text="폐기물 재활용 폐유"), {}, {},
                           run(store, RetrieverConfig(max_articles=2, citation_depth=2, min_score=0.01)))
    assert [c.source for c in cands] == ["min_laws"] * 3 and summ["dropped"] > 0


def test_rest_sorted_by_score_then_key(store):
    cands, _ = retrieve(q(text="폐기물 재활용 폐유 기계유"), CASES[1], {}, run(store))
    rest = cands[1:]
    assert rest == sorted(rest, key=lambda c: (-c.score, c.law, c.key))
    assert len({(c.law, c.key) for c in cands}) == len(cands)


def test_sub_question_query_uses_component(store):
    case = CASES[1]
    parent = q("D-1")
    sub = q("D-1.c2", parent_id="D-1", component=2)
    assert "혼입된 기계유" in build_query(sub, case, {}) and "혼입된 기계유" not in build_query(parent, case, {})
    # 성분 이름으로만 걸리는 조문 (폐유의 보관 기준: '기계유')
    sub_keys = {c.key for c in retrieve(sub, case, {}, run(store))[0]}
    assert "40" in sub_keys


def test_parent_and_sub_share_min_law_lookup(store, monkeypatch):
    calls = []
    real = law_tools.find_by_title
    monkeypatch.setattr(law_tools, "find_by_title", lambda *a, **k: calls.append(a) or real(*a, **k))
    case = CASES[1]
    qs = [q("D-1", when=None), q("D-1.c1", parent_id="D-1", component=1), q("D-1.c2", parent_id="D-1", component=2)]
    st = initial_state(case)
    st["questions"] = qs
    out = node(st, store=store, cfg=CFG)
    assert set(out["candidates"]) == {"D-1", "D-1.c1", "D-1.c2"}
    assert len(calls) == 1                                                # 같은 최소 조문은 한 번만


def test_retry_hint_added_to_query(store):
    qq = q("D-1", when=None)
    st = AgentState(questions=[qq], case=CASES[0], facts={}, retry_count={"D-1": 1},
                    verdicts={"D-1": Verdict("D-1", "retry", retry_hint=["R-9-1", "에너지 회수"])})
    out = node(st, store=store, cfg=CFG)
    assert out["audit"][0]["output"]["D-1"]["query"].endswith("R-9-1 에너지 회수")


def test_node_only_ready_questions(store):
    qs = [q("B-1", mins=(), text="재활용 금지"), q("D-1", when="B-1 != yes")]
    st = AgentState(questions=qs, verdicts={}, case=CASES[0], facts={})
    out = node(st, store=store, cfg=CFG)
    assert set(out["candidates"]) == {"B-1"}


def test_deterministic(store):
    qq = q("D-1", text="폐기물 재활용 유형 폐유")
    a = retrieve(qq, CASES[1], {}, run(store))
    b = retrieve(qq, CASES[1], {}, run(store))
    assert a == b


def test_build_query_with_facts_and_no_tables(monkeypatch):
    monkeypatch.delenv("WASTE_DATA_DIR", raising=False)                  # 분류번호 이름 조회가 실패해도 됨
    facts = {"values": {"waste_code": "51-03-01"}}
    s = build_query(q(), CASES[0], facts)
    assert s.startswith("허용된 재활용 유형") and "51-03-01" in s and "PE 필름" in s


def test_config_from_settings():
    c = RetrieverConfig.from_settings()
    assert c.max_articles == 12 and c.citation_depth == 2 and c.min_score == 0.15


# ── BM25·검색어 나누기·인용 우선순위 ─────────────────────────

def test_keywords_strip_question_noise():
    assert rt.keywords("이 폐기물은 지정폐기물인가? (예 / 아니오 / 분석해야 앎)") == "지정폐기물"


def test_build_queries_split_and_dedup():
    sub = q("D-1.c2", parent_id="D-1", component=2, text="허용된 재활용 유형은 무엇인가?")
    qs = rt.build_queries(sub, CASES[1], {"values": {"waste_code": "04-02-00"}}, hint=["R-9-1"])
    assert qs[0] == "허용된 재활용 유형은"
    assert qs[1].startswith("04-02-00") and qs[2] == "기계 세척 후 남은 세척액"
    assert qs[3].startswith("혼입된 기계유") and qs[4] == "R-9-1"
    assert len(qs) == len(set(qs))


def test_long_annex_hit_keeps_row_key(store):
    qq = q("D-1", mins=(), text="재활용 유형")
    cands, _ = retrieve(qq, CASES[1], {"values": {"waste_code": "04-02-00"}}, run(store))
    search_keys = [c.key for c in cands if c.source == "search"]
    assert "B4의3:2" in search_keys and "B4의3" not in search_keys  # 검색은 행 키 그대로 (별표 통째로 올리지 않음)
    # 별표 본체는 인용(법 13의2 → 시행규칙 별표 4의3)으로만 들어올 수 있고, evidence 가 행만 잘라 넣는다
    assert all(c.source == "citation" for c in cands if c.key == "B4의3")


def test_short_article_hit_rolls_up_to_article(store):
    qq = q("G-1", mins=(), text="수탁자를 확인")
    keys = [c.key for c in retrieve(qq, {}, {}, run(store))[0]]
    assert "16의13" in keys and not any(k.startswith("16의13/") for k in keys)


def test_search_citations_only_one_level(store):
    # 검색 → 50 (세척액) → WMA 25 (1단계, 들어옴) → WMA 25의2 (2단계, 안 들어옴)
    qq = q("I-1", mins=(), text="세척액 처리 특례")
    got = {(c.law, c.key): c for c in retrieve(qq, {}, {}, run(store), wide=True)[0]}
    assert ("WMA_RULE", "50") in got and got[("WMA", "25")].via == "WMA_RULE:50"
    assert ("WMA", "25의2") not in got
    # 같은 조문이 최소 조문이면 2단계까지 따라감
    qq2 = q("I-1", mins=(("WMA_RULE", "50", "세척액 처리 특례"),), text="zzzz")
    got2 = {(c.law, c.key) for c in retrieve(qq2, {}, {}, run(store), wide=True)[0]}
    assert ("WMA", "25의2") in got2



# ── 단계 (좁게 → 넓게) ───────────────────────────────────────

def test_narrow_stage_limits_search_and_citations(store):
    qq = q("B-1", mins=(("WMA_RULE", "14의4", "재활용의 금지ㆍ제한"),), text="폐기물 재활용 폐유 기계유 세척액")
    narrow, sn = retrieve(qq, CASES[1], {}, run(store))                    # retries 0 → 좁게
    wide, sw = retrieve(qq, CASES[1], {}, run(store), retries=1)            # 재검색 → 넓게
    assert sn["stage"] == "narrow" and sw["stage"] == "wide"
    assert sum(c.source == "search" for c in narrow) <= CFG.narrow_search_top
    assert sum(c.source == "search" for c in wide) > CFG.narrow_search_top
    keys_n = {(c.law, c.key) for c in narrow}
    assert ("WMA", "13의2") in keys_n                                       # 최소 조문 인용 1단계는 들어옴
    assert all(c.via is None or c.via.startswith("WMA_RULE:14의4") for c in narrow if c.source == "citation")
    assert len(narrow) < len(wide)


def test_j_questions_always_wide(store):
    qq = q("J-1", mins=(), text="전기전자제품 재활용", search=True)
    _, s = retrieve(qq, {}, {}, run(store))
    assert s["stage"] == "wide"


def test_node_uses_retry_count_for_stage(store):
    qq = q("D-1", when=None)
    st = AgentState(questions=[qq], case=CASES[0], facts={}, retry_count={"D-1": 1},
                    verdicts={"D-1": Verdict("D-1", "retry", retry_hint=["R-9-1"])})
    assert node(st, store=store, cfg=CFG)["audit"][0]["output"]["D-1"]["stage"] == "wide"
    st2 = AgentState(questions=[qq], case=CASES[0], facts={}, verdicts={})
    assert node(st2, store=store, cfg=CFG)["audit"][0]["output"]["D-1"]["stage"] == "narrow"



def test_moved_by_title_is_still_forced(store):
    qq = q("B-1", mins=(("WMA_RULE", "77", "폐유의 보관 기준"),), text="zzzz")
    cands, summ = retrieve(qq, {}, {}, run(store))
    assert (cands[0].key, cands[0].source, cands[0].moved) == ("40", "min_laws", True)


def test_node_searches_with_a1_confirmed_code(store):
    qq = q("B-1", mins=(), text="재활용 금지")
    st = initial_state(CASES[0])
    st["questions"] = [qq]
    st["verdicts"] = {"A-1": Verdict("A-1", "agreed", "yes", {"items": ["04-02-00"]})}
    query = node(st, store=store, cfg=CFG)["audit"][0]["output"]["B-1"]["query"]
    assert "04-02-00" in query and "51-03-01" not in query


def test_narrow_citations_stay_in_same_law_family(store):
    from fake_law import ch as mk
    store.chunks[("WMA", "2")] = mk("WMA", "2", "정의", "제2조(정의) 「화학물질관리법」 제2조에 따른 유해화학물질과 시행령 제7조")
    store.chunks[("CMA", "2")] = mk("CMA", "2", "정의", "제2조(정의) 화학물질")
    qq = q("A-2", mins=(("WMA", "2", "정의"),), text="zzzz")
    narrow = {(c.law, c.key) for c in retrieve(qq, {}, {}, run(store))[0]}
    wide = {(c.law, c.key) for c in retrieve(qq, {}, {}, run(store), wide=True)[0]}
    assert ("CMA", "2") not in narrow and ("WMA_DEC", "7") in narrow       # 같은 계열(시행령)은 따라감
    assert ("CMA", "2") in wide


def test_retry_excludes_unquoted_previous_candidates(store):
    """재검색: 이전에 검색·인용으로 들어왔지만 아무도 인용하지 않은 조문은 빼고, 지정 조문(과 그 조각)·인용된 조문은 남긴다."""
    from law_agent.state import JudgeAnswer
    qq = q("D-1", when=None)
    first = node(AgentState(questions=[qq], case=CASES[0], facts={}, verdicts={}), store=store, cfg=CFG)["candidates"]["D-1"]
    assert ("WMA_RULE", "B4의2") in {(c.law, c.key) for c in first}          # 시험 전제: 검색으로 들어온 다른 별표
    ans = JudgeAnswer("D-1", "m1", "unknown", quotes=[{"ref": "WMA_RULE:B4의3@20261112#h0", "text": "x"}])
    st = AgentState(questions=[qq], case=CASES[0], facts={}, retry_count={"D-1": 1},
                    candidates={"D-1": first}, answers={"D-1": [ans]},
                    verdicts={"D-1": Verdict("D-1", "retry", retry_hint=["에너지 회수"])})
    assert rt.stale_candidates("D-1", st) == {("WMA_RULE", "B4의2")}       # 별표 4의3 조각(B4의3:2)은 지정 조문 것이라 남김
    out = node(st, store=store, cfg=CFG)
    keys = {(c.law, c.key) for c in out["candidates"]["D-1"]}
    assert ("WMA_RULE", "B4의3") in keys and ("WMA_RULE", "B4의2") not in keys
    assert out["audit"][0]["output"]["D-1"]["excluded_previous"] == ["WMA_RULE:B4의2"]
    # 인용된 조문은 빼지 않는다
    ans2 = JudgeAnswer("D-1", "m1", "unknown", quotes=[{"ref": "WMA_RULE:B4의2@20261112#h0", "text": "x"}])
    st["answers"] = {"D-1": [ans2]}
    assert rt.stale_candidates("D-1", st) == set()



def test_allowed_families():
    from law_agent.agents.retriever import allowed_families, law_family
    from law_agent.state import MinLaw, Question
    q = Question(id="G-3", stage="G", text="운반 기준", answer_type="text",
                 min_laws=[MinLaw.from_dict({"law": "WMA_DEC", "no": "7", "title": "x"}),
                           MinLaw.from_dict({"law": "WMA_RULE", "no": "9", "title": "y"})])
    assert allowed_families(q) == {"WMA"} and law_family("CEA_DESIG") == "CEA"
    q.search = True
    assert allowed_families(q) is None
