from law_agent.output import plain


def _it(qid, status="agreed", answer="yes", value=None, quotes=None, **kw):
    return {"question_id": qid, "status": status, "answer": answer, "value": value or {},
            "quotes": quotes or [], "conditions": kw.get("conditions", []), "missing_facts": kw.get("missing", [])}


EFF = {"values": {"waste_code": "51-03-01", "waste_name": "폐합성수지류"}}


def test_ref_text():
    assert plain.ref_text("WMA_RULE:16의13/호3@20261112#abc") == "폐기물관리법 시행규칙 제16조의13 제3호 (2026.11.12 시행)"
    assert plain.ref_text("WMA_RULE:B4의3@20261112") == "폐기물관리법 시행규칙 별표 4의3 (2026.11.12 시행)"
    assert plain.ref_text("WMA:13의2/항2") == "폐기물관리법 제13조의2 제2항"
    assert plain.ref_text("WMA_RULE:18의2/항5@20261112").endswith("제18조의2 제5항 (2026.11.12 시행)")
    assert plain.ref_text("WMA_RULE:BF5@20261112").startswith("폐기물관리법 시행규칙 별지 제5호서식")


def test_d1_mentions_r10_and_pre_analysis():
    it = _it("D-1", value={"items": ["R-3-2", "R-10"], "attrs": {"R-10": {"pre_analysis": True}}},
             quotes=[{"ref": "WMA_RULE:B4의3@20261112#x", "text": "51-03-01 폐합성수지류: R-3-2, R-10"}])
    s = plain.summarize(it, EFF, ["R-10"])
    assert s["title"] == "재활용 가능 유형" and s["label"] == "확인됨"
    assert "51-03-01(폐합성수지류)" in s["summary"] and "중간 가공(R-10)이 포함" in s["summary"] and "사전 분석" in s["summary"]
    assert s["basis"][0]["where"] == "폐기물관리법 시행규칙 별표 4의3 (2026.11.12 시행)"


def test_compare_and_text_and_status():
    h1 = plain.summarize(_it("H-1", value={"criterion": "월 평균 1톤 이상", "case_value": 1.21, "unit": "톤/월",
                                         "result": "met"}), EFF)
    assert h1["summary"] == "사업장폐기물배출자 신고 대상입니다." and "이 사례: 1.21톤/월" in h1["detail"]
    g1 = plain.summarize(_it("G-1", value={"text": "서면으로 계약할 것"}), EFF)
    assert g1["summary"].startswith("폐기물을 맡길 때") and g1["detail"] == "서면으로 계약할 것"
    ni = plain.summarize(_it("H-2", status="needs_info", answer="unknown", missing=["monthly_ton"]), EFF)
    assert ni["label"] == "정보 필요" and "월 발생량" in ni["summary"]
    nh = plain.summarize(_it("A-1", status="needs_human", answer="unknown"), EFF)
    assert nh["label"] == "담당자 확인 필요"
    a2 = plain.summarize(_it("A-2", answer="no"), EFF)
    assert a2["summary"].startswith("지정폐기물이 아닙니다")
    c = plain.summarize(_it("B-1", answer="conditional", conditions=["PCB 함유 시"]), EFF)
    assert c["label"] == "조건부" and c["conditions"] == ["PCB 함유 시"]


def test_attach_skips_not_applicable_and_component_title():
    rev = [_it("A-2", answer="no"), _it("C-1", status="not_applicable", answer="unknown"),
           _it("D-1.c2", value={"items": ["R-10"]})]
    cards = plain.attach(rev, EFF, ["R-10"])
    assert [c["question_id"] for c in cards] == ["A-2", "D-1.c2"]
    assert cards[1]["title"] == "재활용 가능 유형 (성분 2)"
    assert rev[1]["summary"] and rev[0]["title"] == "지정폐기물 여부"     # law_review 항목에도 붙음


def test_from_result_uses_saved_code():
    res = {"preliminary": {"classification": {"normalized_waste_code": "51-03-01"}},
           "law_review": [_it("A-1")]}
    cards = plain.from_result(res, ["R-10"])
    assert cards[0]["summary"].startswith("분류번호 51-03-01:")


def _base(**over):
    rev = {"A-2": _it("A-2", answer="no"), "B-1": _it("B-1", answer="no"),
           "D-1": _it("D-1", value={"items": ["R-3-2", "R-10"], "attrs": {"R-10": {"pre_analysis": False}}}),
           "G-1": _it("G-1", value={"text": "x"}),
           "H-1": _it("H-1", value={"result": "met", "criterion": "월 1톤", "case_value": 1.2, "unit": "톤"})}
    rev.update(over)
    return [v for v in rev.values() if v is not None]


