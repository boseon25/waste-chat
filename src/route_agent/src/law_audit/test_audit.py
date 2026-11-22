"""법령 누락 점검 테스트 — 실제 조문 파일과 같은 형식의 작은 가짜 조문으로.

실행: $PY -m pytest -v law_audit/test_audit.py
실제 실행 (사용자 컴퓨터): python -m law_audit --chunks <data/law/chunks> --tables <data/tables/active> --out <폴더>
"""
import json

import pytest

from law_audit.__main__ import write_report
from law_audit.audit import _top, audit, extract_refs, load_chunks, used_keys, Chunk


def row(law, key, title, text, type_="article", parent=None):
    d = {"type": type_, "key": key, "title": title, "text": text, "law_id": law, "effective": "20260918",
         "chunk_key": f"{law}_{key}_20260918", "hash": "x"}
    if parent:
        d["parent"] = parent
    return d


FAKE = {
    "WMA": [
        row("WMA", "13", "폐기물의 처리 기준 등", "제13조(폐기물의 처리 기준 등) 누구든지 폐기물을 처리하려는 자는 대통령령으로 정하는 기준과 방법을 따라야 한다."),
        row("WMA", "13의2", "폐기물의 재활용 원칙 및 준수사항", "제13조의2 … 제13조에 따른 … 재활용"),
        row("WMA", "13의2/항2", "폐기물의 재활용 원칙 및 준수사항", "② 다음 각 호의 폐기물은 재활용을 해서는 아니 된다.", "article_part", "13의2"),
        row("WMA", "17", "사업장폐기물배출자의 의무 등", "제17조 … 제18조제1항에 따라 위탁 … 법 제25조"),
        row("WMA", "17/항1/호3", "사업장폐기물배출자의 의무 등", "3. 처리를 위탁하는 경우 …", "article_part", "17"),
        row("WMA", "18", "사업장폐기물의 처리", "제18조 … 제25조제3항에 따른 폐기물처리업의 허가를 받은 자에게 위탁"),
        row("WMA", "18/항3", "사업장폐기물의 처리", "③ 전자정보처리프로그램에 입력", "article_part", "18"),
        row("WMA", "25", "폐기물처리업", "제25조(폐기물처리업) 폐기물의 수집ㆍ운반, 재활용 또는 처분을 업으로 하려는 자는 …"),
        row("WMA", "65", "벌칙", "제65조(벌칙) 제13조를 위반하여 … 제25조"),
    ],
    "WMA_RULE": [
        row("WMA_RULE", "5", "폐기물의 처리에 관한 구체적 기준 및 방법", "[별표 5] … 보관 … 별표 5의3에 따른 재활용 기준", "annex"),
        row("WMA_RULE", "B5:3.", "폐기물의 처리에 관한 구체적 기준 및 방법", "3. 사업장일반폐기물의 기준 및 방법", "annex_part", "B5"),
        row("WMA_RULE", "5의3", "재활용의 세부 기준", "[별표 5의3] 재활용의 세부 기준", "annex"),
        row("WMA_RULE", "18", "사업장폐기물배출자의 신고", "제18조 법 제17조제2항에 따라 … 신고"),
        row("WMA_RULE", "14", "폐기물의 보관", "제14조 … 별표 5에 따라 보관"),
        row("WMA_RULE", "4의3", "폐기물의 종류별 재활용 유형", "[별표 4의3] …", "annex"),
        row("WMA_RULE", "5", "광역 폐기물처리시설의 설치ㆍ운영의 위탁", "제5조 … (조문 5와 별표 5가 겹치지 않아야 함)"),
    ],
}


