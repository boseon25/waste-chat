"""판단 캐시.

같은 입력이면 같은 결과를 내기 위해 judge 응답을 cache_key 별 JSON 파일로 저장한다.
파일: {root}/cache/{key[:2]}/{key}.json
형식: {"key": ..., "saved_at": ISO 시각, "value": {...}}
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from law_agent.settings import env, load_settings
from law_agent.state import to_jsonable

# state.cache_key() 결과 형식: SHA-256 소문자 hex 64자
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")

# 이 환경변수가 "1" 이면 설정과 상관없이 캐시를 끈다 (안정성 평가용)
NO_CACHE_ENV = "LAW_AGENT_NO_CACHE"


def _store_settings() -> dict[str, Any]:
    """settings.yaml 의 store 칸 (없으면 빈 dict)."""
    return dict(load_settings().get("store") or {})


def default_root() -> Path:
    """저장 루트 = Path(env("root_env", section="store")).
    settings 에 칸이 없으면 KeyError, 환경변수가 비어 있으면 RuntimeError (settings.env 가 냄)."""
    return Path(env("root_env", section="store"))


def check_key(key: str) -> str:
    """캐시 키 형식 검사. 64자 소문자 hex 가 아니면 ValueError."""
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise ValueError(f"캐시 키 형식이 잘못되었습니다 (64자 hex 이어야 함): {key!r}")
    return key


class JudgeCache:
    """judge 응답 캐시. 꺼져 있으면 get 은 항상 None, put 은 아무것도 안 한다."""

    def __init__(self, root: Path | None = None, enabled: bool | None = None) -> None:
        if os.environ.get(NO_CACHE_ENV) == "1":
            enabled = False
        elif enabled is None:
            enabled = bool(_store_settings().get("cache", True))
        self.enabled: bool = enabled
        # 꺼져 있으면 루트 환경변수가 없어도 동작하도록, 켜져 있을 때만 기본 루트를 읽는다
        if root is not None:
            self.root: Path | None = Path(root)
        else:
            self.root = default_root() if enabled else None

    def path_for(self, key: str) -> Path:
        """키에 해당하는 캐시 파일 경로."""
        check_key(key)
        if self.root is None:
            raise RuntimeError("캐시가 꺼져 있어 저장 루트가 없습니다")
        return self.root / "cache" / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        """저장된 value 를 돌려준다. 꺼져 있거나, 없거나, JSON 이 깨졌으면 None."""
        check_key(key)
        if not self.enabled:
            return None
        path = self.path_for(key)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("key") != key:
            return None
        value = data.get("value")
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict) -> None:
        """value 를 저장한다. 임시 파일에 쓰고 os.replace 로 바꿔 끼운다 (원자적)."""
        check_key(key)
        if not self.enabled:
            return
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "value": to_jsonable(value),
        }
        text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            # 실패하면 임시 파일을 남기지 않는다
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise



def purge_dry(root: Path | None = None) -> int:
    """dry 실행이 캐시에 남긴 가짜 답(reason "dry run")을 지운다. 지운 파일 수."""
    base = Path(root or default_root()) / "cache"
    n = 0
    for f in base.glob("*/*.json"):
        try:
            if '"dry run"' in f.read_text(encoding="utf-8"):
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


if __name__ == "__main__":                            # PYTHONPATH=src python3 -m law_agent.store.cache --purge-dry
    import sys
    if "--purge-dry" in sys.argv:
        print(f"dry 가짜 답 {purge_dry()}개 지움")
