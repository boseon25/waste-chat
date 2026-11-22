"""지정폐기물 사전 확인 질문 테스트.

실행: $PY -m pytest -v law_tables/test_designated_check.py
"""
from types import SimpleNamespace

from law_tables.designated_check import request

ROWS = [
    {"rule_id": "DC-T-01", "구분": "확인대상", "대상": "오니류, 폐흡착제, 폐흡수제", "적용_분류번호": "51-01;51-02;51-11;01-02;03-08",
     "확인_항목": "", "근거_청크": "GUIDE_DECL:§Ⅵ@20251231#a", "status": "confirmed"},
    {"rule_id": "DC-T-02", "구분": "확인대상", "대상": "광재, 분진 …", "적용_분류번호": "51-04;51-05;03-02;11-03",
     "확인_항목": "", "근거_청크": "GUIDE_DECL:§Ⅵ@20251231#a", "status": "confirmed"},
    {"rule_id": "DC-T-03", "구분": "확인대상", "대상": "기름성분", "적용_분류번호": "", "확인_항목": "기름성분",
     "근거_청크": "GUIDE_DECL:§Ⅵ@20251231#a", "status": "confirmed"},
    {"rule_id": "DC-H-01", "구분": "유해물질", "대상": "유해물질", "적용_분류번호": "",
     "확인_항목": "별표 1 유해물질, 기름성분, 석면, PCB", "근거_청크": "WMA_RULE:17의2@20261002#b", "status": "confirmed"},
    {"rule_id": "DC-W-01", "구분": "확인시기", "대상": "배출자 신고·변경신고 때", "적용_분류번호": "", "확인_항목": "",
     "근거_청크": "WMA_RULE:17의2@20261002#b", "status": "confirmed"},
]


def _case(analysis=None, subs=()):
    return SimpleNamespace(analysis=analysis or {}, contamination={"substances": list(subs)}, waste_code=None)


def _r(*codes):
    return [{"waste_codes": list(codes)}]


def test_sludge_asks_analysis():
    q = request(_case(), _r("51-01-01"), ROWS, ["R1"])
    assert q["field"] == "designated_check" and "51-01-01" in q["text"] and "(근거 표 확인 전)" not in q["text"]
    assert q["affected_routes"] == ["R1"] and q["when"] == ["배출자 신고·변경신고 때"]
    assert "WMA_RULE:17의2@20261002#b" in q["basis"]


def test_no_question_cases():
    assert request(_case(), _r("51-03-01"), ROWS, ["R1"]) is None                       # 대상 아님
    assert request(_case({"leaching_ok": True}), _r("51-01-01"), ROWS, ["R1"]) is None   # 분석 결과 있음
    assert request(_case(), _r("04-02-00"), ROWS, ["R1"]) is None                       # 이미 지정폐기물
    assert request(_case(), _r("51-01-01"), [], ["R1"]) is None                         # 표 없음


def test_oil_contamination_and_draft():
    q = request(_case(subs=["기계유"]), _r("51-03-01"), ROWS, ["R1"])
    assert q and q["matched"][0]["rule_id"] == "DC-T-03"
    draft = [{**r, "status": "draft"} for r in ROWS]
    q2 = request(_case(), _r("51-05-01"), draft, ["R1"])
    assert q2["text"].endswith("(근거 표 확인 전)") and q2["basis_status"] == "draft"