def test_cards_possible_with_todos():
    c = plain.cards(_base(), EFF, ["R-10"], partners=5)
    assert c["conclusion"]["status"] == "가능" and "중간 가공(R-10) 재활용업체에 맡길 수 있습니다" in c["conclusion"]["text"]
    assert "추천 업체 5곳" in c["conclusion"]["notes"][-1]
    assert [t["key"] for t in c["todos"]] == ["capacity", "contract", "emitter_report"]
    assert c["checks"] == [] and c["reviewed"] is False


def test_cards_conditional_pre_analysis_and_designated():
    rev = _base(**{"A-2": _it("A-2", answer="yes"),
                   "D-1": _it("D-1", value={"items": ["R-10"], "attrs": {"R-10": {"pre_analysis": True}}}),
                   "H-2": _it("H-2", value={"result": "met"})})
    c = plain.cards(rev, EFF, ["R-10"])
    assert c["conclusion"]["status"] == "조건부 가능" and len(c["conclusion"]["notes"]) == 2
    keys = [t["key"] for t in c["todos"]]
    assert keys[0] == "pre_analysis" and "treatment_plan" in keys
    tp = next(t for t in c["todos"] if t["key"] == "treatment_plan")
    assert "1개월" in tp["when"]


def test_cards_not_possible():
    c = plain.cards(_base(**{"B-1": _it("B-1", answer="yes")}), EFF, ["R-10"])
    assert c["conclusion"]["status"] == "불가" and c["todos"] == []
    c2 = plain.cards(_base(**{"D-1": _it("D-1", value={"items": ["R-3-2"]})}), EFF, ["R-10"])
    assert c2["conclusion"]["status"] == "불가"


def test_cards_unknown_goes_to_checks():
    rev = _base(**{"A-2": _it("A-2", status="needs_human", answer="unknown"),
                   "H-1": _it("H-1", status="needs_info", answer="unknown", missing=["monthly_ton"])})
    c = plain.cards(rev, EFF, ["R-10"])
    assert c["conclusion"]["status"] == "확인 필요"
    assert c["checks"][0]["kind"] == "알려 주세요" and c["checks"][0]["text"] == "배출자 신고 대상인지 확인에 필요: 월 발생량"
    assert any(x["kind"] == "담당자 확인" and x["title"] == "지정폐기물 여부" for x in c["checks"])
    assert "emitter_report" not in [t["key"] for t in c["todos"]]


def test_show_cards_runs():
    out = plain.show_cards(plain.cards(_base(), EFF, ["R-10"]))
    assert "■ 결론: [가능]" in out and out.index("■ 판단 근거") < out.index("■ 해야 할 일") and "재활용 가능 유형 — 확인됨" in out and "위탁계약서 쓰기" in out


def test_save_cards(tmp_path):
    import json
    f = tmp_path / "C1_v1_result.json"
    f.write_text(json.dumps({"preliminary": {"classification": {"normalized_waste_code": "51-03-01"}},
                             "law_review": _base(), "routes": []}, ensure_ascii=False), encoding="utf-8")
    out = plain.save_cards(str(f))
    assert out[0].endswith("C1_v1_cards.json") and (tmp_path / "C1_v1_cards.html").read_text(encoding="utf-8").startswith("<!DOCTYPE html>") and (tmp_path / "C1_v1_cards.txt").read_text(encoding="utf-8").startswith("■ 결론")


DISPUTE = "모델 간 이견 — openai/gpt-6.1-sol 은(는) 'no' 로 판단. 사람 확인 필요"


def test_dispute_hidden_and_marked():
    a1 = _it("A-1", answer="conditional", conditions=[DISPUTE])
    a1["votes"] = [{"model": "a", "answer": "yes", "valid": True}, {"model": "b", "answer": "yes", "valid": True},
                   {"model": "openai/gpt-6.1-sol", "answer": "no", "valid": True}]
    s = plain.summarize(a1, EFF)
    assert s["label"] == "담당자 확인 필요" and "gpt" not in str(s) and s["conditions"] == []
    assert s["summary"].startswith("분류번호 51-03-01(폐합성수지류): 시행규칙 별표 4에") and "엇갈려" in s["summary"]
    c = plain.cards(_base(**{"A-1": a1}), EFF, ["R-10"])
    assert c["conclusion"]["status"] == "조건부 가능" and "담당자 확인 후 확정" in c["conclusion"]["notes"][0]
    assert any(x["title"] == "분류번호 확인" for x in c["checks"])


