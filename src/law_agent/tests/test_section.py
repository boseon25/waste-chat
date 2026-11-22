"""긴 별표의 호·목 발췌 (별표 5) 와 R코드 호 발췌 (별표 4의2) — 2026.11.17 데모에서 F-1·F-2·D-2 근거가 비었던 문제."""
from __future__ import annotations

from fake_law import ch

from law_agent.agents.evidence import EvidenceConfig, build_pack, section_kinds
from law_agent.core.lawstore import LawStore
from law_agent.state import Candidate, MinLaw, Question, Verdict, initial_state
from law_agent.tools import law_tools

PAD = "\n".join(f"   (채움 문장 {i}) 이 줄은 길이를 늘리기 위한 것이다." for i in range(60))
SEC3 = "\n".join([
    "3. 사업장일반폐기물의 기준 및 방법",
    "가. 공통사항", "1) 사업장일반폐기물은 종류별로 분리한다.", PAD,
    "나. 수집·운반의 경우", "1) 흩날리지 않게 운반한다.", PAD,
    "다. 보관의 경우", "1) 보관 기간은 90일이다.", "다. 이 경우 본문 속 가짜 목 머리", PAD,
    "라. 처리의 경우",
    "1) 오니류는 수분함량 85퍼센트 이하로 탈수하여 매립한다.", PAD,
    "2) 폐합성수지류는 소각하여야 한다. 다만, 소각이 곤란한 경우 최대 지름 15센티미터 이하로 파쇄하여 매립할 수 있다.",
    "3) 폐석재는 매립한다.", PAD,
])
SEC4 = "4. 지정폐기물(의료폐기물은 제외한다)의 기준 및 방법\n가. 수집·운반의 경우\n1) 전용 용기\n다. 처리의 경우\n1) 폐유기용제는 고온소각한다."
B42 = ["1. 원형 그대로 재사용하는 유형(R-1, R-2)\n가. R-1: 원형 그대로 재사용\n나. R-2: 수리하여 재사용",
       "2. 고상의 자원을 회수하는 유형\n가. R-3: 원료를 제조\n1) R-3-2: 합성수지 원료 제조\n나. R-4: 제품을 제조",
       "5. 에너지 회수 유형(R-8, R-9)\n가. R-9: 에너지를 회수할 수 있는 상태로 만드는 유형" + PAD * 2]


def store():
    s = LawStore()
    for c in [ch("WMA_RULE", "B5", "폐기물의 처리에 관한 구체적 기준 및 방법(제14조 관련)",
                 "1. 생활폐기물의 기준 및 방법\n" + PAD * 3 + "\n" + SEC3 + "\n" + SEC4, typ="annex"),
              ch("WMA_RULE", "B5:1~2", "별표 5", "1. 생활폐기물의 기준 및 방법\n가. 처리의 경우\n1) 소각한다.", typ="annex_part"),
              ch("WMA_RULE", "B5:3", "별표 5", SEC3, typ="annex_part"),
              ch("WMA_RULE", "B5:4", "별표 5", SEC4, typ="annex_part"),
              ch("WMA_RULE", "B4의2", "폐기물의 재활용 유형별 세부분류(제4조의2제2항 관련)", "\n".join(B42), typ="annex"),
              *[ch("WMA_RULE", f"B4의2:{i + 1}", "별표 4의2", t, typ="annex_part") for i, t in enumerate(B42)]]:
        s.chunks[(c.law, c.key)] = c
    return s


SPEC = MinLaw("WMA_RULE", "B5", "폐기물의 처리에 관한 구체적 기준 및 방법",
              section={"general": "사업장일반폐기물의 기준", "designated": "지정폐기물(의료폐기물은 제외한다)의 기준"},
              subsection="처리의 경우")


def test_section_excerpt_picks_status_section_and_item():
    s = store()
    r = law_tools.section_excerpt("WMA_RULE", "B5", "사업장일반폐기물의 기준", "처리의 경우", ["합성수지"], 800, store=s)
    assert r["part"].key == "B5:3" and "라. 처리의 경우" in r["text"]
    assert "2) 폐합성수지류는 소각하여야 한다" in r["text"] and "보관 기간" not in r["text"]
    assert "…(중략)…" in r["text"] and "관련 부분" in r["note"]
    big = law_tools.section_excerpt("WMA_RULE", "B5", "사업장일반폐기물의 기준", "처리의 경우", [], 100000, store=s)
    assert "가짜 목 머리" not in big["text"] and "3) 폐석재" in big["text"]      # 다음 목은 '마.' 만 끝으로 봄
    d = law_tools.section_excerpt("WMA_RULE", "B5", "지정폐기물(의료폐기물은 제외한다)의 기준", "처리의 경우", [], 5000, store=s)
    assert d["part"].key == "B5:4" and "고온소각" in d["text"]


