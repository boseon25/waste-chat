"""처리계획 확인 대상 표 테스트.

실행: $PY -m pytest -v law_tables/test_plan_check.py
"""
import pytest

from law_tables.plan_check import judge


def _rows(status="confirmed"):
    def t(rid, ho, name, pre, each, total=""):
        return {"행ID": rid, "구분": "대상", "항": "1", "호": ho, "대상": name, "분류번호_앞자리": pre,
                "각각_kg_월": each, "합계_kg_월": total, "내용": "", "근거키": f"WMA_RULE:18의2/항1/호{ho}@x#h",
                "원문": "", "상태": status}
    return [t("A", "1", "오니", "01-02", "500"), t("B", "2", "폐유", "06", "50", "130"),
            t("C", "2", "폐유기용제", "04", "50", "130"), t("D", "3의2", "폐석면", "07", "20"),
            t("E", "6", "의료폐기물", "10", "0"), t("F", "9", "고시된 지정폐기물", "30", "고시"),
            {"행ID": "P4", "구분": "절차", "항": "4", "호": "", "대상": "서류", "분류번호_앞자리": "",
             "각각_kg_월": "", "합계_kg_월": "", "내용": "폐기물처리계획서=제12호서식", "근거키": "k4", "원문": "",
             "상태": status}]


@pytest.mark.parametrize("codes,kg,want", [
    (["01-02-01"], 600, "required"), (["01-02-01"], 400, "not_required"),
    (["06-01-01"], 60, "required"), (["06-01-01", "04-01-00"], 140, "required"),       # 합계 130 이상
    (["06-01-01", "04-01-00"], 40, "not_required"),                                  # 둘 다 50 미만
    (["06-01-01", "04-01-00"], 80, "needs_info"),                               # 품목별 양 모름
    (["10-13-00"], None, "required"), (["10-13-00"], 1, "required"),                   # 양과 관계없이
    (["01-02-01"], None, "needs_info"), (["30-00-00"], 100, "needs_info"),
    (["03-10-01"], 5000, "not_required"),                                            # 제1항 각 호에 없음
])
def test_judge(codes, kg, want):
    r = judge(codes, kg, _rows())
    assert r["decision"] == want, r
    assert r["documents"] == "폐기물처리계획서=제12호서식"


def test_draft_rows_never_decide():
    r = judge(["10-13-00"], 1, _rows("draft"))
    assert r["decision"] == "needs_info" and r["draft_decision"] == "required"


def test_longest_prefix():
    rows = _rows() + [{**_rows()[0], "행ID": "Z", "분류번호_앞자리": "01-02-03", "각각_kg_월": "0"}]
    assert judge(["01-02-03"], 1, rows)["decision"] == "required"
    assert judge(["01-02-01"], 1, rows)["decision"] == "not_required"
