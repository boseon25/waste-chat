"""파트 1의 Word 기초 입력서(wastecase_intake_050.docx)를 읽어 법령 판단 사실로 바꾼다.

읽기는 파트 1의 intake_parser(콘텐츠 컨트롤 태그 기준, 임의 DOCX 거부)를 그대로 쓴다.
입력서의 출처 칸(원료 투입대장 몇 행 등)은 근거로 함께 돌려준다. 값을 추측해 채우지 않는다.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from .paths import PART1  # noqa: E402
TEMPLATE = PART1 / "fixtures" / "wastecase_intake_050.docx"
if str(PART1) not in sys.path:
    sys.path.append(str(PART1))

import intake_parser  # noqa: E402

from . import tables  # noqa: E402

MIX = ["단일", "혼합", "모름"]
CONTAM = ["깨끗", "오염", "오염"]
HAZARD = ["예", "아니오", "모름"]


def _ton(text):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|킬로그램|t|톤|ton|g)\b", (text or "").replace(",", ""), re.I)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return n / 1000 if unit in ("kg", "킬로그램") else n / 1e6 if unit == "g" else n


def _site(site, common):
    """개별 배출 현장이 '사출동 1층'처럼 건물 안 위치만 적혀 있으면 공통 주소를 앞에 붙인다(거리·관할 계산용)."""
    if not site:
        return common
    region = re.search(r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|충청|전북|전남|전라|경북|경남|경상|제주|[가-힣]+[시군구])\b", site)
    return site if region or not common else f"{common} {site}"


def read(blob):
    parsed = intake_parser.parse_document(blob)
    controls = parsed["controls"]
    tags = {c["tag"]: c for c in controls}

    def val(tag):
        c = tags.get(tag)
        return c["raw_value"].strip() if c and c["is_real"] and not c["is_checkbox"] else ""

    def pick(prefix, names):
        for i, name in enumerate(names):
            c = tags.get(f"{prefix}_{i}")
            if c and c.get("checked") == 1:
                return name
        return ""

    company = {k: val(k) for k in ("company_name", "business_number", "contact_name", "contact_phone", "site_address")}
    items = []
    n = 1
    while f"waste_{n}_name" in tags:
        p = f"waste_{n}"
        name = val(f"{p}_name")
        if name:
            comps, sources = [], []
            for j in range(1, 6):
                cname, pct = val(f"{p}_record_component{j}_name"), val(f"{p}_record_component{j}_percent")
                if cname:
                    comps.append(f"{cname} {pct}%" if pct else cname)
                    if val(f"{p}_record_component{j}_source"):
                        sources.append(f"성분 {cname}: {val(f'{p}_record_component{j}_source')}")
            months = []
            for j in range(1, 13):
                q = _ton(val(f"{p}_record_month{j}_quantity"))
                if q is not None:
                    months.append((val(f"{p}_record_month{j}_period"), q, val(f"{p}_record_month{j}_source")))
            facts = {
                "name": name, "process": val(f"{p}_process"), "form": val(f"{p}_appearance"),
                "components": ", ".join(comps) or val(f"{p}_mixture_details"),
                "contamination": pick(f"{p}_contamination", CONTAM),
                "contamination_detail": val(f"{p}_contamination_details") or val(f"{p}_contamination_note"),
                "hazard": pick(f"{p}_hazard", HAZARD),
                "address": _site(val(f"{p}_site"), company["site_address"]),
            }
            if months:
                facts["monthly_ton"] = f"{round(sum(m[1] for m in months) / len(months), 2):g}"
                sources.append("월 발생량: " + ", ".join(f"{m[0]} {m[1]:g}톤({m[2]})" for m in months))
            for key, label in (("record_materials", "원료"), ("record_chemicals", "약품"), ("record_label", "라벨"),
                               ("record_feed_ratio", "투입 비율")):
                v = val(f"{p}_{key}")
                if v:
                    src = val(f"{p}_{key}_source")
                    sources.append(f"{label}: {v}" + (f" ({src})" if src else ""))
            for key, label in (("quantity", "이번 처리량"), ("quantity_evidence", "발생량 근거"),
                               ("hazard_details", "유해성 메모"), ("mixture_details", "섞임 설명"), ("notes", "메모")):
                v = val(f"{p}_{key}")
                if v and v not in facts.values():
                    sources.append(f"{label}: {v}")
            code_note = ""
            code = val(f"{p}_code")  # 입력서 판본에 따라 있는 '분류번호' 칸. 별표 4 말단 번호일 때만 쓴다
            if code:
                good, msg = tables.check_code(code)
                if good:
                    facts["code"] = code
                code_note = f"입력서에 적힌 분류번호 {code}: {msg}"
            mix = pick(f"{p}_mixture", MIX)
            if mix in ("단일", "혼합"):
                facts["mixture"] = mix
            items.append({"index": n, "facts": {k: v for k, v in facts.items() if v}, "sources": sources,
                          "mixture": pick(f"{p}_mixture", MIX), "history": val(f"{p}_record_history"),
                          "code_note": code_note})
        n += 1
    if not items:
        raise ValueError("입력서에 작성된 폐기물이 없습니다.")
    return {"company": {k: v for k, v in company.items() if v}, "items": items}
