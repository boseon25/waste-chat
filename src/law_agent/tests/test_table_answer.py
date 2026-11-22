"""표 답 노드 (2026.11.18) — 서버 표의 실제 행 모양으로 만든 작은 표."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from law_agent.agents import table_answer as ta
from law_agent.state import Question, Verdict, initial_state
from law_agent.tools import fact_tools

REF43 = "WMA_RULE:B4의3@20261112#7aeb303d9fe8dcf0"
REF5 = "WMA_RULE:B5:3@20261002#8b91601a27cc5291"
TABLES = {
    "recycling_types_4_3.csv": (["분류번호", "폐기물_구분", "폐기물의_종류", "묶음", "r_code", "사전분석확인", "재활용_금지", "상태", "근거"], [
        ["51-03-01", "사업장일반폐기물", "폐합성수지류", "1", "R-1", "해당 없음", "N", "상위코드(아래 세부코드로 펼침)", REF43],
        ["51-03-01", "사업장일반폐기물", "폐합성수지류", "1", "R-1-1", "해당 없음", "N", "R-1에서 펼침", REF43],
        ["51-03-01", "사업장일반폐기물", "폐합성수지류", "1", "R-3-2", "해당 없음", "N", "", REF43],
        ["51-03-01", "사업장일반폐기물", "폐합성수지류", "1", "R-10", "해당 없음", "N", "", REF43],
        ["51-03-01", "사업장일반폐기물", "폐합성수지류", "2", "R-9-1", "해당", "N", "", REF43],
        ["51-03-01", "사업장일반폐기물", "폐합성수지류", "2", "R-10", "해당", "N", "", REF43],
        ["99-99-01", "사업장일반폐기물", "금지 시험", "1", "R-3-2", "해당 없음", "Y", "", REF43]]),
    "not_in_4_3.csv": (["분류번호", "상태"], [["02-01-04", "별표 4의3에 유형 없음 → 비고2 재활용환경성평가(R-11)만"]]),
    "designated_resources.csv": (["품목", "분류번호", "허용_R코드", "품목_한정", "적용범위_원문", "방법기준_원문", "그밖의_준수사항", "근거"], [
        ["폐지류", "51-28-02", "R-4-3", "Y", "폐종이류(51-28-02) … 종이팩은 제외한다.", "이물질 제거·압축", "", "CEA_DESIG:B2@x"],
        ["고철", "51-29-01", "R-4-1", "N", "고철(51-29-01)", "", "", "CEA_DESIG:B2@x"],
        ["폐합성수지류 중 아이씨트레이(IC-Tray)", "51-03-01", "R-4-4", "Y", "IC-Tray 에 한하여", "이물질 제거", "", "CEA_DESIG:B2@x"]]),
    "disposal_methods_5.csv": (["rule_id", "대상_폐기물", "적용_분류번호", "처리방법_코드", "처리방법_원문", "조건", "수치_기준", "action",
                                "근거_인용", "confidence", "근거_청크", "모델", "status"], [
        ["D3-1가", "소각 가능한 사업장일반폐기물", "", "2101;2102", "소각", "", "1일 평균 100킬로그램 이상", "REQUIRE", "…", "high", REF5, "", "confirmed"],
        ["D3-2자", "폐합성고분자화합물", "51-03-01;51-03-03", "2101;2102;2201", "소각 (곤란하면 파쇄 후 매립)", "소각이 곤란한 경우에만 매립",
         "최대지름 15센티미터 이하", "REQUIRE", "폐합성고분자화합물은 소각하여야 한다.", "high", REF5, "", "confirmed"]]),
}


@pytest.fixture
def tables(tmp_path, monkeypatch):
    for name, (head, rows) in TABLES.items():
        with open(tmp_path / name, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(head)
            w.writerows(rows)
    monkeypatch.setattr(fact_tools, "tables_dir", lambda: tmp_path)
    fact_tools._read.cache_clear()
    fact_tools.TABLE_USES.clear()
    yield tmp_path
    fact_tools._read.cache_clear()


def test_d1_original_codes_and_pre_analysis_per_code(tables):
    v = ta.d1("51-03-01")
    assert v.status == "agreed" and v.answer == "yes" and "table_answer" in v.flags
    assert v.value["items"] == ["R-1", "R-3-2", "R-10", "R-9-1"]                  # 펼친 R-1-1 은 빼고, R-10 은 한 번
    assert v.value["attrs"]["R-10"]["pre_analysis"] is True                       # 두 묶음에 나오면 참 쪽
    assert v.value["attrs"]["R-3-2"]["pre_analysis"] is False
    assert v.quotes[0]["ref"] == REF43 and fact_tools.TABLE_USES[0]["table"] == "recycling_types_4_3.csv"


def test_d1_not_in_table_prohibited_and_unknown(tables):
    assert ta.d1("02-01-04").answer == "no" and "R-11" in ta.d1("02-01-04").conditions[0]["text"]
    assert ta.d1("99-99-01").answer == "no"
    assert ta.d1("51-03-02") is None                                               # 표에 없음 → LLM


def test_c1(tables):
    assert ta.c1("51-29-01", {}).answer == "yes"
    assert ta.c1("40-02-07", {}).answer == "no"
    assert ta.c1("51-03-01", {"name": "제품 포장용 PE 필름 자투리"}) is None          # 품목 한정(IC-Tray) → LLM
    v = ta.c1("51-03-01", {"name": "반도체 아이씨트레이 폐품"})
    assert v.answer == "conditional" and v.value["r_codes"] == ["R-4-4"]
    assert ta.c1("51-28-02", {}) is None                                           # 품목 한정만 있고 이름 안 맞음


def test_f1_f2(tables):
    v = ta.f1("51-03-01")
    assert v.answer == "conditional" and v.value["items"] == ["2101", "2102", "2201"]
    assert "15센티미터" in v.conditions[0]["text"] and v.quotes[0]["ref"] == REF5
    assert ta.f1("51-05-01") is None                                               # 분류번호 행 없음 → LLM (일반 규정 해석)
    assert ta.f2("51-03-01").answer == "conditional"


def test_node_uses_confirmed_code_only(tables):
    st = initial_state({"case_id": "X", "name": "PE 필름", "classification": {"selected_code": "51-03-01"}})
    st["questions"] = [Question(id=i, stage=i[0], text="q", answer_type="list") for i in ("A-1", "D-1", "F-1", "G-1")]
    st["facts"] = {"values": {"waste_code": "51-03-01"}, "missing": []}
    st["verdicts"] = {}
    out = ta.node(st)
    assert "verdicts" not in out                                                   # A-1 미확정 → 표로 답하지 않음
    st["verdicts"] = {"A-1": Verdict("A-1", "agreed", "yes", value={"items": ["51-03-01"]})}
    out = ta.node(st)
    assert set(out["verdicts"]) == {"D-1", "F-1"} and out["audit"][0]["summary"]["answered"] == ["D-1", "F-1"]


def test_missing_table_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(fact_tools, "tables_dir", lambda: tmp_path)
    fact_tools._read.cache_clear()
    q = Question(id="D-1", stage="D", text="q", answer_type="list")
    eff = {"confirmed": {"waste_code": "A-1"}, "values": {"waste_code": "51-03-01"}}
    assert ta.answer(q, {"case": {}}, eff) is None


ER = (["행ID", "호", "목", "r_codes", "항목", "비교", "기준값", "단위", "입력", "조건", "근거키", "원문", "상태"], [
    ["ER-1-가", "1", "가", "R-8-2;R-9-1", "저위발열량", ">=", "3000", "kcal/kg", "analysis.heating_value_kcal", "", "WMA_RULE:3@x", "저위발열량 3천 이상", "confirmed"],
    ["ER-1-나", "1", "나", "R-8-2;R-9-1", "회수효율", ">=", "75", "%", "partner.recovery_efficiency_pct", "", "WMA_RULE:3@x", "회수효율 75% 이상", "confirmed"]])


def _e1_state(heating):
    st = initial_state({"case_id": "X", "analysis": {"heating_value_kcal": heating}})
    st["verdicts"] = {"D-1": Verdict("D-1", "agreed", "yes", value={"items": ["R-3-2", "R-9-1"]})}
    return st


def test_e1_partner_values_become_conditions(tables):
    with open(tables / "energy_recovery_criteria.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(ER[0]); w.writerows(ER[1])
    v = ta.e1(_e1_state(10200))
    assert v.status == "agreed" and v.answer == "conditional" and v.value["result"] == "met"
    assert "회수효율" in v.conditions[0]["text"] and not v.missing_facts          # 업체 값은 파트 1 질문이 아님
    assert ta.e1(_e1_state(2500)).value["result"] == "not_met"
    nv = ta.e1(_e1_state(None))
    assert nv.status == "needs_info" and nv.missing_facts == ["저위발열량"]


def test_provisional_code_when_a1_unconfirmed(tables, monkeypatch):
    """A-1 이 needs_info 여도 파트 1 번호가 별표 4에 있으면 잠정으로 표 답 → conditional + 이유 (데모 2: 04-02-00)."""
    monkeypatch.setattr("law_agent.tools.fact_tools.waste_code_exists", lambda c, *a, **k: c == "51-03-01")
    st = initial_state({"case_id": "X", "classification": {"selected_code": "51-03-01"}})
    st["questions"] = [Question(id=i, stage=i[0], text="q", answer_type="list") for i in ("A-1", "D-1")]
    st["facts"] = {"values": {"waste_code": "51-03-01"}, "missing": []}
    st["verdicts"] = {"A-1": Verdict("A-1", "needs_info", "unknown", missing_facts=["할로겐족 유기용제 여부"])}
    v = ta.node(st)["verdicts"]["D-1"]
    assert v.answer == "conditional" and "provisional_code" in v.flags and "확정되지 않음" in v.conditions[0]["text"]
    monkeypatch.setattr("law_agent.tools.fact_tools.waste_code_exists", lambda c, *a, **k: False)
    assert "verdicts" not in ta.node(st)                                           # 표에 없는 번호 → 잠정도 안 씀
