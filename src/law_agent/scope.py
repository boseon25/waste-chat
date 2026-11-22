"""서비스 범위 (settings scope). 2026.11.18 중간재활용업만.

scope_rcodes(): 범위 안 R코드 머리 (예: ["R-10"]). 비어 있으면 제한 없음.
in_scope_rcodes(codes): 그중 범위 안 것만. 범위 안 것이 하나도 없으면 원래 목록 (판단을 막지 않게).
"""
from __future__ import annotations


def scope() -> dict:
    try:
        from law_agent.settings import load_settings
        return dict(load_settings().get("scope") or {})
    except Exception:                                  # noqa: BLE001
        return {}


def scope_rcodes() -> list[str]:
    return [str(x) for x in scope().get("r_codes") or []]


def in_scope_rcodes(codes: list[str]) -> list[str]:
    heads = scope_rcodes()
    if not heads:
        return list(codes)
    keep = [c for c in codes if any(c == h or c.startswith(h + "-") for h in heads)]
    return keep or list(codes)
