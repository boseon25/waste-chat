"""InformationRequest — 배출업체에 되물을 것 (파트 1 방향).

경로·단계에 흩어진 질문 중 업체가 아니라 배출업체가 답해야 하는 것을 모아,
같은 질문은 하나로 합치고 어느 경로에 영향을 주는지(affected_routes) 붙인다.
"""
from __future__ import annotations

from .route_proposal import SCHEMA_VERSION, assign_route_ids

INTERNAL = {"disposal_method"}          # 파트 2 판정표 보완 사항 (배출업체에 묻지 않음)
TO_LABEL = {"analysis": "배출업체 (분석기관 의뢰 필요)", "designated_analysis": "배출업체 (분석기관 의뢰 필요)"}


def build_information_request(result: dict, proposals: list[dict] | None = None) -> dict:
    """proposals를 주면 그 route_id를 쓴다 (같은 종류 경로 번호까지 일치)."""
    items: dict[str, dict] = {}
    internal: dict[str, dict] = {}

    def add(field: str, text: str, rid: str, to: str | None) -> None:
        target = internal if (field in INTERNAL or to == "part2") else items
        it = target.setdefault(field, {"field": field, "text": text,
                                       "to": TO_LABEL.get(field, "배출업체"), "affected_routes": []})
        if rid not in it["affected_routes"]:
            it["affected_routes"].append(rid)

    ids = [p["route_id"] for p in proposals] if proposals else assign_route_ids(result["case_id"], result["routes"])
    for r, rid in zip(result["routes"], ids):
        for q in r.get("questions") or []:
            if q.get("to") in (None, "part1", "user", "part2"):
                add(q["field"], q["text"], rid, q.get("to"))
        for s in (r.get("partners") or {}).get("steps") or []:
            for q in s.get("questions") or []:
                if q.get("to") in ("part1", "part2"):
                    add(q["field"], q["text"], rid, q.get("to"))

    for p in proposals or []:                          # 업체 회신에서 생긴 배출업체 확인 사항 (시험 요구 등)
        for q in p.get("emitter_requests") or []:
            add(q["field"], q["text"], p["route_id"], "part1")

    reqs = [{"request_id": f"IR-{i + 1}", **v} for i, v in enumerate(items.values())]
    return {"schema_version": SCHEMA_VERSION, "case_id": result["case_id"], "case_version": result["case_version"],
            "requests": reqs,
            "fields": [r["field"] for r in reqs],
            "part2_internal": list(internal.values())}