def test_build_pack_uses_section_spec():
    q = Question(id="F-1", stage="F", text="처분 방법", answer_type="list", min_laws=[SPEC])
    c = Candidate("WMA_RULE", "B5", "폐기물의 처리에 관한 구체적 기준 및 방법", 1.0, "min_laws")
    cfg = EvidenceConfig(max_chars=30000, narrow_max_chars=6000)
    pack, summary = build_pack(q, [c], ["51-03-01"], store(), cfg, [], wide=False, kinds=["general"], terms=["합성수지"])
    assert len(pack.items) == 1 and pack.items[0].key == "B5:3" and "폐합성수지류는 소각" in pack.items[0].text
    assert "호·목 발췌" in pack.items[0].problems[0] and not pack.truncated
    both, _ = build_pack(q, [c], ["51-03-01"], store(), cfg, [], wide=False, kinds=["general", "designated"])
    assert [i.key for i in both.items] == ["B5:3", "B5:4"]


def test_rcode_parts_for_annex_4_2():
    q = Question(id="D-2", stage="D", text="형태", answer_type="list",
                 min_laws=[MinLaw("WMA_RULE", "B4의2", "폐기물의 재활용 유형별 세부분류")])
    c = Candidate("WMA_RULE", "B4의2", "폐기물의 재활용 유형별 세부분류", 1.0, "min_laws")
    cfg = EvidenceConfig(max_chars=30000, narrow_max_chars=6000)
    pack, _ = build_pack(q, [c], ["51-03-01", "R-1", "R-3-2"], store(), cfg, [], wide=False)
    assert [i.key for i in pack.items] == ["B4의2:1", "B4의2:2"]


def test_section_kinds():
    st = initial_state({"case_id": "X"})
    st["facts"] = {"values": {"waste_status": "general", "waste_code": "51-03-01"}, "missing": []}
    assert section_kinds(st) == ["general"]
    st["facts"]["values"]["waste_status"] = None
    assert section_kinds(st) == ["general", "designated"]
    st["verdicts"] = {"A-2": Verdict(question_id="A-2", status="agreed", answer="yes")}
    assert section_kinds(st) == ["designated"]


BIG = "\n".join([
    "3. 사업장일반폐기물의 기준 및 방법", "라. 처리의 경우",
    "1) 재활용하지 아니하는 소각 가능한 사업장일반폐기물은 1일 평균 100킬로그램 이상이면 소각하여야 한다.",
    "2) 종류별 처리 기준은 다음과 같다.",
    "가) 오니류는 탈수하여 매립한다.", PAD,
    "나) 폐합성수지류는 소각하여야 한다. 다만 소각이 곤란하면 파쇄하여 매립한다.", PAD,
    "다) 폐석재는 매립한다.", PAD,
])


def test_long_item_split_into_subitems():
    """긴 항목(2))은 가) 나) 로 나눠 관련 있는 것만 — 데모 1 F-1 근거가 155자였던 문제."""
    s = store()
    s.chunks[("WMA_RULE", "B5:3")] = ch("WMA_RULE", "B5:3", "별표 5", BIG, typ="annex_part")
    r = law_tools.section_excerpt("WMA_RULE", "B5", "사업장일반폐기물의 기준", "처리의 경우", ["합성수지"], 2000,
                                  store=s, weak=["소각", "매립"])
    t = r["text"]
    assert "나) 폐합성수지류는 소각" in t and "2) 종류별 처리 기준" in t        # 잘린 항목의 머리 줄도 같이
    assert "1) 재활용하지 아니하는 소각 가능한" in t                            # 약한 신호(소각)로 고름
    assert len(t) <= 2000 + 200


def test_row_blocks_keeps_wrapped_lines():
    """표 한 행이 여러 줄이고 이어진 줄의 첫 칸이 비어 있으면 같은 행 (별표 4의3 51-03-01: 'R-4-4,' 뒤 R-8·R-9·R-10)."""
    text = "\n".join([
        "│51-03-01│폐합성수지류(폐염화비닐수│R-1, R-2, R-3-2,│해당 없음│",
        "│        │지류는 제외한다)        │R-3-3, R-3-4, R-4-4,│        │",
        "│        │                        │R-8-1, R-9-1, R-10-1│        │",
        "├────────┼────────────────────────┼────────────────────┼────────┤",
        "│51-03-02│폐합성고무류            │R-4-1               │해당 없음│"])
    rows = law_tools.row_blocks(text, "51-03-01")
    assert len(rows) == 1 and "R-9-1" in rows[0] and "R-10-1" in rows[0] and "51-03-02" not in rows[0]


def test_circular_missing_facts_dropped():
    from law_agent.agents.judge import split_gaps
    facts, gaps = split_gaps(["재활용 예정 유형", "재활용환경성평가 여부", "PCB 농도"], [])
    assert facts == ["재활용환경성평가 여부", "PCB 농도"] and gaps == []
