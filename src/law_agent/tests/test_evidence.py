"""agents/evidence.py 테스트 — 실제 lawstore + 가짜 조문."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_law import build_store, ch
from law_agent.agents import evidence as ev
from law_agent.agents.evidence import EvidenceConfig, build_pack, code_ancestors, excerpt_codes, node, to_item
from law_agent.state import AgentState, Candidate, MinLaw, Question, Verdict, initial_state, quote_in

CASES = json.loads(Path(__file__).with_name("part1_sample.json").read_text(encoding="utf-8"))["cases"]
CFG = EvidenceConfig(max_chars=30000)


@pytest.fixture
def store():
    return build_store()


def q(qid="D-1", **kw):
    return Question(id=qid, stage=qid[0], text="질문", answer_type="list", **kw)


def cand(law, key, source="search", score=0.5, **kw):
    return Candidate(law=law, key=key, title="", score=score, source=source, **kw)


# ── 코드 ──────────────────────────────────────────────────────

def test_code_ancestors():
    assert code_ancestors("51-03-01") == ["51-03-01", "51-03"]
    assert code_ancestors("04-02-00") == ["04-02-00", "04-02"]
    assert code_ancestors("51") == ["51"] and code_ancestors("") == []
    assert code_ancestors("R-3-2") == ["R-3-2"]


def test_excerpt_codes_case_component_and_verdict_rcodes():
    case = json.loads(json.dumps(CASES[1]))
    case["components"][1]["waste_code"] = "06-01-00"
    st = initial_state(case)
    st["verdicts"] = {"D-1": Verdict("D-1", "agreed", "yes", {"items": ["R-6-1", "R-9-1"]}),
                      "D-1.c2": Verdict("D-1.c2", "agreed", "yes", {"items": ["R-6-2"]}),
                      "B-2": Verdict("B-2", "needs_human", "unknown", {"items": ["R-1"]})}   # 채택 안 된 건 안 씀
    sub = excerpt_codes(q("D-2.c2", parent_id="D-2", component=2), st)
    assert sub[:2] == ["06-01-00", "06-01"]                           # 성분 번호 먼저
    assert sub[2:4] == ["04-02-00", "04-02"]                          # 그다음 사례 번호
    assert sub[-3:] == ["R-6-1", "R-9-1", "R-6-2"] and "R-1" not in sub
    parent = excerpt_codes(q("D-2"), st)
    assert parent[:4] == ["04-02-00", "04-02", "06-01-00", "06-01"]   # 부모: 사례 번호 + 모든 성분 번호


# ── 후보 → 근거 ───────────────────────────────────────────────

def test_article_whole_with_count_check(store):
    item, note = to_item(cand("WMA_RULE", "16의13"), [], store)
    assert item.complete and item.text.startswith("제16조의13") and "2. 서면으로" in item.text
    assert item.ref == "WMA_RULE:16의13@20261112#h0" and not item.excerpt
    del store.chunks[("WMA_RULE", "16의13/항1/호2")]
    item, _ = to_item(cand("WMA_RULE", "16의13"), [], store)
    assert not item.complete and item.problems


def test_long_annex_rows_only(store):
    item, _ = to_item(cand("WMA_RULE", "B4의3", "min_laws", 1.0), ["04-02-00", "04-02", "77-77-77"], store)
    assert item.excerpt and "04-02-00" in item.text and "R-6-1" in item.text
    assert "51-03-01" not in item.text and len(item.text) < 500
    assert item.problems == ["이 별표에 행이 없는 코드: ['04-02', '77-77-77']"]   # 04-02 는 04-02-00 안에서 안 걸림
    assert quote_in("04-02-00 | 폐유기용제 | R-6-1, R-9-1", item.text)     # judge 가 이 행을 그대로 인용 가능


def test_long_annex_no_rows_is_left_out(store):
    item, note = to_item(cand("WMA_RULE", "B4의3"), ["88-88-88"], store)
    assert item is None and "찾지 못함" in note


def test_long_annex_non_box_falls_back_to_parts(store):
    body = "[별표 9] 긴 별표 (괘선 없음)\n" + "가나다라 " * 3000
    store.chunks[("WMA_RULE", "B9")] = ch("WMA_RULE", "B9", "긴 별표", body, typ="annex")
    store.chunks[("WMA_RULE", "B9:1")] = ch("WMA_RULE", "B9:1", "긴 별표", "1. 51-03-01 은 소각한다", typ="annex_part")
    store.chunks[("WMA_RULE", "B9:2")] = ch("WMA_RULE", "B9:2", "긴 별표", "2. 04-02-00 은 매립한다", typ="annex_part")
    items, _ = ev.to_items(cand("WMA_RULE", "B9"), ["04-02-00"], [], store)
    assert [i.key for i in items] == ["B9:2"] and items[0].text == "2. 04-02-00 은 매립한다" and items[0].excerpt
    assert items[0].ref == "WMA_RULE:B9:2@20261112#h0"


def test_long_annex_without_code_rows_uses_relevant_parts(store):
    # 별표 5 처럼 분류번호 행이 없고 절로 나뉜 긴 별표
    body = "[별표 5] 처리 기준\n" + "나라마바 " * 3000
    store.chunks[("WMA_RULE", "B5")] = ch("WMA_RULE", "B5", "처리 기준", body, typ="annex")
    store.chunks[("WMA_RULE", "B5:1")] = ch("WMA_RULE", "B5:1", "처리 기준", "1. 생활폐기물 기준 음식물", typ="annex_part")
    store.chunks[("WMA_RULE", "B5:2")] = ch("WMA_RULE", "B5:2", "처리 기준", "2. 지정폐기물 기준 폐유기용제 소각", typ="annex_part")
    store.chunks[("WMA_RULE", "B5:3")] = ch("WMA_RULE", "B5:3", "처리 기준", "3. 건설폐기물 기준", typ="annex_part")
    items, _ = ev.to_items(cand("WMA_RULE", "B5", "min_laws", 1.0), ["04-02-00"], ["폐유기용제", "지정폐기물"], store)
    assert [i.key for i in items] == ["B5:2"] and "관련도" in items[0].problems[0]
    assert items[0].source == "min_laws"
    items, note = ev.to_items(cand("WMA_RULE", "B5"), ["04-02-00"], ["ㅋㅋ"], store)
    assert items == [] and "관련 조각" in note


def test_row_chunk_candidate(store):
    item, _ = to_item(cand("WMA_RULE", "B4의3:2"), [], store)
    assert item.key == "B4의3:2" and item.excerpt and "R-6-1" in item.text
    assert item.ref == "WMA_RULE:B4의3:2@20261112#h0"


def test_short_annex_whole(store):
    item, _ = to_item(cand("WMA_RULE", "B4의2"), ["04-02-00"], store)
    assert not item.excerpt and "R-9-1" in item.text


def test_moved_recorded(store):
    item, _ = to_item(cand("WMA_RULE", "14의4", "min_laws", 1.0, moved=True), [], store)
    assert "제목으로 다시 찾은" in item.problems[-1]


def test_missing_candidate(store):
    assert to_item(cand("WMA", "999"), [], store)[0] is None


# ── 묶음 ──────────────────────────────────────────────────────

def test_pack_sorted_hashed_dedup(store):
    cands = [cand("WMA_RULE", "B4의3", "min_laws", 1.0), cand("WMA", "13의2", "citation", 0.5),
             cand("WMA_RULE", "B4의3:2", "search", 0.9), cand("WMA", "13의2", "search", 0.4)]
    pack, summ = build_pack(q(), cands, ["04-02-00"], store, CFG)
    assert [(i.law, i.key) for i in pack.items] == [("WMA", "13의2"), ("WMA_RULE", "B4의3"), ("WMA_RULE", "B4의3:2")]
    assert len(pack.pack_hash) == 64 and not pack.truncated
    again, _ = build_pack(q(), list(reversed(cands)), ["04-02-00"], store, CFG)
    assert again.pack_hash == pack.pack_hash                             # 후보 순서가 달라도 같은 묶음


def test_size_limit_drops_non_min_first(store):
    cands = [cand("WMA", "13의2", "search", 0.9), cand("WMA_RULE", "16의13", "min_laws", 1.0)]
    limit = len(store.get("WMA_RULE", "16의13").text) + 10
    pack, summ = build_pack(q(), cands, [], store, EvidenceConfig(max_chars=limit))
    assert [i.key for i in pack.items] == ["16의13"] and pack.truncated
    assert "크기 한도" in summ["notes"][0]


def test_min_law_bigger_than_limit_is_left_out(store):
    store.chunks[("GUIDE_DECL", "전체")] = ch("GUIDE_DECL", "전체", "지침", "가" * 40000, typ="whole")
    pack, _ = build_pack(q(), [cand("GUIDE_DECL", "전체", "min_laws", 1.0), cand("WMA", "25")], [], store, CFG)
    assert [i.key for i in pack.items] == ["25"] and pack.truncated


def test_empty_candidates_empty_pack(store):
    pack, summ = build_pack(q(), [], [], store, CFG)
    assert pack.items == [] and not pack.truncated and summ["items"] == 0


# ── 노드 ──────────────────────────────────────────────────────

def test_node_every_ready_question_gets_pack(store):
    qs = [q("B-1"), q("C-1"), q("D-1", when="B-1 != yes")]
    st = AgentState(questions=qs, verdicts={}, case=CASES[0], facts={},
                    candidates={"B-1": [cand("WMA", "13의2", "min_laws", 1.0)]})
    out = node(st, store=store, cfg=CFG)
    assert set(out["packs"]) == {"B-1", "C-1"}                        # D-1 은 다음 차례
    assert out["packs"]["C-1"].items == [] and out["audit"][0]["summary"]["empty"] == ["C-1"]
    assert out["packs"]["B-1"].items[0].source == "min_laws"


def test_node_end_to_end_with_retriever(store):
    from law_agent.agents import planner, retriever
    st = initial_state(CASES[1])
    st["questions"] = [Question(id="D-1", stage="D", text="허용된 재활용 유형", answer_type="list",
                                min_laws=[MinLaw("WMA_RULE", "B4의3", "폐기물의 종류별 재활용 유형")])]
    st.update(retriever.node(st, store=store, cfg=retriever.RetrieverConfig(12, 2, 0.01)))
    out = node(st, store=store, cfg=CFG)
    pack = out["packs"]["D-1"]
    row = next(i for i in pack.items if i.key == "B4의3")
    assert row.excerpt and "04-02-00" in row.text and "51-03-01" not in row.text
    assert sum(len(i.text) for i in pack.items) <= CFG.max_chars


def test_config_from_settings():
    c = EvidenceConfig.from_settings()
    assert c.max_chars == 30000 and c.narrow_max_chars == 6000


# ── 단계 (좁게 → 넓게) ───────────────────────────────────────

def test_narrow_limit_not_marked_truncated_for_search_items(store):
    cands = [cand("WMA_RULE", "16의13", "min_laws", 1.0), cand("WMA", "13의2", "search", 0.9)]
    limit = len(store.get("WMA_RULE", "16의13").text) + 10
    cfg = EvidenceConfig(max_chars=limit, narrow_max_chars=limit)
    narrow, sn = build_pack(q(), cands, [], store, cfg, wide=False)
    wide, sw = build_pack(q(), cands, [], store, cfg, wide=True)
    assert [i.key for i in narrow.items] == ["16의13"] and not narrow.truncated      # 좁게: 의도된 제한
    assert wide.truncated                                                          # 넓게: 빠진 것 = 잘림
    assert sn["stage"] == "narrow" and sn["limit"] == limit


def test_narrow_min_law_dropped_is_truncated(store):
    cfg = EvidenceConfig(max_chars=30000, narrow_max_chars=10)
    pack, _ = build_pack(q(), [cand("WMA_RULE", "16의13", "min_laws", 1.0)], [], store, cfg, wide=False)
    assert pack.items == [] and pack.truncated


def test_node_stage_from_retry_count(store):
    qq = q("D-1")
    big = [cand("WMA_RULE", "16의13", "min_laws", 1.0), cand("WMA", "13의2", "search", 0.9)]
    cfg = EvidenceConfig(max_chars=30000, narrow_max_chars=len(store.get("WMA_RULE", "16의13").text) + 10)
    st = AgentState(questions=[qq], verdicts={}, case=CASES[0], facts={}, candidates={"D-1": big})
    assert len(node(st, store=store, cfg=cfg)["packs"]["D-1"].items) == 1
    st["retry_count"] = {"D-1": 1}
    st["verdicts"] = {"D-1": Verdict("D-1", "retry")}
    assert len(node(st, store=store, cfg=cfg)["packs"]["D-1"].items) == 2


def test_evidence_text_is_cleaned(store):
    store.chunks[("WMA", "26")] = ch("WMA", "26", "x", "제26조(x) 보관한다. <개정 2013.5.31>\n[본조신설 2008.8.4]")
    item, _ = to_item(cand("WMA", "26"), [], store)
    assert item.text == "제26조(x) 보관한다."


def test_smoke_script_runs_on_fake_store(store, monkeypatch, capsys, tmp_path):
    from law_agent.eval import smoke_evidence
    from law_agent.tools import law_tools
    law_tools.set_store(store)
    try:
        out = tmp_path / "r.json"
        assert smoke_evidence.main([str(Path(__file__).with_name("part1_sample.json")), "--case", "DEMO-PART2-02",
                                    "--json", str(out)]) == 0
        text = capsys.readouterr().out
        assert "DEMO-PART2-02" in text and "확인할 질문" in text
        rows = json.loads(out.read_text(encoding="utf-8"))["DEMO-PART2-02"]
        assert any(r["qid"] == "A-1" for r in rows) and not any("." in r["qid"] for r in rows)   # 분리 안 되는 혼합물 → 하위 질문 없음
    finally:
        law_tools.set_store(None)


def test_long_whole_document_uses_relevant_sections(store):
    # fake_law 의 ALLBARO_NOTICE "전체" 는 5만 자 → 같은 법령의 조문 중 관련 있는 것만
    store.chunks[("ALLBARO_NOTICE", "5")] = ch("ALLBARO_NOTICE", "5", "인계서 작성", "제5조(인계서 작성) 배출자는 전자인계서를 작성한다.")
    store.chunks[("ALLBARO_NOTICE", "9")] = ch("ALLBARO_NOTICE", "9", "수수료", "제9조(수수료) 수수료는 없다.")
    items, note = ev.to_items(cand("ALLBARO_NOTICE", "전체", "min_laws", 1.0), [], ["전자인계서 작성"], store)
    assert [i.key for i in items] == ["5"] and items[0].source == "min_laws" and "통째 문서" in items[0].problems[0]
    items, note = ev.to_items(cand("ALLBARO_NOTICE", "전체", "min_laws", 1.0), [], ["ㅋㅋ"], store)
    assert items == [] and "관련 조문" in note


def test_short_whole_document_kept_whole(store):
    store.chunks[("CEA_DESIG", "전체")] = ch("CEA_DESIG", "전체", "고시", "짧은 고시 전문", typ="whole")
    item, _ = to_item(cand("CEA_DESIG", "전체", "min_laws", 1.0), [], store)
    assert item.key == "전체" and item.text == "짧은 고시 전문"


# ── 판단 누적 ────────────────────────────────────────────────

def test_excerpt_codes_use_a1_confirmed_code_first():
    st = initial_state(CASES[0])                                     # 파트 1 번호 51-03-01
    st["verdicts"] = {"A-1": Verdict("A-1", "agreed", "yes", {"items": ["51-04-01"]})}
    codes = excerpt_codes(q("D-1"), st)
    assert codes[:3] == ["51-04-01", "51-04", "51-03-01"]           # 확정 번호 먼저, 파트 1 번호도 뒤에


def test_evidence_queries_use_a2_status():
    st = initial_state(CASES[0])
    st["verdicts"] = {"A-2": Verdict("A-2", "agreed", "yes")}
    assert ev.evidence_queries(q("F-1"), st)[-1] == "지정폐기물"
    st["verdicts"] = {"A-2": Verdict("A-2", "agreed", "no")}
    assert ev.evidence_queries(q("F-1"), st)[-1] == "사업장일반폐기물"


def test_list_annex_part_trimmed_to_code_group(store):
    from test_law_tools import LIST_ANNEX
    store.chunks[("WMA_RULE", "B4")] = ch("WMA_RULE", "B4", "폐기물의 종류별 세부분류", "머리 " + "가" * 9000, typ="annex")
    store.chunks[("WMA_RULE", "B4:2")] = ch("WMA_RULE", "B4:2", "폐기물의 종류별 세부분류", LIST_ANNEX + "\n" + "나" * 3000,
                                            typ="annex_part")
    items, _ = ev.to_items(cand("WMA_RULE", "B4", "min_laws", 1.0), ["51-03-01", "51-03"], [], store)
    assert len(items) == 1 and items[0].key == "B4:2" and "51-03-07" in items[0].text
    assert len(items[0].text) < 200 and "묶음" in items[0].problems[0]
    assert quote_in("51-03-01 폐합성수지류(폐염화비닐수지류는 제외한다)", items[0].text)


def test_narrow_citation_failure_not_truncated(store):
    cands = [cand("WMA", "13의2", "min_laws", 1.0), cand("WMA_RULE", "B4의3", "citation", 0.5)]
    narrow, s = build_pack(q(), cands, ["88-88-88"], store, CFG, wide=False)      # 별표 행 못 찾음
    assert not narrow.truncated and s["notes"]
    wide, _ = build_pack(q(), cands, ["88-88-88"], store, CFG, wide=True)
    assert wide.truncated
