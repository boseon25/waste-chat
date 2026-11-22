"""settings.yaml 읽기 (한 번만). 모든 모듈은 이 함수로 설정을 읽는다.

인증값은 settings.yaml 에 없다. *_env 칸은 환경변수 "이름"이고, 값은 env() 로 그때그때 읽는다.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "config" / "settings.yaml"


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_settings(path: str | Path | None = None) -> dict:
    """settings.yaml → dict. LAW_AGENT_SETTINGS 환경변수가 있으면 그 파일."""
    p = path or os.environ.get("LAW_AGENT_SETTINGS") or DEFAULT_PATH
    return _load(str(Path(p).resolve()))


def env(name_key: str, settings: dict | None = None, section: str = "data", required: bool = True) -> str | None:
    """settings[section][name_key] 에 적힌 환경변수 이름으로 값을 읽는다.
    예: env("law_dir_env") → os.environ["WASTE_LAW_DIR"]"""
    s = settings if settings is not None else load_settings()
    var = (s.get(section) or {}).get(name_key)
    if not var:
        raise KeyError(f"settings.yaml 에 {section}.{name_key} 가 없음")
    val = os.environ.get(var)
    if required and not val:
        raise RuntimeError(f"환경변수 {var} 가 비어 있음 (source env.sh 를 했는지 확인)")
    return val