@pytest.fixture
def chunks_dir(tmp_path):
    for law, rows in FAKE.items():
        d = tmp_path / "chunks" / law
        d.mkdir(parents=True)
        (d / "20260918.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return tmp_path / "chunks"


@pytest.fixture
def tables_dir(tmp_path):
    d = tmp_path / "tables"
    d.mkdir()
    (d / "prohibited_restricted.csv").write_text("id,근거\nPR-1,WMA:13의2/항2@20260918#aa\n", encoding="utf-8-sig")
    (d / "condition_texts.csv").write_text(
        "id,근거_청크\nCT-1,WMA:17/항1/호3@20260918#bb\nCT-2,WMA:18/항3@20260918#cc\n", encoding="utf-8-sig")
    (d / "disposal_methods_5.csv").write_text("id,근거\nD3,WMA_RULE:B5:3@20261002#dd\n", encoding="utf-8-sig")
    (d / "recycling_types_4_3.csv").write_text("id,근거\nR,WMA_RULE:B4의3@20261002#ee\n", encoding="utf-8-sig")
    (d / "gate3_rules.csv").write_text("id,근거\nG3,CEA:21/항1@20260219#ff\n", encoding="utf-8-sig")
    (d / "treatment_code_map.csv").write_text("id,근거\nT,WMA_RULE:BF5@처리구분@20261002#gg\n", encoding="utf-8-sig")
    return d


def test_top_key():
    assert _top("13의2/항2") == "13의2" and _top("B5:3") == "B5" and _top("§Ⅶ.2") == "§Ⅶ" and _top("B4의3") == "B4의3"
    assert _top("B5:3~2") == "B5"


def test_used_keys_excludes_gate3_and_handles_form_key(tables_dir):
    u = used_keys(tables_dir)
    assert ("WMA", "13의2/항2") in u and ("WMA_RULE", "BF5") in u
    assert not any(l == "CEA" for l, _ in u)


def test_extract_refs():
    c = Chunk("WMA_RULE", "18", "article", "신고", "제18조 법 제17조제2항에 따라 … 별표 5의3 … 영 제7조의3", "20261002")
    refs = extract_refs(c)
    assert ("WMA", "17") in refs and ("WMA_RULE", "B5의3") in refs and ("WMA_DEC", "7의3") in refs
    assert ("WMA_RULE", "18") not in refs and ("WMA_RULE", "17") not in refs


def test_graph_candidates(chunks_dir, tables_dir):
    res = audit(load_chunks(chunks_dir), used_keys(tables_dir))
    cands = {(c["law"], c["key"]): c for c in res["graph_candidates"]}
    assert ("WMA", "25") in cands and cands[("WMA", "25")]["depth"] == 1        # 제18조 → 제25조 (허가 업체)
    assert ("WMA_RULE", "B5의3") in cands                                      # 별표 5 → 별표 5의3 (재활용 기준)
    assert ("WMA", "13") in cands                                             # 제13조의2 → 제13조 (처리 기준)
    assert ("WMA", "65") not in cands                                         # 벌칙은 범위 밖
    assert ("WMA", "18") not in cands and ("WMA_RULE", "B5") not in cands     # 이미 쓰는 조문


def test_stage_checklist(chunks_dir, tables_dir):
    res = audit(load_chunks(chunks_dir), used_keys(tables_dir))
    st = {s["stage"]: s for s in res["stages"]}
    keep = st["보관"]
    assert any(r["key"] == "14" and r["law"] == "WMA_RULE" for r in keep["not_used"])     # 보관 조문 안 씀
    pre = st["배출 전: 배출자 신고·지정폐기물 확인"]
    assert any(r["law"] == "WMA_RULE" and r["key"] == "18" for r in pre["not_used"])
    assert any(r["law"] == "WMA" and r["key"] == "17" for r in pre["used_rows"])


def test_missing_in_chunks_reported(chunks_dir, tables_dir):
    res = audit(load_chunks(chunks_dir), used_keys(tables_dir))
    assert "WMA_RULE:BF5" in res["missing_in_chunks"]                         # 가짜 조문에 별지 없음


def test_report_files(chunks_dir, tables_dir, tmp_path):
    res = audit(load_chunks(chunks_dir), used_keys(tables_dir))
    path = write_report(res, tmp_path / "out")
    assert path.exists() and (tmp_path / "out" / "law_audit_candidates.csv").exists()
    assert "인용 그래프 누락 후보" in path.read_text(encoding="utf-8")