def test_dispute_on_key_question_blocks_conclusion():
    b1 = _it("B-1", answer="conditional", conditions=[DISPUTE])
    c = plain.cards(_base(**{"B-1": b1}), EFF, ["R-10"])
    assert c["conclusion"]["status"] == "확인 필요"


def test_pending_fact_condition_rewritten():
    it = _it("G-2", answer="conditional", conditions=["x/model 은(는) 다음 사실이 없어 판단을 보류함: 월 발생량 — 확인되면 'yes' 로 확정"])
    s = plain.summarize(it, EFF)
    assert s["conditions"] == ["다음 정보가 확인되면 확정됩니다: 월 발생량"] and "model" not in str(s["conditions"])


def test_negative_basis_role_and_clip():
    it = _it("B-2", answer="no", quotes=[{"ref": "WMA_DEC:B4의3@20261112", "text": "2. 폐농약(\n「농약관리법」…)" + "가" * 300}])
    s = plain.summarize(it, EFF)
    assert s["basis"][0]["role"] == "확인한 기준" and "\n" not in s["basis"][0]["quote_short"]
    assert s["basis"][0]["quote_short"].endswith("…") and len(s["basis"][0]["quote_short"]) <= 151
    long = plain.summarize(_it("D-3", value={"text": "공통기준을 지켜야 한다. " + "나" * 400}), EFF)
    assert long["detail_short"] == "공통기준을 지켜야 한다."


def test_dispute_conclusion_explains_why():
    b1 = _it("B-1", answer="conditional",
             conditions=[DISPUTE.replace("'no'", "'yes'"), "할로겐족 유기용제가 들어 있으면 재활용 금지"])
    b1["missing_facts"] = ["components"]
    c = plain.cards(_base(**{"B-1": b1}), EFF, ["R-10"])
    k = c["conclusion"]
    assert k["status"] == "확인 필요" and k["text"].startswith("재활용 금지 여부 판단이 엇갈려")
    joined = " ".join(k["notes"])
    assert "'재활용 금지'(으)로 봤습니다" in joined and "할로겐족" in joined and "성분" in joined and "참고용" in joined


def test_abstention_is_conditional_not_dispute():
    b1 = _it("B-1", answer="conditional",
             conditions=["openai/gpt-6.1-sol 은(는) 다음 사실이 없어 판단을 보류함: 세척용제의 구체 성분 — 확인되면 'no' 로 확정"])
    b1["flags"] = ["safety_veto"]
    b1["votes"] = [{"model": "a", "answer": "no", "valid": True}, {"model": "b", "answer": "unknown", "valid": True},
                   {"model": "c", "answer": "no", "valid": True}]
    c = plain.cards(_base(**{"B-1": b1}), EFF, ["R-10"])
    k = c["conclusion"]
    assert k["status"] == "조건부 가능"
    assert any("세척용제의 구체 성분 확인되면 '금지 아님'으로 확정" in n and "3개 중 2개" in n for n in k["notes"])
    s = plain.summarize(b1, EFF)
    assert s["label"] == "조건부" and not s["disputed"]


def test_withheld_ban_points_to_pcb_and_documents():
    b1 = _it("B-1", answer="conditional",
             conditions=["x 은(는) 다음 사실이 없어 판단을 보류함: 세척용제의 구체 성분 — 확인되면 'no' 로 확정"],
             quotes=[{"ref": "WMA:13의2@20261112#d5", "text": "2. 폴리클로리네이티드비페닐(PCBs)이 기후에너지환경부령으로 정하는 농도 이상 들어있는 폐기물"}])
    b1["votes"] = [{"answer": "no", "valid": True}, {"answer": "unknown", "valid": True}, {"answer": "no", "valid": True}]
    c = plain.cards(_base(**{"B-1": b1}), EFF, ["R-10"])
    assert any("PCB(폴리클로리네이티드비페닐)가 기준 농도 이상 들어 있지 않은지 확인되면" in n for n in c["conclusion"]["notes"])
    top = c["checks"][0]
    assert top["kind"] == "자료를 내 주세요" and "PCB 분석 결과" in top["text"] and "MSDS" in top["text"] and "제13조의2" in top["text"]
