"""waste-chat 서류 칸 ↔ 검토 칸 (config/review_fields/{문서}.yaml).

  m = load("contract")
  fields, origin = to_review(m, values)        # waste-chat 칸 값 → 검토 엔진 입력(중첩 dict) + 위치별 원래 칸
  origin["contract.start_date"] == "contract_period"

waste-chat 은 칸 이름을 평평하게(generator_name, receiver_permit_number …) 쓰고,
검토 엔진(rules.py)은 consignor.name, waste_items[0].waste_code 처럼 중첩 위치를 쓴다.
결과를 돌려줄 때 위치를 다시 waste-chat 칸 이름으로 바꿔야 화면에서 그 칸을 강조할 수 있다.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DIR = Path(__file__).resolve().parents[1] / "config" / "review_fields"


@lru_cache(maxsize=None)
def load(doc: str) -> dict | None:
    p = DIR / f"{doc}.yaml"
    if not re.fullmatch(r"[A-Za-z0-9_]+", doc or "") or not p.is_file():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8")) or None


def supported() -> list[str]:
    return sorted(p.stem for p in DIR.glob("*.yaml"))


def _put(doc: dict, path: str, value: Any) -> None:
    """"waste_items[0].x" 같은 위치에 값 넣기"""
    cur: Any = doc
    parts = path.split(".")
    for i, part in enumerate(parts):
        m = re.fullmatch(r"(.+?)\[(\d+)\]", part)
        last = i == len(parts) - 1
        if m:
            name, idx = m.group(1), int(m.group(2))
            lst = cur.setdefault(name, [])
            while len(lst) <= idx:
                lst.append({})
            if last:
                lst[idx] = value
            else:
                cur = lst[idx]
        elif last:
            cur[part] = value
        else:
            cur = cur.setdefault(part, {})


# ── 글자 한 칸 → 여러 칸 ─────────────────────────────────────

RX_DATE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")


def _period(text: Any) -> list[str | None]:
    """"2026-12-01 ~ 2027-11-30", "2026년 12월 1일부터 2027년 11월 30일까지" → [시작, 끝]"""
    found = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in RX_DATE.findall(str(text or ""))]
    return (found + [None, None])[:2]


def _ton_year(text: Any) -> list[float | None]:
    """"월 1.2톤" → 14.4, "연 15 t" → 15, "1,200 kg/월" → 14.4. 기간·단위를 모르면 None (추측 안 함)"""
    s = str(text or "").replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(톤|t|ton|kg|킬로)", s, re.I)
    if not m:
        return [None]
    n = float(m.group(1)) / (1000 if m.group(2).lower() in ("kg", "킬로") else 1)
    if re.search(r"월|/\s*m|month", s, re.I):
        return [round(n * 12, 3)]
    if re.search(r"연|년|/\s*y|year", s, re.I):
        return [round(n, 3)]
    return [None]


def _first(*vals: Any) -> list[Any]:
    return [next((v for v in vals if v not in (None, "") and str(v).strip()), None)]


FNS = {"period": _period, "ton_year": _ton_year, "first": _first}


def to_review(m: dict, values: dict) -> tuple[dict, dict[str, str]]:
    """waste-chat 칸 값 → (검토 입력, {검토 위치: waste-chat 칸})"""
    doc: dict = {}
    origin: dict[str, str] = {}
    for wc, path in (m.get("fields") or {}).items():
        origin[path] = wc
        v = (values or {}).get(wc)
        if v not in (None, ""):
            _put(doc, path, v)
    for d in m.get("derive") or []:
        src = d["from"] if isinstance(d["from"], list) else [d["from"]]
        outs = FNS[d["fn"]](*[(values or {}).get(k) for k in src])
        for path, v in zip(d["to"], outs):
            origin[path] = src[0]
            if v is not None:
                _put(doc, path, v)
    return doc, origin


def form_field(loc: str | None, origin: dict[str, str]) -> str | None:
    """검토 결과 위치 → waste-chat 칸. "waste_items[0].x" 와 "waste_items[].x" 모두 찾음"""
    if not loc:
        return None
    if loc in origin:
        return origin[loc]
    loose = re.sub(r"\[\d*\]", "[0]", loc)
    return origin.get(loose)


def attachments(m: dict, done: list | None) -> list[str]:
    """거래방에서 완료된 항목 key → 검토 첨부 이름 (이미 검토 이름이면 그대로)"""
    amap = m.get("attachments") or {}
    out = []
    for a in done or []:
        k = a.get("type") if isinstance(a, dict) else a
        k = amap.get(str(k), str(k))
        if k not in out:
            out.append(k)
    return out
