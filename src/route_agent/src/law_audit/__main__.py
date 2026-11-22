"""python -m law_audit --chunks <data/law/chunks> --tables <data/tables/active> --out <폴더> [--depth 2]"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from .audit import audit, load_chunks, used_keys


SCOPE_LAWS = ("WMA", "WMA_DEC", "WMA_RULE", "GUIDE_DECL", "ALLBARO_NOTICE", "CEA_DESIG")
ALWAYS_SKIP = ("목적", "재검토기한", "정의", "적용범위", "음식물", "생활폐기물", "생활계", "의료", "광역", "시멘트",
               "기술관리", "전용용기", "결격", "취소", "방치폐기물", "사후관리", "처리시설의 설치", "임원")


def core_candidates(res: dict, laws=SCOPE_LAWS, top: int = 30) -> list[dict]:
    """여러 방법에 동시에 걸린 조문을 위로. 인용 1단계 2점, 2단계 1점, RAG 검색 2점, 단계 제목 1점."""
    score: dict[tuple, dict] = {}

    def add(law, key, title, pts, why, stage=None):
        if laws and law not in laws or any(w in (title or "") for w in ALWAYS_SKIP):
            return
        it = score.setdefault((law, key), {"law": law, "key": key, "title": title, "score": 0, "why": [], "stages": []})
        it["score"] += pts
        if why not in it["why"]:
            it["why"].append(why)
        if stage and stage not in it["stages"]:
            it["stages"].append(stage)

    for c in res["graph_candidates"]:
        add(c["law"], c["key"], c["title"], 2 if c["depth"] == 1 else 1, "인용 " + str(c["depth"]) + "단계")
    for s in res["stages"]:
        for r in s["not_used"]:
            add(r["law"], r["key"], r["title"], 1, "단계 제목", s["stage"])
        for r in s.get("search_not_used", []):
            add(r["law"], r["key"], r["title"], 2, "RAG 검색", s["stage"])
    from lawstore import LAW_NAME
    out = sorted(score.values(), key=lambda x: (-x["score"], x["law"], x["key"]))[:top]
    for it in out:
        it["law_name"] = LAW_NAME.get(it["law"], it["law"])
    return out


def write_report(res: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "law_audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "law_audit_candidates.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["구분", "단계", "법령", "조항", "제목", "찾은 이유", "판정표 사용", "원문 앞부분", "검토 결과(사람)"])
        for c in res["graph_candidates"]:
            w.writerow(["인용 그래프", "", c["law_name"], c["key"], c["title"], c["reason"], "", c["text"], ""])
        for s in res["stages"]:
            for r in s["not_used"]:
                w.writerow(["절차 점검", s["stage"], r["law_name"], r["key"], r["title"],
                            "제목 키워드: " + ", ".join(r["matched"]), "", r["text"], ""])
            for r in s.get("search_not_used", []):
                w.writerow(["RAG 검색", s["stage"], r["law_name"], r["key"], r["title"],
                            f"{r['matched'][0]} (점수 {r['score']}, 조각 {r['hit']})", "", r["text"], ""])
    lines = ["# 법령 누락 점검 보고서", "",
             f"- 조문·별표 {res['node_count']}개 중 판정표가 근거로 쓰는 것 {res['used_count']}개",
             f"- 인용 그래프 누락 후보 {len(res['graph_candidates'])}건",
             f"- 판정표에 있는데 조문 파일에 없는 키 {len(res['missing_in_chunks'])}건", "",
             "## 0. 핵심 후보 (여러 방법에 동시에 걸린 조문, 먼저 검토)", "",
             f"범위: {', '.join(SCOPE_LAWS)} / 점수: 인용 1단계 2점·2단계 1점, RAG 검색 2점, 단계 제목 1점", "",
             "| 점수 | 법령 | 조항 | 제목 | 근거 | 단계 |", "|---|---|---|---|---|---|"]
    res["core_candidates"] = core_candidates(res)
    for c in res["core_candidates"]:
        lines.append(f"| {c['score']} | {c['law_name']} | {c['key']} | {c['title'][:30]} | {', '.join(c['why'])} | {', '.join(c['stages'])} |")
    lines += ["",
             "## 1. 절차 단계별 (제목 키워드 기준)", "",
             "| 단계 | 제목으로 찾은 조문 | 판정표 사용 | 안 쓰는 조문 (앞 5개) | RAG 검색으로 더 찾은 조문 (앞 5개) |",
             "|---|---|---|---|---|"]
    for s in res["stages"]:
        heads = ", ".join(f"{r['law_name']} {r['key']}({r['title'][:20]})" for r in s["not_used"][:5])
        more = ", ".join(f"{r['law_name']} {r['key']}({r['title'][:20]})" for r in s.get("search_not_used", [])[:5])
        lines.append(f"| {s['stage']} | {s['total']} | {s['used']} | {heads} | {more} |")
    lines += ["", "## 2. 인용 그래프 누락 후보 (판정표 근거 조문에서 이어짐)", "",
              "| 법령 | 조항 | 제목 | 찾은 이유 |", "|---|---|---|---|"]
    for c in res["graph_candidates"]:
        lines.append(f"| {c['law_name']} | {c['key']} | {c['title']} | {c['reason']} |")
    if res["missing_in_chunks"]:
        lines += ["", "## 3. 판정표에 있는데 조문 파일에 없는 키 (개정·오타 확인)", ""]
        lines += [f"- {k}" for k in res["missing_in_chunks"]]
    lines += ["", "전체 목록: law_audit_candidates.csv (마지막 칸에 검토 결과를 적어 판정표 반영 여부를 정한다)"]
    path = out / "law_audit_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (out / "law_audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="법령 누락 점검 (개발용)")
    ap.add_argument("--chunks", required=True, help="data/law/chunks")
    ap.add_argument("--tables", default=os.environ.get("WASTE_DATA_DIR"), help="data/tables/active")
    ap.add_argument("--out", default="law_audit_out")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--no-search", action="store_true", help="RAG 검색 단계 끄기")
    args = ap.parse_args()
    from lawstore import LawStore
    store = LawStore.load(args.chunks)
    res = audit(store.chunks, used_keys(args.tables), args.depth, store=None if args.no_search else store)
    path = write_report(res, Path(args.out))
    print(path.read_text(encoding="utf-8")[:3000])
    print(f"\n→ {path}")


if __name__ == "__main__":
    main()
