"""에이전트 — 규칙 판정이 남긴 빈칸(업체 미확보 경로)을 도구로 조사해 대안 후보를 붙인다.

  from agent import find_alternatives
  find_alternatives(pipeline, case, pre, routes, route_dicts)   # 미확보 경로마다 route["alternatives"]

판정·업체 목록은 바꾸지 않는다. 결과 status는 "candidate"(사람 확인 전 후보) 또는 "failed".
"""
from __future__ import annotations

from .graph import run
from .tools import Ctx


def needs_agent(route: dict) -> bool:
    p = route.get("partners") or {}
    return route.get("regulatory") != "rejected" and p.get("secured") is False


def find_alternatives(pipe, case, pre, routes, route_dicts) -> int:
    n = 0
    for i, d in enumerate(route_dicts):
        if not needs_agent(d):
            continue
        ctx = Ctx(index=i, routes=routes, route_dicts=route_dicts, case=case, pre=pre,
                  partner_data=pipe.partner_data, data_dir=pipe.data_dir, law_store=pipe.law_store, top_n=pipe.top_n)
        d["alternatives"] = run(ctx)
        n += 1
    return n


__all__ = ["find_alternatives", "needs_agent", "run", "Ctx"]
