"""store/cache.py, store/audit.py 테스트 (네트워크 없음).

tmp_path 에 임시 settings.yaml 을 쓰고 LAW_AGENT_SETTINGS 로 가리킨다.
실제 config/settings.yaml·실제 환경변수 값에 기대지 않는다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from law_agent import settings as settings_mod
from law_agent.state import cache_key, safe_name
from law_agent.store import AuditLog, JudgeCache

SECRET = "sk-test-SECRET-1234567890"
KEY_ENV = "LAW_AGENT_TEST_API_KEY"


def make_key(text: str = "a") -> str:
    """state.cache_key 로 만든 진짜 형식의 키."""
    return cache_key(text, "pack", "facts", "model-x", "judge_v1")


class FakeSettings:
    """임시 settings.yaml 을 쓰고 LAW_AGENT_SETTINGS 로 가리킨다 (실제 load_settings·env 를 그대로 씀)."""

    def __init__(self, monkeypatch, tmp_path: Path) -> None:
        self.mp = monkeypatch
        self.root = tmp_path / "data"
        self.file = tmp_path / "settings.yaml"
        monkeypatch.setenv("LAW_AGENT_SETTINGS", str(self.file))
        monkeypatch.setenv("LAW_AGENT_DATA_DIR", str(self.root))
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.delenv("LAW_AGENT_NO_CACHE", raising=False)
        self.configure()

    def configure(self, cache: bool = True, audit: bool = True) -> None:
        self.file.write_text(
            "store:\n"
            "  root_env: LAW_AGENT_DATA_DIR\n"
            f"  cache: {str(cache).lower()}\n"
            f"  audit: {str(audit).lower()}\n"
            "llm:\n"
            f"  api_key_env: {KEY_ENV}\n",
            encoding="utf-8",
        )
        settings_mod._load.cache_clear()  # load_settings 는 lru_cache 라 다시 읽게 한다

    def set_secret(self, value: str) -> None:
        self.mp.setenv(KEY_ENV, value)


@pytest.fixture
def fake_settings(monkeypatch, tmp_path):
    fs = FakeSettings(monkeypatch, tmp_path)
    yield fs
    settings_mod._load.cache_clear()


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ───────────────────────── 캐시 ─────────────────────────

def test_cache_put_get(fake_settings):
    c = JudgeCache()
    key = make_key()
    value = {"answer": "yes", "reason": "별표 4의3 해당", "quotes": [{"ref": "WMA:13", "text": "재활용"}]}
    c.put(key, value)
    assert c.get(key) == value

    path = fake_settings.root / "cache" / key[:2] / f"{key}.json"
    raw = path.read_text(encoding="utf-8")
    assert "재활용" in raw  # ensure_ascii=False
    data = json.loads(raw)
    assert data["key"] == key and data["value"] == value
    assert "T" in data["saved_at"]
    # 임시 파일이 남지 않는다
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_cache_missing_returns_none(fake_settings):
    assert JudgeCache().get(make_key("없음")) is None


def test_cache_overwrite(fake_settings):
    c = JudgeCache()
    key = make_key()
    c.put(key, {"v": 1})
    c.put(key, {"v": 2})
    assert c.get(key) == {"v": 2}


def test_cache_disabled_by_argument(fake_settings):
    c = JudgeCache(enabled=False)
    key = make_key()
    c.put(key, {"v": 1})
    assert c.get(key) is None
    assert not (fake_settings.root / "cache").exists()


def test_cache_disabled_by_settings(fake_settings):
    fake_settings.configure(cache=False)
    c = JudgeCache()
    assert c.enabled is False
    c.put(make_key(), {"v": 1})
    assert not (fake_settings.root / "cache").exists()


def test_cache_no_cache_env_overrides(fake_settings, monkeypatch, tmp_path):
    key = make_key()
    JudgeCache(root=tmp_path).put(key, {"v": 1})
    monkeypatch.setenv("LAW_AGENT_NO_CACHE", "1")
    c = JudgeCache(root=tmp_path, enabled=True)  # 명시적으로 켜도 꺼진다
    assert c.enabled is False
    assert c.get(key) is None
    c.put(make_key("b"), {"v": 2})
    assert not (tmp_path / "cache" / make_key("b")[:2] / f"{make_key('b')}.json").exists()


def test_cache_broken_file_returns_none(fake_settings):
    c = JudgeCache()
    key = make_key()
    path = c.path_for(key)
    path.parent.mkdir(parents=True)
    path.write_text("{깨진 json", encoding="utf-8")
    assert c.get(key) is None
    # 형식은 맞는데 키가 다르거나 value 가 dict 가 아니어도 None
    path.write_text(json.dumps({"key": make_key("x"), "value": {}}), encoding="utf-8")
    assert c.get(key) is None
    path.write_text(json.dumps({"key": key, "value": [1, 2]}), encoding="utf-8")
    assert c.get(key) is None


@pytest.mark.parametrize("bad", ["", "abc", make_key().upper(), make_key()[:63], make_key() + "0",
                                 "../" + make_key()[3:], "g" * 64])
def test_cache_bad_key(fake_settings, bad):
    c = JudgeCache()
    with pytest.raises(ValueError):
        c.get(bad)
    with pytest.raises(ValueError):
        c.put(bad, {"v": 1})


def test_cache_disabled_needs_no_root(monkeypatch, fake_settings):
    monkeypatch.delenv("LAW_AGENT_DATA_DIR")
    assert JudgeCache(enabled=False).get(make_key()) is None
    with pytest.raises(RuntimeError):
        JudgeCache(enabled=True)


# ───────────────────────── 감사 기록 ─────────────────────────

def test_audit_lines_in_order(fake_settings):
    log = AuditLog("CASE-001")
    assert log.path.parent == fake_settings.root / "audit" / safe_name("CASE-001")
    assert log.path.suffix == ".jsonl" and len(log.path.stem) == 15  # YYYYmmddTHHMMSS

    r1 = log.record("planner", {"questions": 12})
    r2 = log.record("judge", {"model": "m1"}, output={"answer": "yes", "이유": "해당"},
                    seconds=1.23456, question_id="A-1")
    lines = read_lines(log.path)
    assert lines == [r1, r2]
    assert [x["node"] for x in lines] == ["planner", "judge"]
    assert set(r1) == {"ts", "case_id", "node", "question_id", "seconds", "summary", "output"}
    assert r1["question_id"] is None and r1["output"] is None and r1["seconds"] is None
    assert r2["seconds"] == 1.235 and r2["question_id"] == "A-1" and r2["case_id"] == "CASE-001"
    assert "해당" in log.path.read_text(encoding="utf-8")  # ensure_ascii=False


def test_audit_one_file_per_run(fake_settings):
    a = AuditLog("CASE-001")
    b = AuditLog("CASE-001")  # 같은 초에 시작해도 파일이 겹치지 않는다
    assert a.path != b.path
    a.record("x", {})
    b.record("y", {})
    assert [x["node"] for x in read_lines(a.path)] == ["x"]
    assert [x["node"] for x in read_lines(b.path)] == ["y"]


def test_audit_timer(fake_settings):
    log = AuditLog("CASE-001")
    with log.timer("retriever", question_id="B-2") as t:
        time.sleep(0.05)
        t.summary["candidates"] = 5
        t.output = {"keys": ["WMA:13"]}
    assert t.line is not None
    assert t.line["seconds"] >= 0.05
    assert t.line["summary"] == {"candidates": 5}
    assert t.line["question_id"] == "B-2"
    assert read_lines(log.path) == [t.line]


def test_audit_timer_records_error(fake_settings):
    log = AuditLog("CASE-001")
    with pytest.raises(RuntimeError):
        with log.timer("judge") as t:
            raise RuntimeError("모델 응답 없음")
    (line,) = read_lines(log.path)
    assert line["summary"]["error"] == "RuntimeError: 모델 응답 없음"
    assert line["seconds"] is not None


def test_audit_redact_nested(fake_settings):
    log = AuditLog("CASE-001")
    line = log.record("judge", {
        "api_key": "abc",
        "Authorization": "Bearer xyz",
        "nested": {"ACCESS_TOKEN": "t", "list": [{"db_password": "p", "ok": 1}], "Client_Secret": {"a": 1}},
        "model": "m1",
    })
    s = line["summary"]
    assert s["api_key"] == "***" and s["Authorization"] == "***"
    assert s["nested"]["ACCESS_TOKEN"] == "***"
    assert s["nested"]["list"] == [{"db_password": "***", "ok": 1}]
    assert s["nested"]["Client_Secret"] == "***"
    assert s["model"] == "m1"
    text = log.path.read_text(encoding="utf-8")
    assert "xyz" not in text and "abc" not in text


def test_audit_redact_allowlist(fake_settings):
    log = AuditLog("CASE-001")
    k = make_key()
    line = log.record("evidence", {"question_id": "A-1", "pack_hash": "h1", "cache_key": k,
                                   "Cache_Key": k, "items": [{"law": "WMA", "key": "13"}]},
                      question_id="A-1")
    s = line["summary"]
    assert s["question_id"] == "A-1" and s["pack_hash"] == "h1"
    assert s["cache_key"] == k and s["Cache_Key"] == k
    assert s["items"] == [{"law": "WMA", "key": "13"}]
    assert line["question_id"] == "A-1"


def test_audit_redact_env_secret_value(fake_settings):
    fake_settings.set_secret(SECRET)
    log = AuditLog("CASE-001")
    line = log.record("llm", {"error": f"401 for key {SECRET} at server", "deep": [[SECRET]]},
                      output={"raw": "prefix" + SECRET})
    assert line["summary"]["error"] == "401 for key *** at server"
    assert line["summary"]["deep"] == [["***"]]
    assert line["output"]["raw"] == "prefix***"
    assert SECRET not in log.path.read_text(encoding="utf-8")


def test_audit_to_jsonable(fake_settings):
    log = AuditLog("CASE-001")
    line = log.record("composer", {"path": Path("/x/y"), "tuple": (1, 2)})
    assert line["summary"] == {"path": "/x/y", "tuple": [1, 2]}


def test_audit_disabled_no_file(fake_settings):
    log = AuditLog("CASE-001", enabled=False)
    line = log.record("planner", {"api_key": "abc", "n": 1})
    assert line["summary"] == {"api_key": "***", "n": 1}
    with log.timer("judge") as t:
        t.summary["n"] = 2
    assert t.line["summary"] == {"n": 2}
    assert log.path is None
    assert not (fake_settings.root / "audit").exists()


def test_audit_disabled_by_settings(fake_settings):
    fake_settings.configure(audit=False)
    log = AuditLog("CASE-001")
    log.record("planner", {})
    assert not (fake_settings.root / "audit").exists()


def test_audit_non_json_value_to_str(fake_settings):
    fake_settings.set_secret(SECRET)
    log = AuditLog("CASE-001")

    class Odd:
        def __str__(self) -> str:
            return f"odd {SECRET}"

    line = log.record("x", {"odd": Odd()})
    assert line["summary"]["odd"] == "odd ***"
    assert read_lines(log.path) == [line]


def test_cache_put_non_json_raises_without_file(fake_settings):
    c = JudgeCache()
    key = make_key()
    with pytest.raises(TypeError):
        c.put(key, {"path": object()})
    assert c.get(key) is None
    assert not c.path_for(key).parent.exists() or not any(c.path_for(key).parent.iterdir())


def test_safe_name_case_folder(fake_settings):
    log = AuditLog("CASE/2026 001")
    assert log.path.parent.name == "CASE__2026__001"


def test_audit_law_keys_not_masked(fake_settings):
    """조문 키·검색 키는 인증값이 아니다 (통합 때 추가)."""
    log = AuditLog("CASE-001")
    line = log.record("retriever", {"top_key": "13의2", "chunk_key": "WMA:13의2@20261112", "consensus_key": "x",
                                    "apiKey": "a", "subscription-key": "b", "private_key": "c"})
    s = line["summary"]
    assert s["top_key"] == "13의2" and s["chunk_key"] == "WMA:13의2@20261112" and s["consensus_key"] == "x"
    assert s["apiKey"] == "***" and s["subscription-key"] == "***" and s["private_key"] == "***"
