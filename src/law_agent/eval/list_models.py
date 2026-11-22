"""타임리 직접 API 에서 내 키로 쓸 수 있는 모델 이름 (회사별). 키는 출력하지 않는다.

  PYTHONPATH=src python3 -m law_agent.eval.list_models
settings.yaml judge_models·planner_model 은 "회사/모델이름" 으로 적는다 (예: anthropic/claude-sonnet-4-6).
"""
from __future__ import annotations

import httpx

from law_agent.llm import client as C
from law_agent.settings import load_settings


def main() -> int:
    key, host = C._api_key(), C._host()
    llm = load_settings().get("llm") or {}
    want = list(llm.get("judge_models") or []) + [llm.get("planner_model")]
    reqs = {
        "openai": (f"{host}/api/v2/chat/direct/openai/v1/models", {"Authorization": f"Bearer {key}"}),
        "anthropic": (f"{host}/api/v2/chat/direct/anthropic/v1/models",
                      {"x-api-key": key, "anthropic-version": "2023-06-01"}),
        "google": (f"{host}/api/v2/chat/direct/google/v1beta/models", {"x-goog-api-key": key}),
    }
    found = set()
    for vendor, (url, h) in reqs.items():
        try:
            r = httpx.get(url, headers=h, timeout=30)
        except httpx.HTTPError as e:
            print(f"{vendor}: 연결 실패 {type(e).__name__}")
            continue
        if r.status_code >= 400:
            print(f"{vendor}: HTTP {r.status_code} {r.text[:200]}")
            continue
        d = r.json()
        items = d.get("data") or d.get("models") or []
        names = sorted({(it.get("id") or it.get("name") or "").removeprefix("models/") for it in items} - {""})
        found |= {f"{vendor}/{n}" for n in names}
        print(f"{vendor} ({len(names)}): {', '.join(names)}")
    print("\n설정의 모델:")
    for m in want:
        if m:
            print(f"  {'OK ' if m in found else '없음'} {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
