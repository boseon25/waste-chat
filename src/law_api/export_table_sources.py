"""export_table_sources.py — 판정표와 그 표를 만든 법령 원문을 표별 폴더로 묶어 내보내기

    python3 export_table_sources.py                     # active 표 전부 → data/table_sources/{날짜}/
    python3 export_table_sources.py plan_check_targets  # 표 하나
    python3 export_table_sources.py --tables 폴더 --out 폴더
    python3 export_table_sources.py --list              # 표 ↔ 원문 대응만 보기

결과 (표마다 폴더 하나)
    data/table_sources/{날짜}/
    ├── README.md                       전체 목록: 표, 만드는 방식, 원문, 행 수, confirmed 수, 근거 키 점검
    ├── plan_check_targets/
    │   ├── plan_check_targets.csv      표 (active 에서 복사)
    │   ├── sources.md                  이 표를 만든 법령 원문 (사람이 읽는 용)
    │   ├── sources.jsonl               같은 원문 조각 (chunk_v2 그대로)
    │   ├── config/plan_check_items.csv 설계값 표 (있으면)
    │   └── manifest.json               대응 정보, 원문 시행일·해시, 표 근거 키 점검 결과
    └── _text_only/                     표로 만들지 않고 원문만 쓰는 조문 (related_laws)

- 원본(active, chunk_v2)은 복사만 하고 옮기거나 바꾸지 않는다.
- 근거 키 점검: 표 안의 근거 키(WMA_RULE:18의2/항1/호2@20261112#…)가 이 표의 원문 목록에 들어 있는지,
  지금 원문의 시행일·해시와 같은지 센다. 목록 밖 키는 SOURCES 를 고칠 후보.
- 인증키를 쓰지 않는다 (API 를 부르지 않고 저장된 원문만 읽음).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── 표 ↔ 원문 대응 ────────────────────────────────────────────────────
# parts 형식은 targets.py 와 같다:
#   "18의2" 조문 / "17/항1/호3" 항·호 / "B4의3" 별표 / "B5:3" 별표 제3호 / "BF5" 별지 제5호서식
#   "B*제목" 제목을 포함한 별표 / "§Ⅵ" 지침 장·절 / "*" 본문 전체
SOURCES: dict[str, dict] = {
    # 코드 파서 (build_tables.py)
    "waste_codes_4": dict(method="code", builder="build_tables.py build",
                          parts={"WMA_RULE": ["B4"]}),
    "hazardous_codes": dict(method="code", builder="build_tables.py build",
                            parts={"WMA_RULE": ["B4"]}),
    "rcode_detail_4_2": dict(method="code", builder="build_tables.py build",
                             parts={"WMA_RULE": ["B4의2"]}),
    "recycling_types_4_3": dict(method="code", builder="build_tables.py build",
                                parts={"WMA_RULE": ["B4의3"]}),
    "not_in_4_3": dict(method="code", builder="build_tables.py build",
                       parts={"WMA_RULE": ["B4", "B4의3"]}),
    "treatment_code_map": dict(method="code", builder="build_tables.py build",
                               parts={"WMA_RULE": ["BF5"]}, config=["treatment_design.csv"]),
    "designated_resources": dict(method="code", builder="build_tables.py build",
                                 parts={"CEA_DESIG": ["B1", "B2"]}),
    "prohibited_restricted": dict(method="code", builder="build_tables.py build",
                                  parts={"WMA": ["13의2/항2"], "WMA_RULE": ["14의3/항3", "B4의3"],
                                         "WMA_DEC": ["B1", "B4의3"]},
                                  config=["prohibited_rules.csv"]),
    "plan_check_targets": dict(method="code", builder="build_tables.py build",
                               parts={"WMA_RULE": ["18의2"]}, config=["plan_check_items.csv"]),
    "energy_recovery_criteria": dict(method="code", builder="build_tables.py build",
                                     parts={"WMA_RULE": ["3/항1"]}, config=["energy_recovery_items.csv"]),
    # LLM 초안 (draft_tables.py)
    "disposal_methods_5": dict(method="llm", builder="draft_tables.py draft disposal_methods_5",
                               parts={"WMA_RULE": ["B5:3", "B5:4", "B5:6.가.7)"]}),
    "condition_texts": dict(method="llm", builder="draft_tables.py draft condition_texts",
                            parts={"WMA": ["17", "18"], "ALLBARO_NOTICE": ["11", "13"],
                                   "GUIDE_DECL": ["§Ⅵ", "§Ⅶ.2", "§Ⅷ.1"]}),
    "reporter_scope": dict(method="llm", builder="draft_tables.py draft reporter_scope",
                           parts={"WMA": ["46/항1", "46/항5"],
                                  "WMA_RULE": ["66/항3", "66/항4", "66/항5", "66/항6", "B16", "B17"]}),
    "designated_check_targets": dict(method="llm", builder="draft_tables.py draft designated_check_targets",
                                     parts={"WMA_RULE": ["17의2"], "GUIDE_DECL": ["§Ⅵ"]}),
    # route_agent 가 만드는 규칙표
    "doc_requirements": dict(method="code", builder="route_agent/src/doc_review/build_requirements.py --confirm",
                             parts={"WMA_RULE": ["16의13", "18의2", "BF5", "BF12"]}),
}
# 표 없이 원문만 파트 3에 붙이는 조문 (proposal/related_laws.py)
TEXT_ONLY = {"WMA_RULE": ["9", "16의13", "18", "B5의3", "B5의4", "B17의2"], "WMA_DEC": ["7"], "WMA": ["45"]}

LAW_NAMES = {
    "WMA": "폐기물관리법", "WMA_DEC": "폐기물관리법 시행령", "WMA_RULE": "폐기물관리법 시행규칙",
    "CEA": "순환경제사회 전환 촉진법", "CEA_DEC": "순환경제사회 전환 촉진법 시행령",
    "CEA_RULE": "순환경제사회 전환 촉진법 시행규칙", "CEA_DESIG": "순환자원 지정 등에 관한 고시",
    "ALLBARO_NOTICE": "폐기물 전자정보처리 프로그램 운영 및 사용 등에 관한 고시",
    "GUIDE_DECL": "사업장폐기물 배출자신고 및 처리계획 확인 업무처리지침",
}
RX_REF = re.compile(r"([A-Z][A-Z_]+):([^@\s,;|\"]+)@(\d{8})(?:#([0-9a-f]+))?")
STATUS_COLS = ("상태", "status")


# ── 경로 ──────────────────────────────────────────────────────────────
def data_root() -> Path:
    env = os.environ.get("WASTE_DATA_DIR")
    if env:
        p = Path(env)
        return p.parent.parent if p.name in ("active", "staged", "old") and p.parent.name == "tables" else p
    return HERE.parents[1] / "data"


def law_dir() -> Path:
    v = os.environ.get("WASTE_LAW_DIR")
    if v:
        p = Path(v)
        return p / "chunk_v2" if (p / "chunk_v2").is_dir() else p    # 보통 env.sh 가 chunk_v2 를 직접 가리킴
    return data_root() / "law" / "chunk_v2"


# ── 원문 조각 ─────────────────────────────────────────────────────────
_cache: dict[str, list[dict]] = {}


def chunks(law: str) -> list[dict]:
    """법령의 가장 최근 시행일 파일"""
    if law not in _cache:
        files = sorted((law_dir() / law).glob("*.jsonl"))
        _cache[law] = [json.loads(l) for l in files[-1].open(encoding="utf-8") if l.strip()] if files else []
    return _cache[law]


def select(law: str, part: str) -> list[dict]:
    """parts 한 칸 → 원문 조각 (조문은 통째, 그 아래 항·호 조각은 빼고)"""
    rows = chunks(law)
    if part == "*":
        return [r for r in rows if r["type"] == "whole"] or [r for r in rows if r["type"] in ("article", "annex")]
    if part.startswith("§"):
        return [r for r in rows if r["type"] == "doc_part" and (r["key"] == part or r["key"].startswith(part + "."))]
    if part.startswith("B"):
        spec = part[1:]
        if spec.startswith("*"):                                   # 제목으로
            return [r for r in rows if r["type"] == "annex" and spec[1:] in (r.get("title") or "")]
        num, _, sub = spec.partition(":")
        num = num.split("@")[0]                                    # "F5@처리구분" → "F5"
        if not sub:
            return [r for r in rows if r["type"] == "annex" and r["key"] == num]
        # "B5:6.가.7)" 처럼 조각보다 깊으면 한 단계씩 올라가며 찾는다 (별표 조각은 보통 호 단위)
        segs = [s for s in re.split(r"(?<=[.)])", sub) if s]
        while segs:
            pre = f"B{num}:{''.join(segs)}".rstrip(".")
            hit = [r for r in rows if r["type"] == "annex_part"
                   and (r["key"].rstrip(".") == pre or r["key"].startswith(pre + "."))]
            if hit:
                return hit
            segs.pop()
        return [r for r in rows if r["type"] == "annex" and r["key"] == num]
    if "/" in part:                                                # 항·호
        exact = [r for r in rows if r["type"] == "article_part" and r["key"] == part]
        if exact:
            return exact
        under = [r for r in rows if r["type"] == "article_part" and r["key"].startswith(part + "/")]
        return under or [r for r in rows if r["type"] == "article" and r["key"] == part.split("/")[0]]
    return [r for r in rows if r["type"] == "article" and r["key"] == part]


def source_list(spec: dict) -> tuple[list[dict], list[str]]:
    got, missing, seen = [], [], set()
    for law, parts in spec["parts"].items():
        for part in parts:
            rs = select(law, part)
            if not rs:
                missing.append(f"{law}:{part}")
            for r in rs:
                k = (law, r["type"], r["key"])
                if k not in seen:
                    seen.add(k)
                    got.append({**r, "law_id": r.get("law_id", law), "_part": part})
    return got, missing


def ref_key(r: dict) -> str:
    """조각 → 근거 키 앞부분 (별표는 B 를 붙임)"""
    k = r["key"]
    return f"B{k}" if r["type"] == "annex" else k


# ── 표 ────────────────────────────────────────────────────────────────
def read_table(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check_refs(rows: list[dict], srcs: list[dict]) -> dict:
    """표 근거 키가 원문 목록 안에 있는지, 시행일·해시가 지금 원문과 같은지"""
    top = lambda k: k.split("/")[0].split(":")[0]               # "18의2/항1/호2" → "18의2", "B5:3." → "B5"
    eff = {(s["law_id"], top(ref_key(s))): s.get("effective") for s in srcs}
    refs = Counter()
    for row in rows:
        for v in row.values():
            for m in RX_REF.finditer(v or ""):
                refs[m.groups()] += 1
    inside, outside, stale = 0, Counter(), Counter()
    for (law, key, d, h), n in refs.items():
        k = (law, top(key))
        if k in eff:
            inside += n
            if eff[k] and d != eff[k]:
                stale[f"{law}:{key}@{d} (지금 {eff[k]})"] += n
        else:
            outside[f"{law}:{top(key)}"] += n
    return {"refs": sum(refs.values()), "inside": inside,
            "outside": dict(outside.most_common()), "old_effective": dict(stale.most_common(20))}


def write_sources_md(path: Path, table: str, spec: dict, srcs: list[dict], missing: list[str]):
    lines = [f"# {table} — 원문", "", f"- 만드는 방식: {'코드 파서' if spec['method'] == 'code' else 'LLM 초안 → 원문 대조'}"
             f" (`{spec['builder']}`)", ""]
    if missing:
        lines += [f"- ⚠️ 원문에서 못 찾은 부분: {', '.join(missing)} (번호 이동·이름 확인)", ""]
    for s in srcs:
        name = s.get("law_name") or LAW_NAMES.get(s["law_id"], s["law_id"])
        lines += [f"## {name} {s['key']} {s.get('title') or ''}".rstrip(),
                  f"`{s['law_id']}:{ref_key(s)}@{s.get('effective')}#{s.get('hash')}`  (요청 부분: `{s['_part']}`)", "",
                  "```", (s.get("text") or "").rstrip(), "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def export_table(table: str, tables_dir: Path, out: Path, cfg_dir: Path) -> dict:
    spec = SOURCES[table]
    d = out / table
    d.mkdir(parents=True, exist_ok=True)
    src_csv = tables_dir / f"{table}.csv"
    rows = read_table(src_csv) if src_csv.exists() else []
    if src_csv.exists():
        shutil.copy2(src_csv, d / src_csv.name)
    srcs, missing = source_list(spec)
    with (d / "sources.jsonl").open("w", encoding="utf-8") as f:
        for s in srcs:
            f.write(json.dumps({k: v for k, v in s.items() if k != "_part"}, ensure_ascii=False) + "\n")
    write_sources_md(d / "sources.md", table, spec, srcs, missing)
    for c in spec.get("config", []):
        if (cfg_dir / c).exists():
            (d / "config").mkdir(exist_ok=True)
            shutil.copy2(cfg_dir / c, d / "config" / c)
    col = next((c for c in STATUS_COLS if rows and c in rows[0]), None)
    confirmed = sum(1 for r in rows if col and (r.get(col) or "").strip() == "confirmed") if col else None
    info = {
        "table": table, "method": spec["method"], "builder": spec["builder"],
        "table_file": str(src_csv) if src_csv.exists() else None, "rows": len(rows), "confirmed": confirmed,
        "parts": spec["parts"], "config": spec.get("config", []), "missing_parts": missing,
        "sources": [{"law_id": s["law_id"], "type": s["type"], "key": s["key"], "title": s.get("title"),
                     "effective": s.get("effective"), "hash": s.get("hash"), "chars": len(s.get("text") or ""),
                     "part": s["_part"]} for s in srcs],
        "ref_check": check_refs(rows, srcs) if rows else None,
    }
    (d / "manifest.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def export_text_only(out: Path) -> list[dict]:
    d = out / "_text_only"
    d.mkdir(parents=True, exist_ok=True)
    spec = {"method": "text", "builder": "proposal/related_laws.py (원문 그대로 파트 3에)", "parts": TEXT_ONLY}
    srcs, missing = source_list(spec)
    write_sources_md(d / "sources.md", "표 없이 원문만 쓰는 조문", {**spec, "method": "code"}, srcs, missing)
    with (d / "sources.jsonl").open("w", encoding="utf-8") as f:
        for s in srcs:
            f.write(json.dumps({k: v for k, v in s.items() if k != "_part"}, ensure_ascii=False) + "\n")
    return srcs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="판정표와 원문을 표별 폴더로 내보내기")
    ap.add_argument("tables", nargs="*", help="표 이름 (없으면 전부)")
    ap.add_argument("--tables", dest="tables_dir", default=os.environ.get("WASTE_DATA_DIR") or str(data_root() / "tables" / "active"))
    ap.add_argument("--out", default=str(data_root() / "table_sources" / f"{date.today():%Y%m%d}"))
    ap.add_argument("--list", action="store_true", help="대응표만 출력")
    a = ap.parse_args(argv)

    names = a.tables or list(SOURCES)
    bad = [n for n in names if n not in SOURCES]
    if bad:
        print(f"모르는 표: {bad}\n가능: {list(SOURCES)}", file=sys.stderr)
        return 2
    if a.list:
        for n in names:
            s = SOURCES[n]
            parts = "; ".join(f"{law} {', '.join(p)}" for law, p in s["parts"].items())
            print(f"{n:<26} {s['method']:<5} {parts}")
        print(f"{'(원문만)':<26} text  " + "; ".join(f"{law} {', '.join(p)}" for law, p in TEXT_ONLY.items()))
        return 0
    if not law_dir().exists():
        print(f"법령 원문 폴더가 없습니다: {law_dir()} (source env.sh)", file=sys.stderr)
        return 1

    out, tdir, cfg = Path(a.out), Path(a.tables_dir), HERE / "config"
    out.mkdir(parents=True, exist_ok=True)
    infos = [export_table(n, tdir, out, cfg) for n in names]
    text_srcs = export_text_only(out) if not a.tables else []

    lines = [f"# 판정표 ↔ 원문 ({date.today()})", "", f"- 표 폴더: `{tdir}`", f"- 원문: `{law_dir()}`", "",
             "| 표 | 방식 | 행 | confirmed | 원문 조각 | 근거 키 (원문 안/전체) | 못 찾은 부분 · 목록 밖 근거 |",
             "|---|---|---|---|---|---|---|"]
    for i in infos:
        rc = i["ref_check"] or {}
        warn = ", ".join(i["missing_parts"] + [f"{k}×{v}" for k, v in (rc.get("outside") or {}).items()][:5])
        lines.append(f"| [{i['table']}]({i['table']}/sources.md) | {i['method']} | {i['rows']} | "
                     f"{'-' if i['confirmed'] is None else i['confirmed']} | {len(i['sources'])} | "
                     f"{rc.get('inside', '-')}/{rc.get('refs', '-')} | {warn or '-'} |")
    if text_srcs:
        lines += ["", f"표 없이 원문만 쓰는 조문 {len(text_srcs)}개: [_text_only/sources.md](_text_only/sources.md)"]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for i in infos:
        rc = i["ref_check"] or {}
        flag = " ⚠ " + ", ".join(i["missing_parts"]) if i["missing_parts"] else ""
        if rc.get("outside"):
            flag += f" ⚠ 목록 밖 근거 {sum(rc['outside'].values())}"
        print(f"{i['table']:<26} 행 {i['rows']:>5}  원문 {len(i['sources']):>3}조각  근거 {rc.get('inside', '-')}/{rc.get('refs', '-')}{flag}")
    print(f"\n→ {out}  (README.md 부터 보기)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
