"""tools/fact_tools.py 테스트 — 가짜 CSV, 실제 settings.yaml."""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from law_agent.settings import load_settings
from law_agent.tools import fact_tools as ft

PKG = Path(__file__).resolve().parents[1]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def tables(tmp_path, monkeypatch):
    write_csv(tmp_path / "waste_codes_4.csv", [
        {"분류번호": "51-03-01", "폐기물_구분": "사업장일반", "단계": "4", "상위": "51-03", "명칭": "폐합성수지류",
         "지정폐기물": "N", "근거": "WMA_RULE:B4@20261112", "말단": "Y"},
        {"분류번호": "01-01-01", "폐기물_구분": "지정", "단계": "4", "상위": "01-01", "명칭": "폐유기용제",
         "지정폐기물": "Y", "근거": "WMA_RULE:B4@20261112", "말단": "Y"},
    ])
    write_csv(tmp_path / "rcode_detail_4_2.csv", [
        {"r_code": "R-3", "단계": "2", "상위": "", "설명": "물질 재활용", "tier": "material_recycle", "근거": "B4의2"},
        {"r_code": "R-3-2", "단계": "3", "상위": "R-3", "설명": "합성수지 원료 제조", "tier": "", "근거": "B4의2"},
    ])
    write_csv(tmp_path / "treatment_code_map.csv", [
        {"code": "2003", "처리구분": "위탁처리", "처리분류": "재활용", "세분류": "물질 재활용", "근거": "BF5", "r_code": "R-3",
         "baseline": "", "tier": "", "carbon_factor_key": "k", "transport_needed": "Y", "confidence": "", "note": ""},
        {"code": "1101", "처리구분": "소각", "처리분류": "소각", "세분류": "일반 소각", "근거": "BF5", "r_code": "",
         "baseline": "", "tier": "", "carbon_factor_key": "k", "transport_needed": "Y", "confidence": "", "note": ""},
        {"code": "1201", "처리구분": "매립", "처리분류": "매립", "세분류": "관리형 매립", "근거": "BF5", "r_code": "",
         "baseline": "", "tier": "", "carbon_factor_key": "k", "transport_needed": "Y", "confidence": "", "note": ""},
    ])
    write_csv(tmp_path / "recycling_types_4_3.csv", [{"분류번호": "51-03-01", "r_code": "R-3-2"}])
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path))
    ft.clear_cache()
    yield tmp_path
    ft.clear_cache()


# ── 판정표 차단 ───────────────────────────────────────────────

def test_every_blocked_table_raises(tables):
    blocked = load_settings()["data"]["blocked_tables"]
    assert "recycling_types_4_3.csv" in blocked and "reporter_scope.csv" in blocked
    for name in blocked:
        with pytest.raises(ft.BlockedTableError):
            ft.read_table(name)


def test_not_allowed_and_paths(tables):
    for name in ["random.csv", "../waste_codes_4.csv", "sub/waste_codes_4.csv"]:
        with pytest.raises(ft.NotAllowedTableError):
            ft.read_table(name)
    ft.check_table_name("partners_recycling.csv")            # 패턴 partners_*.csv
    ft.check_table_name("facility_master.csv")


def test_blocked_check_before_file_open(tables):
    # 파일이 실제로 있어도 열기 전에 막는다
    assert (tables / "recycling_types_4_3.csv").exists()
    with pytest.raises(ft.BlockedTableError):
        ft.read_table("recycling_types_4_3.csv")


# ── 분류번호 ─────────────────────────────────────────────────

def test_waste_code_hides_designated_column(tables):
    info = ft.waste_code_info("01-01-01")
    assert info["명칭"] == "폐유기용제" and "지정폐기물" not in info
    assert ft.waste_code_exists("51-03-01") and not ft.waste_code_exists("99-99-99")
    assert ft.waste_code_name("51-03-01") == "폐합성수지류"
    assert ft.waste_code_name(" 51-03-01 ") == "폐합성수지류"
    assert ft.waste_code_name("99") is None


def test_rows_are_copies(tables):
    ft.waste_code_info("51-03-01")["명칭"] = "바뀜"
    assert ft.waste_code_name("51-03-01") == "폐합성수지류"
    ft.read_table("waste_codes_4.csv")[0]["명칭"] = "바뀜"
    assert ft.read_table("waste_codes_4.csv")[0]["명칭"] == "폐합성수지류"


# ── R코드·처리방법 코드 ──────────────────────────────────────

def test_r_code(tables):
    info = ft.r_code_info("R-3-2")
    assert info["설명"] == "합성수지 원료 제조" and "tier" not in info
    assert ft.r_code_exists("R-3") and not ft.r_code_exists("R-99")
    assert ft.r_code_tier("R-3-2") == "material_recycle"       # 비어 있으면 상위 코드 값
    assert ft.r_code_tier("R-99") is None


