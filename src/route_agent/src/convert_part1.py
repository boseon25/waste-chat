"""파트 1 출력(WasteCase schema 2.0) → 파트 2 입력(schema 1.0) 변환

    python3 convert_part1.py 파트1출력.json --out part2_input.json
    python3 convert_part1.py 파트1출력.json --out part2_input.json --assume-lot-monthly --density 1.0
    python3 convert_part1.py 파트1출력.json --out part2_input.json --code ac0bea9e=51-03-01   # 분류번호 직접 지정 (시험용)

받는 파트 1 파일 (둘 다 됨)
  - db_intake.py export        → db_export.json            (cases[] + sites[])
  - phase3.py export           → case_document_review.json (case + source + document_packets)

원칙
  - 파트 1 값은 {value, status, …} 묶음. status 가 confirmed / conditional 이면 쓰고, needs_info 면 빈 값으로 둔다
    → 파트 2 가 "확인 필요"로 판정하고 파트 1 질문을 만든다.
  - 값을 지어내지 않는다. 가정이 필요한 변환(이번 lot 양을 월 배출량으로, L → kg 밀도)은
    옵션을 켰을 때만 하고, 사례의 _conversion.assumptions 에 남긴다.
  - 문서 검토값(document_packets.reviews)은 status 가 accepted_candidate 인 것만 쓰고 출처를 남긴다.
  - 파트 1 원본 칸은 바꾸지 않는다 (읽기만).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
USE = ("confirmed", "conditional")
SGIS_SIDO = {
    "11": "서울특별시", "21": "부산광역시", "22": "대구광역시", "23": "인천광역시", "24": "광주광역시",
    "25": "대전광역시", "26": "울산광역시", "29": "세종특별자치시", "31": "경기도", "32": "강원특별자치도",
    "33": "충청북도", "34": "충청남도", "35": "전북특별자치도", "36": "전라남도", "37": "경상북도",
    "38": "경상남도", "39": "제주특별자치도",
}
SIDO_WORDS = {  # 주소 첫 단어 → 정식 이름
    "서울": "서울특별시", "서울시": "서울특별시", "서울특별시": "서울특별시", "부산": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시", "인천": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시", "대전": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산광역시": "울산광역시", "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",
    "경기": "경기도", "경기도": "경기도", "강원": "강원특별자치도", "강원도": "강원특별자치도", "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도", "충남": "충청남도", "충청남도": "충청남도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도", "전북특별자치도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도", "경북": "경상북도", "경상북도": "경상북도",
    "경남": "경상남도", "경상남도": "경상남도", "제주": "제주특별자치도", "제주도": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}
PER_MONTH = {"day": 30.0, "week": 30.0 / 7, "month": 1.0, "year": 1 / 12}
MASS_TON = {"t": 1.0, "kg": 0.001, "g": 1e-6}
VOLUME_L = {"L": 1.0, "m3": 1000.0}


# ── 파트 1 값 꺼내기 ──────────────────────────────────────────────────
def fv(x):
    """Fact → 값 (needs_info 거나 없으면 None)"""
    if isinstance(x, dict) and "value" in x:
        return x["value"] if x.get("status") in USE and x["value"] not in (None, "") else None
    return x


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ── 지역 ──────────────────────────────────────────────────────────────
def load_sigungu() -> list[tuple[str, str]]:
    """[(시도 정식 이름, 시군구 이름)] — reference 의 시군구 중심점 표"""
    ref = Path(os.environ.get("WASTE_REFERENCE_DIR") or HERE.parents[2] / "data" / "reference")
    files = sorted(ref.glob("sigungu_centers*.csv")) if ref.exists() else []
    out = []
    for f in files[-1:]:
        for r in csv.DictReader(f.open(encoding="utf-8-sig")):
            sido = SGIS_SIDO.get(str(r.get("sig_cd", ""))[:2])
            if sido and r.get("sig_nm"):
                out.append((sido, r["sig_nm"]))
    return out


def parse_region(address: str | None, table: list[tuple[str, str]]) -> tuple[dict, list[str]]:
    notes = []
    if not address:
        return {"sido": None, "sigungu": None, "address": None}, ["현장 주소 없음"]
    toks = re.sub(r"[(),]", " ", address).split()
    sido = SIDO_WORDS.get(toks[0]) if toks else None
    rest = toks[1:] if sido else toks
    sigungu = None
    for i, t in enumerate(rest[:3]):
        if re.search(r"(시|군|구)$", t):
            sigungu = t
            if t.endswith("시") and i + 1 < len(rest) and rest[i + 1].endswith("구") and \
                    any(n == f"{t} {rest[i + 1]}" for _, n in table):
                sigungu = f"{t} {rest[i + 1]}"
            break
    if sigungu and not sido and table:
        hits = {s for s, n in table if n == sigungu or n.split(" ")[0] == sigungu}
        if len(hits) == 1:
            sido = hits.pop()
            notes.append(f"시도를 시군구 이름으로 찾음 ({sigungu} → {sido})")
        elif len(hits) > 1:
            notes.append(f"시군구 '{sigungu}'가 여러 시도에 있어 시도를 정하지 못함 → 주소에 시도를 넣어 주세요")
    if not sigungu:
        notes.append("주소에서 시군구를 찾지 못함")
    return {"sido": sido, "sigungu": sigungu, "address": address}, notes


# ── 칸별 변환 ─────────────────────────────────────────────────────────
def material_form(text: str | None) -> str | None:
    t = text or ""
    if not t:
        return None
    if re.search(r"액체|액상|용액|세척액|폐액|물기 많", t):
        return "액상"
    if re.search(r"슬러지|오니|진흙|반고상", t):
        return "슬러지"
    if re.search(r"분말|가루|분진|파우더", t):
        return "분말"
    if re.search(r"조각|자투리|절단|파쇄|분쇄|부스러기|칩", t):
        return "파쇄"
    if re.search(r"원형|통째|그대로|완제품|용기 형태", t):
        return "원형보존"
    return None


def monthly_ton(c: dict, opts, notes: list, assumptions: list) -> list[float]:
    g = c.get("generation") or {}
    amt, unit, period = num(fv(g.get("amount"))), fv(g.get("unit")), fv(g.get("period"))
    src = "generation"
    if amt is None or not unit or not period:
        q = c.get("quantity") or {}
        if not opts.assume_lot_monthly:
            notes.append("월 배출량 없음 (generation 미기재) → 파트 1에 발생 주기·양 요청. "
                         "이번 lot 양을 월 배출량으로 보려면 --assume-lot-monthly")
            return []
        amt, unit, period, src = num(q.get("value") if q.get("status") in USE else None), q.get("unit"), "month", "quantity(이번 lot)"
        if amt is None:
            notes.append("이번 lot 양도 없음")
            return []
        assumptions.append(f"이번 lot 양({amt:g}{unit})을 월 배출량으로 가정")
    if unit in MASS_TON:
        ton = amt * MASS_TON[unit]
    elif unit in VOLUME_L:
        if opts.density is None:
            notes.append(f"부피 단위({unit})라 톤으로 바꾸려면 밀도 필요 → --density kg/L")
            return []
        ton = amt * VOLUME_L[unit] * opts.density / 1000
        assumptions.append(f"밀도 {opts.density} kg/L 로 {unit} → 톤 환산")
    else:
        notes.append(f"단위 '{unit}'는 무게로 바꿀 수 없음")
        return []
    per = PER_MONTH.get(period)
    if per is None:
        notes.append(f"주기 '{period}'를 알 수 없음")
        return []
    if src == "generation" and period != "month":
        assumptions.append(f"{period} 단위 발생량을 월로 환산 (×{per:.3g})")
    return [round(ton * per, 4)]


def reviewed_values(packets: list[dict], case_id: str) -> dict[str, dict]:
    """문서 검토값 {field_id: review} — accepted_candidate 만, 같은 칸은 마지막 것"""
    out = {}
    for p in packets or []:
        if p.get("case_id") not in (None, case_id) or p.get("stale"):
            continue
        for r in p.get("reviews") or []:
            if r.get("status") == "accepted_candidate":
                out[r["field_id"]] = {**r, "packet_kind": p.get("kind")}
    return out


def analysis(c: dict, rv: dict[str, dict], notes: list) -> dict:
    a: dict = {}
    items = {}
    for r in c.get("analysis_results") or []:
        if r.get("applicability") == "mismatched":
            continue
        items[(r.get("substance") or "").lower()] = {"value": r.get("value"), "unit": r.get("unit"),
                                                      "basis": r.get("sample_basis"), "src": "analysis_results"}
    for fid, r in rv.items():
        if fid.startswith("analysis."):
            items[fid.split(".", 1)[1].lower()] = {"value": r.get("normalized_value"), "unit": r.get("unit"),
                                                    "basis": r.get("basis"), "src": f"문서 검토 {r.get('packet_kind')}"}
    hv = next((items[k] for k in ("heating_value_low", "저위발열량", "heating_value", "발열량") if k in items), None)
    if hv and num(hv["value"]) is not None:
        v, u = num(hv["value"]), (hv["unit"] or "").replace(" ", "")
        kcal = v * 238.846 if u.lower() in ("mj/kg",) else v * 0.238846 if u.lower() in ("kj/kg",) else v if "kcal" in u.lower() else None
        if kcal is not None:
            a["heating_value_kcal"] = round(kcal)
            a["heating_value_unit"] = "kcal/kg"
            if u.lower() != "kcal/kg":
                notes.append(f"발열량 {v:g} {u} → {round(kcal)} kcal/kg")
            if "low" not in str(hv.get("src")) and "heating_value_low" not in items:
                notes.append("발열량이 저위·고위 구분 없이 옴 → 저위발열량으로 보고 비교 (확인 필요)")
        else:
            notes.append(f"발열량 단위 '{hv['unit']}'를 kcal/kg 로 바꿀 수 없음")
    for key, out in (("moisture", "moisture_pct"), ("chlorine", "chlorine_pct")):
        if key in items and num(items[key]["value"]) is not None:
            a[out] = num(items[key]["value"])
    for k in ("leaching", "용출"):
        if k in items:
            a["leaching_ok"] = None
            notes.append("용출 결과가 있으나 기준 대비 판정은 파트 2 사전검증에서 (원문 확인)")
    return a


def convert_case(c: dict, address: str | None, packets: list, table, opts) -> dict:
    notes, assumptions = [], []
    rv = reviewed_values(packets, c["case_id"])
    cls = c.get("classification") or {}
    selected = cls.get("selected_code")
    reported = fv(cls.get("reported_code")) or (rv.get("classification.reported_code") or {}).get("normalized_value")
    cand = [x.get("code") for x in cls.get("candidates") or [] if x.get("status") in ("supported", "candidate")]
    forced = next((v for k, v in (opts.code or {}).items() if c["case_id"].startswith(k) or k == "*"), None)
    if forced:
        code, cstatus = forced, "user_override"
        assumptions.append(f"분류번호 {forced} 를 사용자가 지정 (파트 1 값 {selected or reported or '없음'})")
    elif selected:
        code, cstatus = selected, "part1_selected"
    elif reported:
        code, cstatus = reported, "part1_reported_unreviewed"
        notes.append(f"분류번호 확정 전: 신고·회신값 {reported} 로 판정 (파트 1 검토 후 다시 실행)")
    else:
        code, cstatus = None, "missing"
        notes.append("분류번호 없음 → 파트 1 분류 검토 필요" + (f" (후보 {cand})" if cand else ""))
    if code and not re.fullmatch(r"\d{2}-\d{2}-\d{2}", str(code)):
        notes.append(f"분류번호 형식이 NN-NN-NN 이 아님: {code}")

    region, rnotes = parse_region(address, table)
    notes += rnotes
    phys = fv(c.get("physical_state"))
    form = material_form(phys)
    if phys and not form:
        notes.append(f"형태를 정하지 못함: '{phys}' → 파트 2 질문으로")
    mix = fv(c.get("mixture_type"))
    sep = fv(c.get("separability"))
    vis = fv(c.get("visible_contamination"))
    haz = fv(c.get("hazard_declaration"))
    comps = []
    for comp in c.get("components") or []:
        comps.append({"material": fv(comp.get("name")), "share_pct": num(fv(comp.get("proportion_percent"))),
                      "separable": {True: "예", False: "아니오"}.get(sep), "waste_code": None,
                      "status": "confirmed" if (comp.get("name") or {}).get("status") == "confirmed" else "conditional"})
    mt = monthly_ton(c, opts, notes, assumptions)
    q = c.get("quantity") or {}
    missing = sorted(set(c.get("missing_fields") or []))
    out = {
        "schema_version": "1.0",
        "case_id": c["case_id"], "source_part1_case_id": c["case_id"],
        "case_version": c.get("case_version") or 0, "synthetic": bool(c.get("synthetic")),
        "name": fv(c.get("name")), "process_description": fv(c.get("process_description")),
        "classification": {"reported_code": reported, "selected_code": code, "status": cstatus,
                           "candidates": cand, "review_required": cls.get("review_required")},
        "components": comps,
        "waste_code_candidates": cand,
        "hazardous_suspect": haz,
        "designated_waste_status": {True: "designated", False: "non_designated"}.get(haz),
        "quantity": {"monthly_ton": mt, "monthly_periods": [], "unit": "ton",
                     "status": "confirmed" if mt and not assumptions else ("assumed" if mt else "needs_info"),
                     "current_lot": {"value": num(q.get("value")) if q.get("status") in USE else None,
                                     "unit": q.get("unit")}},
        "region": region,
        "item_specific": {"ic_tray": None, "nonferrous_material": None, "grain_processor_registered": None},
        "foreign_matter": {"items": [], "separable": None},
        "mixed_with_other": {"single": False, "mixed": True, "composite": True, "multiple_unspecified": True}.get(mix),
        "contamination": {"level": {"not_visible": "깨끗", "partial": "일부 오염", "widespread": "오염"}.get(vis),
                          "substances": [fv(s) if not isinstance(s, dict) else (fv(s.get("name")) or s.get("substance"))
                                         for s in c.get("substances") or []]},
        "material_form": form,
        "existing_declaration": None,
        "combustible": None,
        "analysis": analysis(c, rv, notes),
        "storage_state": None,
        "missing": missing,
        "not_applicable": [],
        "_conversion": {"from": "part1 WasteCase 2.0", "notes": notes, "assumptions": assumptions,
                        "physical_state_text": phys, "document_reviews_used": sorted(k for k in rv)},
    }
    return out


def load_input(data: dict) -> tuple[list[tuple[dict, str | None, list]], dict]:
    """→ [(case, 주소, document_packets)], 공통 정보"""
    if "cases" in data:                                  # db_export.json
        sites = {s["site_id"]: fv(s.get("address")) for s in data.get("sites") or []}
        return [(c, sites.get(c.get("site_id")), []) for c in data["cases"]], data
    if "case" in data:                                   # case_document_review.json
        addr = (data.get("source") or {}).get("site_address")
        return [(data["case"], addr, data.get("document_packets") or [])], data
    if "case_id" in data:                                # WasteCase 하나
        return [(data, None, [])], data
    raise SystemExit("파트 1 출력 형식이 아닙니다 (cases[] / case / case_id 없음)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="파트 1 출력(2.0) → 파트 2 입력(1.0)")
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assume-lot-monthly", action="store_true", help="월 배출량이 없으면 이번 lot 양을 월 배출량으로 (가정 기록)")
    ap.add_argument("--density", type=float, default=None, help="부피(L, m3) → 무게 환산 밀도 kg/L")
    ap.add_argument("--code", action="append", default=[], metavar="사례ID앞부분=분류번호",
                    help="분류번호 직접 지정 (시험용, 여러 번 가능, '*=코드'는 전체)")
    opts = ap.parse_args(argv)
    opts.code = dict(x.split("=", 1) for x in opts.code if "=" in x)

    data = json.loads(Path(opts.input).read_text(encoding="utf-8"))
    items, meta = load_input(data)
    table = load_sigungu()
    cases = [convert_case(c, addr, packets, table, opts) for c, addr, packets in items]
    bundle = {"schema_version": "1.0", "export_kind": "converted_from_part1",
              "source_export_kind": meta.get("export_kind"), "source_schema_version": meta.get("schema_version"),
              "published": bool(meta.get("published")), "synthetic": any(c["synthetic"] for c in cases),
              "cases": cases}
    Path(opts.out).write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"파트 1 {meta.get('export_kind')} (schema {meta.get('schema_version')}, published={meta.get('published')}) → {opts.out}")
    for c in cases:
        cv = c["_conversion"]
        print(f"■ {c['case_id'][:12]}… {c['name']}  분류 {c['classification']['selected_code']} ({c['classification']['status']}), "
              f"월 {c['quantity']['monthly_ton'] or '-'}톤, {c['region']['sido']} {c['region']['sigungu']}, 형태 {c['material_form']}")
        for n in cv["assumptions"]:
            print(f"   가정: {n}")
        for n in cv["notes"]:
            print(f"   · {n}")
    if not meta.get("published"):
        print("※ 파트 1 초안(published=false) — 결과는 검토용")
    return 0


if __name__ == "__main__":
    sys.exit(main())
