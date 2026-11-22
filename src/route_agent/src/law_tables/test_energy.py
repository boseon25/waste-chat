"""에너지 회수기준 확인 테스트.

실행: $PY -m pytest -v law_tables/test_energy.py
"""
from types import SimpleNamespace

from law_tables.energy import emitter_questions, requirements


def _rows(status="confirmed"):
    def r(rid, item, rc, cmp_="", val="", unit="", cond=""):
        return {"행ID": rid, "항목": item, "r_codes": rc, "비교": cmp_, "기준값": val, "단위": unit,
                "조건": cond, "근거키": "WMA_RULE:3/항1/호1@x#h", "상태": status}
    return [r("ER-1-가", "저위발열량", "R-8-2", ">=", "3000", "kcal/kg"),
            r("ER-1-나", "회수효율", "R-8-2", ">=", "75", "%"),
            r("ER-3", "시멘트 소성로 연료", "R-8-1", "제외", cond="폐타이어 등")]


def _route(rcodes, codes=("51-03-01",)):
    return {"tier": "energy_recovery", "waste_codes": list(codes), "waste_status": "general",
            "steps": [{"r_codes": list(rcodes)}]}


def _case(lhv=None, mixed=False, unit="kcal/kg"):
    return SimpleNamespace(analysis={"heating_value_kcal": lhv, "heating_value_unit": unit} if lhv else {},
                           is_mixed=mixed, mixed_with_other=None)


def _by(reqs):
    return {r["row_id"]: r for r in reqs}


def test_lhv_compare():
    assert _by(requirements(_route(["R-8-2"]), _case(10200), _rows()))["ER-1-가"]["decision"] == "met"
    assert _by(requirements(_route(["R-8-2"]), _case(2500), _rows()))["ER-1-가"]["decision"] == "not_met"


def test_lhv_unknown_asks_emitter():
    reqs = requirements(_route(["R-8-2"]), _case(), _rows())
    assert _by(reqs)["ER-1-가"]["decision"] == "needs_info"
    assert emitter_questions(reqs)[0]["field"] == "heating_value"


def test_mixed_and_unit():
    assert _by(requirements(_route(["R-8-2"]), _case(9000, mixed=True), _rows()))["ER-1-가"]["decision"] == "needs_info"
    assert _by(requirements(_route(["R-8-2"]), _case(9000, unit="MJ/kg"), _rows()))["ER-1-가"]["decision"] == "needs_info"


def test_partner_items_and_rcode_filter():
    reqs = _by(requirements(_route(["R-8-2"]), _case(9000), _rows()))
    assert reqs["ER-1-나"]["decision"] == "needs_info" and reqs["ER-1-나"]["to"] == "partner"
    assert "ER-3" not in reqs                                                    # R-8-1 경로가 아님


def test_cement_kiln_excludes_designated():
    r = _by(requirements(_route(["R-8-1"], codes=["06-01-01"]), _case(), _rows()))["ER-3"]
    assert r["decision"] == "not_met"
    assert _by(requirements(_route(["R-8-1"]), _case(), _rows()))["ER-3"]["decision"] == "needs_info"


def test_draft_never_decides_and_other_tiers():
    r = _by(requirements(_route(["R-8-2"]), _case(10200), _rows("draft")))["ER-1-가"]
    assert r["decision"] == "needs_info" and r["draft_decision"] == "met"
    assert requirements({**_route(["R-8-2"]), "tier": "material_recycle"}, _case(10200), _rows()) == []
