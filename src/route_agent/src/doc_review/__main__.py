"""python3 -m doc_review 요청.json --proposal 경로.json --case 사례.json [--tables 폴더] [--out 결과.json]

--proposal 파일이 RouteProposal 목록이면 요청의 route_id 로 고른다.
--case 파일이 파트 1 묶음(cases[])이면 요청의 case_id 로 고른다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from doc_review import load_requirements, review


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="doc_review")
    ap.add_argument("request")
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--tables", default=os.environ.get("WASTE_DATA_DIR"))
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    req = _load(a.request)
    props = _load(a.proposal)
    if isinstance(props, dict) and "route_proposals" in props:
        props = props["route_proposals"]
    if isinstance(props, list):
        prop = next((p for p in props if p.get("route_id") == req.get("route_id")), None)
        if prop is None:
            sys.exit(f"route_id {req.get('route_id')} 인 RouteProposal이 없습니다")
    else:
        prop = props
    cases = _load(a.case)
    case = (next((c for c in cases["cases"] if c.get("case_id") == req.get("case_id")), cases["cases"][0])
            if isinstance(cases, dict) and "cases" in cases else cases)

    res = review(req, prop, case, load_requirements(a.tables))
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"{res['verdict']} (오류 {res['summary'].get('error', 0)}, 확인 {res['summary'].get('warning', 0)}) → {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
