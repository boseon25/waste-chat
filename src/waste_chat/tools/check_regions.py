"""서식 칸 영역 점검: 칸마다 클릭 영역이 다른 칸과 겹치지 않는지, 값을 채운 뒤에도 그런지 본다. python tools/check_regions.py"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import catalog, pdf_writer  # noqa: E402

SAMPLE = {"waste_classification": "51-03-01", "designated_waste_code": "51-03-01", "waste_state_code": "1"}


def sample(f):
    k = f["key"]
    if f.get("choices"):
        return f["choices"][0]
    if k in SAMPLE:
        return SAMPLE[k]
    if k.endswith("_method") or k == "treatment_method":
        return "재활용 중간가공 2010"
    if "date" in k or "birthdate" in k:
        return "2026-11-22"
    if k.endswith(("_tons", "_amount", "quantity_value", "_count", "_size")):
        return "12.5"
    return "가나다라마바사 아자차카 123"  # 보통 길이의 글


def overlaps(regions, tol=0.6):
    """다른 칸끼리 겹치는 면적이 있는 쌍(같은 칸의 여러 조각은 제외). tol: 맞닿는 선(pt)은 허용."""
    out = []
    for a, b in itertools.combinations(regions, 2):
        if a["key"] == b["key"] or a["page"] != b["page"]:
            continue
        w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
        h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
        if w * 595 > tol and h * 842 > tol:
            out.append((a["key"], b["key"], round(w * 595, 1), round(h * 842, 1)))
    return out


problems = 0
for doc_id, doc in catalog.DOCS.items():
    if not doc.get("fields"):
        continue
    ok_values = {}
    for f in doc["fields"]:
        v = sample(f)
        try:
            pdf_writer.render_document(doc, {f["key"]: v})
            ok_values[f["key"]] = v
        except ValueError:
            pass
    long_values = {}
    for f in doc["fields"]:  # 칸에 들어갈 수 있는 가장 긴 글(서식이 거절하면 짧게) — 글이 길어져도 옆 칸을 침범하지 않는지
        if f["key"] not in ok_values or f.get("choices") or f["key"] in SAMPLE:
            continue
        for n in (6, 3, 2):
            v = (ok_values[f["key"]] + " ") * n
            try:
                pdf_writer.render_document(doc, {f["key"]: v.strip()})
                long_values[f["key"]] = v.strip()
                break
            except ValueError:
                pass
    for name, values in (("빈 서식", {}), ("모두 채움", ok_values), ("긴 값", {**ok_values, **long_values})):
        try:
            _, regions = pdf_writer.render_with_regions(doc, values)
        except ValueError as e:
            print(f"  {doc_id} [{name}] 그리기 실패: {e}")
            problems += 1
            continue
        keys = {f["key"] for f in doc["fields"]}
        missing = keys - {r["key"] for r in regions}
        bad = overlaps(regions)
        if bad or missing:
            problems += 1
            print(f"✗ {doc_id} [{name}] 칸 {len(keys)}개 · 겹침 {len(bad)} · 영역 없음 {sorted(missing)}")
            for x in bad[:12]:
                print("     ", x)
    print(f"  {doc_id}: 확인")
print("문제 없음" if not problems else f"문제 {problems}건")