def test_treatment_code(tables):
    info = ft.treatment_code_info("2003")
    assert info == {"code": "2003", "처리구분": "위탁처리", "처리분류": "재활용", "세분류": "물질 재활용",
                    "근거": "BF5", "r_code": "R-3"}
    assert ft.treatment_code_name("1101") == "일반 소각"
    assert ft.treatment_code_name("0000") is None
    assert [r["code"] for r in ft.treatment_codes("소각")] == ["1101"]
    assert [r["code"] for r in ft.treatment_codes("매립")] == ["1201"]
    assert len(ft.treatment_codes()) == 3


def test_code_kind_and_check(tables):
    assert ft.code_kind("R-3-2") == "r_code"
    assert ft.code_kind("2003") == "treatment_code"
    assert ft.code_kind("51-03-01") == "waste_code"
    assert ft.code_kind("51") == "waste_code"
    assert ft.code_kind("폐기물 종합재활용업") is None
    assert ft.check_code("R-3-2") == {"code": "R-3-2", "kind": "r_code", "exists": True, "name": "합성수지 원료 제조"}
    assert ft.check_code("R-77")["exists"] is False
    assert ft.check_code("51-03-01")["name"] == "폐합성수지류"
    assert ft.check_code("9999")["exists"] is False
    assert ft.check_code("재활용업")["exists"] is None


def test_missing_env(monkeypatch):
    monkeypatch.delenv("WASTE_DATA_DIR", raising=False)
    ft.clear_cache()
    with pytest.raises(RuntimeError):
        ft.waste_code_name("51-03-01")


# ── 코드 전체 정적 검사 (모든 모듈에 계속 적용) ────────────────

def test_no_blocked_table_names_in_law_agent_code():
    """eval/ 와 tests/ 밖의 .py (core/ 포함) 에 판정표 이름·gates.tables 가 없어야 한다 (규칙 2)."""
    data = load_settings()["data"]
    blocked, exception = data["blocked_tables"], set(data.get("partner_matching_exception") or [])
    bad = []
    for p in PKG.rglob("*.py"):
        rel = p.relative_to(PKG).parts
        if rel[0] in ("eval", "tests"):
            continue
        text = p.read_text(encoding="utf-8")
        # 예외 표(reporter_scope)는 업체 표 읽기(core/partners/data.py)에서만 이름을 쓸 수 있다
        allowed_here = exception if rel[-2:] == ("partners", "data.py") else set()
        if rel[-2:] == ("agents", "table_answer.py"):        # 2026.11.18: 표 답 노드는 table_answer_tables 만
            allowed_here = set(data.get("table_answer_tables") or [])
        bad += [f"{'/'.join(rel)}: {n}" for n in blocked if n in text and n not in allowed_here]
        if re.search(r"gates\.tables|from gates import tables|Tables\.load", text):
            bad.append(f"{p.name}: gates.tables")
    assert not bad, bad


def test_no_hardcoded_keys_in_law_agent_code():
    bad = []
    for p in PKG.rglob("*.py"):
        if "tests" in p.relative_to(PKG).parts:
            continue
        if re.search(r"api_key\s*=\s*['\"]", p.read_text(encoding="utf-8"), re.I):
            bad.append(p.name)
    assert not bad, bad



def test_no_route_agent_imports():
    """route_agent 는 비교 기준 — law_agent 는 import 하지 않는다 (core/ 에 복사해 둠)."""
    bad = []
    for p in PKG.rglob("*.py"):
        if "tests" in p.relative_to(PKG).parts:
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if re.match(r"\s*(from|import)\s+(gates|partners|preliminary|proposal|recommend|carbon|lawstore|law_tables|laws|pipeline)\b", ln):
                bad.append(f"{p.relative_to(PKG)}: {ln.strip()}")
    assert not bad, bad


def test_exception_table(tables, monkeypatch):
    write_csv(tables / "reporter_scope.csv", [{"scope_id": "S1", "대상_폐기물": "폐합성수지"}])
    ft.EXCEPTION_USES.clear()
    rows = ft.read_exception_table("reporter_scope.csv")
    assert rows[0]["scope_id"] == "S1" and ft.EXCEPTION_USES[0]["table"] == "reporter_scope.csv"
    with pytest.raises(ft.BlockedTableError):
        ft.read_exception_table("recycling_types_4_3.csv")
    with pytest.raises(ft.BlockedTableError):
        ft.read_table("reporter_scope.csv")                          # 일반 읽기로는 여전히 막힘


def test_treatment_code_for_rcode(tables):
    assert ft.treatment_code_for_rcode("R-3-2") == "2003"            # 위탁처리 + 상위 R-3
    assert ft.treatment_code_for_rcode("R-9-1") is None
