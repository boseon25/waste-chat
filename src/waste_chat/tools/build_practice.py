"""법령 원문에 없는 '어떻게 하나'를 답할 때 쓰는 실무 자료 묶음을 만든다. python tools/build_practice.py

출처(모두 waste_agent 파트 1에 들어 있던 자료):
  - 확인 경로 안내(guidance_catalog.py): 자료를 어디서 구하고 무엇을 준비하며 무엇을 주의하는지
  - 기관 접수 경로(contact_routes.py): 국민신문고 질의, 시험기관 의뢰 절차
  - 기관 작성 안내 PDF(official_forms/안내_*.pdf)
결과: engine/law/practice.jsonl  (law="PRACTICE", kind="practice")
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.paths import PART1 as P1  # noqa: E402
OUT = ROOT / "engine" / "law" / "practice.jsonl"
sys.path.insert(0, str(P1))

import contact_routes  # noqa: E402
import guidance_catalog  # noqa: E402
from pypdf import PdfReader  # noqa: E402

rows = []


def add(key, title, source, text, url=""):
    rows.append({"law": "PRACTICE", "law_name": source, "type": "practice", "key": key, "title": title,
                 "effective": "", "text": text.strip(), "url": url})


for topic, c in guidance_catalog.CATALOG.items():
    if topic == "publication":
        continue
    lines = [f"{c['title']} — {c['reason']}", f"확인할 것: {c['question']}"]
    if c["where"]:
        lines.append("어디서 확인하나: " + " / ".join(c["where"]))
    if c["prepare"]:
        lines.append("준비할 것: " + ", ".join(c["prepare"]))
    if c.get("alternative"):
        lines.append("자료가 없을 때: " + c["alternative"])
    if c.get("caution"):
        lines.append("주의: " + c["caution"])
    for s in c["sources"]:
        src = guidance_catalog.SOURCES[s]
        lines.append(f"공식 경로: {src['url']} ({src['checked_scope']}, 확인일 {src['checked_at']}; 한계: {src['limitation']})")
    add(f"guide:{topic}", c["title"], "파트1 확인 경로 안내", "\n".join(lines))

for kind, r in contact_routes.ROUTES.items():
    text = [f"{r['title']} — 담당: {r['provider']}", "메뉴: " + " > ".join(r["menu"]),
            "준비할 것: " + " / ".join(r["preparation"]),
            "절차: " + " → ".join(f"({i + 1}) {s}" for i, s in enumerate(r["steps"])),
            "막힐 때: " + r["fallback"], "한계: " + " ".join(r["limits"]), f"확인일 {r['verified_on']}"]
    add(f"route:{kind}", r["title"], "파트1 기관 접수 경로", "\n".join(text), r.get("source_url", ""))

for pdf in sorted((P1 / "official_forms").glob("안내_*.pdf")):
    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)
    text = re.sub(r"[ \t]+", " ", text)
    if len(re.sub(r"\W", "", text)) < 400:  # 그림 위주 매뉴얼은 글이 거의 없다
        continue
    name = pdf.stem.removeprefix("안내_").replace("_", " ")
    for i in range(0, len(text), 900):
        add(f"pdf:{pdf.stem}:{i // 900 + 1}", name, "기관 작성 안내 자료", text[i:i + 900])

# 이 앱의 서류 안내(docs/거래서류안내.md): 절차·기한·보관을 법령에 맞춰 정리한 글. 제목 단위로 나눈다.
guide = (ROOT / "docs" / "거래서류안내.md").read_text(encoding="utf-8")
for i, part in enumerate(re.split(r"\n(?=## )", guide)):
    head = part.strip().splitlines()[0].lstrip("# ").strip() if part.strip() else ""
    if len(part.strip()) > 80:
        add(f"app-guide:{i}", head, "앱 거래 서류 안내", part[:2500])

# 칸별 작성 요령(services/field_info.py): 서식 뒷면 작성방법과 현장 확인 방법을 칸 단위로 풀어 쓴 것
sys.path.insert(0, str(ROOT))
from services import catalog  # noqa: E402

for doc_id, doc in catalog.DOCS.items():
    if not doc.get("fields"):
        continue
    lines = [f"{f['label']}: {f.get('help', '')}" + (f" (예: {f['example']})" if f.get("example") else "")
             for f in doc["fields"] if f.get("help")]
    for j in range(0, len(lines), 8):
        add(f"field-help:{doc_id}:{j // 8 + 1}", doc["title"] + " 칸별 작성 요령", "앱 칸별 작성 요령", "\n".join(lines[j:j + 8]))

OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
print(f"{len(rows)}개 → {OUT.relative_to(ROOT)}")
