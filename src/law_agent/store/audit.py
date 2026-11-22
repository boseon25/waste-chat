"""감사 기록.

한 실행 = 한 파일: {root}/audit/{safe_name(case_id)}/{시작시각 YYYYmmddTHHMMSS}.jsonl
한 줄 = 노드 하나의 기록:
    {"ts", "case_id", "node", "question_id", "seconds", "summary", "output"}

인증값은 남기지 않는다:
- 칸 이름에 key/token/secret/password/authorization 이 들어가면 값을 "***" 로 (허용 목록 제외)
- settings llm.api_key_env 환경변수의 실제 값이 문자열 안에 있으면 그 부분을 "***" 로
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from law_agent.settings import env, load_settings
from law_agent.state import safe_name, to_jsonable

MASK = "***"

# 칸 이름이 이 모양이면 값을 가린다 (대소문자 무시).
# 그냥 "key" 나 "top_key"·"chunk_key"(조문 키)는 인증값이 아니므로 가리지 않는다 — api_key·access_key 처럼
# 인증 쪽 말이 붙은 key 만 가린다. (통합 때 수정: 조문 키가 *** 로 가려져 근거 추적이 안 되는 문제)
import re as _re

SENSITIVE_RE = _re.compile(
    r"(?:api|access|secret|private|auth|client|subscription)[_-]?key|token|secret|password|passwd|authorization|credential",
    _re.IGNORECASE)

# 이름에 위 낱말이 있어도 가리지 않는 칸 (소문자로 비교)
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "question_id",
        "pack_hash",
        "cache_key",
        "key",  # 조문 키 (Candidate.key, EvidenceItem.key) — 인증값이 아님
        "prompt_tokens",  # LLM 사용량
        "completion_tokens",
        "total_tokens",
        "max_tokens",
    }
)


def is_sensitive_field(name: str) -> bool:
    """칸 이름이 가려야 할 칸인지."""
    low = name.lower()
    if low in ALLOWED_FIELDS:
        return False
    return bool(SENSITIVE_RE.search(low))


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """중첩 dict·list 안까지 가린다. value 는 to_jsonable 을 거친 값이어야 한다."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            name = _scrub(str(k), secrets)
            out[name] = MASK if is_sensitive_field(str(k)) else redact(v, secrets)
        return out
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        return _scrub(value, secrets)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # to_jsonable 이 바꾸지 않는 값(Path, datetime 등)은 문자열로 남긴다 (감사 기록은 멈추지 않는다)
    return _scrub(str(value), secrets)


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for s in secrets:
        if s and s in text:
            text = text.replace(s, MASK)
    return text


def _secret_values() -> tuple[str, ...]:
    """settings llm.api_key_env 가 가리키는 환경변수의 실제 값 (없으면 빈 튜플)."""
    try:
        value = env("api_key_env", section="llm", required=False)
    except KeyError:  # settings.yaml 에 llm.api_key_env 가 없어도 감사 기록은 동작해야 한다
        return ()
    return (value,) if isinstance(value, str) and value else ()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class AuditLog:
    """사례 하나의 한 실행에 대한 감사 기록 (JSON Lines)."""

    def __init__(self, case_id: str, root: Path | None = None, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = bool((load_settings().get("store") or {}).get("audit", True))
        self.case_id = case_id
        self.enabled: bool = enabled
        self.started_at = datetime.now()
        self._secrets = _secret_values()
        # 꺼져 있으면 파일도 경로도 만들지 않는다
        self.path: Path | None = None
        if enabled:
            if root is None:
                from law_agent.store.cache import default_root

                root = default_root()
            self.path = self._reserve_file(Path(root))

    def _reserve_file(self, root: Path) -> Path:
        """시작 시각 이름으로 빈 파일을 만든다. 같은 초에 다른 실행이 있으면 -2, -3 … 을 붙인다."""
        folder = root / "audit" / safe_name(self.case_id)
        folder.mkdir(parents=True, exist_ok=True)
        stem = self.started_at.strftime("%Y%m%dT%H%M%S")
        n = 1
        while True:
            path = folder / (f"{stem}.jsonl" if n == 1 else f"{stem}-{n}.jsonl")
            try:
                path.open("x", encoding="utf-8").close()
                return path
            except FileExistsError:
                n += 1

    def record(
        self,
        node: str,
        summary: dict,
        output: dict | None = None,
        seconds: float | None = None,
        question_id: str | None = None,
    ) -> dict:
        """한 줄을 추가하고 (가린 뒤의) 그 줄을 돌려준다."""
        line = {
            "ts": _now_iso(),
            "case_id": self.case_id,
            "node": node,
            "question_id": question_id,
            "seconds": None if seconds is None else round(float(seconds), 3),
            "summary": summary,
            "output": output,
        }
        safe = redact(to_jsonable(line), self._secrets)
        if self.enabled and self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(safe, ensure_ascii=False) + "\n")
        return safe

    def timer(self, node: str, question_id: str | None = None) -> "AuditTimer":
        """with 문으로 걸린 시간을 재서 기록한다.

        with log.timer("judge", "A-1") as t:
            t.summary["model"] = "m1"
            t.output = {...}
        t.line  # 기록된 줄
        """
        return AuditTimer(self, node, question_id)


class AuditTimer:
    """AuditLog.timer() 가 돌려주는 with 문 객체."""

    def __init__(self, log: AuditLog, node: str, question_id: str | None) -> None:
        self.log = log
        self.node = node
        self.question_id = question_id
        self.summary: dict = {}
        self.output: dict | None = None
        self.line: dict | None = None
        self._start = 0.0

    def __enter__(self) -> "AuditTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        seconds = time.perf_counter() - self._start
        if exc_type is not None:
            # 예외도 기록하고, 예외는 그대로 위로 올린다
            self.summary = {**self.summary, "error": f"{exc_type.__name__}: {exc}"}
        self.line = self.log.record(
            self.node, self.summary, self.output, seconds=seconds, question_id=self.question_id
        )
        return False
