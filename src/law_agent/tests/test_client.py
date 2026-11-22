"""llm/client.py 테스트 — 네트워크 없이 돈다 (가짜 OpenAI 객체, mock 파일)."""
from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from law_agent.llm import client as C
from law_agent.state import safe_name

SECRET_KEY = "sk-TEST-SECRET-키-0000"
SECRET_URL = "http://secret-llm-host.invalid/v1"
SETTINGS = {
    "llm": {
        "base_url_env": "TEST_LLM_BASE_URL",
        "api_key_env": "TEST_LLM_API_KEY",
        "temperature": 0,
        "judge_models": ["m-a", "m-b", "m-c"],
        "planner_model": "m-a",
        "timeout_seconds": 5,
    },
    "limits": {"max_llm_calls_per_case": 3},
}
ENV_VALUES = {"base_url_env": SECRET_URL, "api_key_env": SECRET_KEY}


# ---------------------------------------------------------------- 가짜 OpenAI

# openai 판(httpx / httpx2)에 묶이지 않도록 요청·응답은 가짜 객체로 만든다
def _req() -> Any:
    return SimpleNamespace(method="POST", url="http://fake.invalid/chat/completions")


def _http_resp(code: int) -> Any:
    return SimpleNamespace(status_code=code, headers={}, request=_req())


def bad_request() -> openai.BadRequestError:
    return openai.BadRequestError("bad", response=_http_resp(400), body=None)


def status_error(code: int) -> openai.APIStatusError:
    resp = _http_resp(code)
    if code == 429:
        return openai.RateLimitError("rate", response=resp, body=None)
    if code >= 500:
        return openai.InternalServerError("server", response=resp, body=None)
    return openai.APIStatusError("err", response=resp, body=None)


def conn_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=_req())


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_req())


def _resp(text: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeOpenAI:
    """script: 차례대로 돌려줄 문자열 또는 던질 예외."""

    instances: list["FakeOpenAI"] = []
    script: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        FakeOpenAI.instances.append(self)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not FakeOpenAI.script:
            raise AssertionError("준비된 응답이 없음")
        item = FakeOpenAI.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _resp(item)


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAW_AGENT_MOCK", raising=False)
    monkeypatch.delenv("LAW_AGENT_MOCK_DIR", raising=False)
    monkeypatch.setattr(C, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(C, "env", lambda name, section="llm": ENV_VALUES[name])
    monkeypatch.setattr(C, "OpenAI", FakeOpenAI)
    sleeps: list[float] = []
    monkeypatch.setattr(C.time, "sleep", lambda s: sleeps.append(s))
    FakeOpenAI.instances = []
    FakeOpenAI.script = []
    C.reset_client()
    yield sleeps
    C.reset_client()


def calls() -> list[dict]:
    return [c for inst in FakeOpenAI.instances for c in inst.calls]


# ---------------------------------------------------------------- mock

def _write_mock(dirpath, model: str, qid: str, content: str):
    p = dirpath / f"{safe_name(model)}_{safe_name(qid)}.json"
    p.write_text(content, encoding="utf-8")
    return p


def test_mock_reads_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LAW_AGENT_MOCK", "1")
    monkeypatch.setenv("LAW_AGENT_MOCK_DIR", str(tmp_path))
    content = '{"answer": "yes", "quotes": []}\n'
    _write_mock(tmp_path, "m-a", "A-1", content)
    counter = C.CallCounter(limit=5)
    r = C.chat_json("m-a", "sys", "user", question_id="A-1", counter=counter)
    assert r.data == {"answer": "yes", "quotes": []}
    assert r.raw == content
    assert r.model == "m-a" and r.attempts == 1
    assert counter.count == 1
    assert FakeOpenAI.instances == []  # 네트워크 클라이언트를 만들지 않음


def test_mock_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LAW_AGENT_MOCK", "1")
    monkeypatch.setenv("LAW_AGENT_MOCK_DIR", str(tmp_path))
    with pytest.raises(C.LLMError, match="mock 응답 없음"):
        C.chat_json("m-a", "sys", "user", question_id="없는질문")


def test_mock_counts_toward_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("LAW_AGENT_MOCK", "1")
    monkeypatch.setenv("LAW_AGENT_MOCK_DIR", str(tmp_path))
    _write_mock(tmp_path, "m-a", "A-1", "{}")
    counter = C.CallCounter(limit=1)
    C.chat_json("m-a", "s", "u", question_id="A-1", counter=counter)
    with pytest.raises(C.BudgetExceeded):
        C.chat_json("m-a", "s", "u", question_id="A-1", counter=counter)


# ---------------------------------------------------------------- 호출 상한

def test_counter_limit_from_settings():
    counter = C.CallCounter()
    assert counter.limit == 3
    for _ in range(3):
        counter.add()
    assert counter.count == 3
    with pytest.raises(C.BudgetExceeded):
        counter.add()
    assert counter.count == 3


def test_budget_exceeded_during_retries():
    # 상한 2: 첫 요청 5xx → 재시도 1회 → 세 번째에서 상한 초과
    FakeOpenAI.script = [status_error(503), status_error(503), '{"a": 1}']
    counter = C.CallCounter(limit=2)
    with pytest.raises(C.BudgetExceeded):
        C.chat_json("m-a", "s", "u", question_id="q", counter=counter)
    assert counter.count == 2
    assert len(calls()) == 2


# ---------------------------------------------------------------- JSON 파싱

@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1, "b": [1, 2]}\n```', {"a": 1, "b": [1, 2]}),
        ('```\n{"a": "x"}\n```', {"a": "x"}),
        ('답은 다음과 같습니다.\n{"answer": "no", "reason": "a}b"}\n이상입니다. {추가}', {"answer": "no", "reason": "a}b"}),
        ('설명\n```json\n{"k": {"n": 2}}\n```\n끝', {"k": {"n": 2}}),
    ],
)
def test_parse_json_text(text, expected):
    assert C.parse_json_text(text) == expected


@pytest.mark.parametrize("text", ["", "JSON 없음", "[1, 2]", '{"a": ', "```json\n[1]\n```"])
def test_parse_json_text_fail(text):
    with pytest.raises(ValueError):
        C.parse_json_text(text)


def test_chat_json_parses_mixed_text():
    FakeOpenAI.script = ['다음이 답이다:\n```json\n{"answer": "yes"}\n```\n참고하라.']
    r = C.chat_json("m-a", "s", "u", question_id="q")
    assert r.data == {"answer": "yes"} and r.attempts == 1
    c = calls()[0]
    assert c["temperature"] == 0
    assert c["response_format"] == {"type": "json_object"}
    assert "tool_choice" not in c


def test_parse_failure_then_rerequest_succeeds():
    FakeOpenAI.script = ["모르겠습니다", '{"answer": "unknown"}']
    counter = C.CallCounter(limit=10)
    r = C.chat_json("m-a", "s", "u", question_id="q", counter=counter)
    assert r.data == {"answer": "unknown"}
    assert r.attempts == 2 and counter.count == 2
    second = calls()[1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "모르겠습니다"}
    assert "JSON 객체 하나만 출력하라" in second[-1]["content"]


def test_parse_failure_twice_raises():
    FakeOpenAI.script = ["아니요", "여전히 아니요"]
    with pytest.raises(C.LLMParseError):
        C.chat_json("m-a", "s", "u", question_id="q")
    assert len(calls()) == 2


def test_schema_added_to_system():
    FakeOpenAI.script = ['{"answer": "yes"}']
    schema = {"type": "object", "required": ["answer"]}
    C.chat_json("m-a", "기본 지시", "u", question_id="q", schema=schema)
    sys_msg = calls()[0]["messages"][0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"].startswith("기본 지시")
    assert '"required": ["answer"]' in sys_msg["content"]


# ---------------------------------------------------------------- response_format 거부

def test_response_format_rejected_then_retry_without():
    FakeOpenAI.script = [bad_request(), '{"ok": true}']
    counter = C.CallCounter(limit=10)
    r = C.chat_json("m-a", "s", "u", question_id="q", counter=counter)
    assert r.data == {"ok": True}
    assert r.attempts == 2 and counter.count == 2
    assert "response_format" in calls()[0]
    assert "response_format" not in calls()[1]


def test_bad_request_without_json_mode_raises():
    FakeOpenAI.script = [bad_request(), bad_request()]
    with pytest.raises(C.LLMError) as ei:
        C.chat_json("m-a", "s", "u", question_id="q")
    assert not isinstance(ei.value, C.LLMParseError)
    assert len(calls()) == 2


# ---------------------------------------------------------------- 재시도 백오프

FakeOpenAI_last_err: list = [None]

@pytest.mark.parametrize("make_err", [conn_error, timeout_error, lambda: status_error(429), lambda: status_error(502)])
def test_retry_backoff_then_success(_setup, make_err):
    sleeps = _setup
    FakeOpenAI.script = [make_err(), make_err(), '{"x": 1}']
    FakeOpenAI_last_err[0] = FakeOpenAI.script[0]
    counter = C.CallCounter(limit=10)
    r = C.chat_json("m-a", "s", "u", question_id="q", counter=counter)
    assert r.data == {"x": 1}
    assert sleeps == ([5.0, 10.0] if isinstance(FakeOpenAI_last_err[0], openai.RateLimitError) else [1.0, 2.0])
    assert r.attempts == 3 and counter.count == 3


def test_retry_exhausted_raises(_setup):
    sleeps = _setup
    FakeOpenAI.script = [status_error(500), status_error(500), status_error(500), '{"x": 1}']
    with pytest.raises(C.LLMError):
        C.chat_json("m-a", "s", "u", question_id="q")
    assert sleeps == [1.0, 2.0]
    assert len(calls()) == 3


def test_no_retry_on_client_error(_setup):
    sleeps = _setup
    FakeOpenAI.script = [status_error(401)]
    with pytest.raises(C.LLMError):
        C.chat_json("m-a", "s", "u", question_id="q")
    assert sleeps == [] and len(calls()) == 1


# ---------------------------------------------------------------- 클라이언트 생성·로그

def test_client_created_once_with_settings():
    FakeOpenAI.script = ['{"a": 1}', '{"a": 2}']
    C.chat_json("m-a", "s", "u", question_id="q1")
    C.chat_json("m-b", "s", "u", question_id="q2")
    assert len(FakeOpenAI.instances) == 1
    kw = FakeOpenAI.instances[0].init_kwargs
    assert kw["base_url"] == SECRET_URL and kw["api_key"] == SECRET_KEY
    assert kw["timeout"] == 5.0 and kw["max_retries"] == 0


def test_missing_env_becomes_llm_error(monkeypatch):
    # 진짜 settings.env 를 쓰고, 키 환경변수만 비운다
    from law_agent import settings as S
    monkeypatch.setattr(C, "env", lambda name, section="data": S.env(name, SETTINGS, section=section))
    monkeypatch.setenv("TEST_LLM_BASE_URL", SECRET_URL)
    monkeypatch.delenv("TEST_LLM_API_KEY", raising=False)
    with pytest.raises(C.LLMError, match="TEST_LLM_API_KEY") as ei:
        C.chat_json("m-a", "s", "u", question_id="q")
    assert SECRET_URL not in str(ei.value)
    assert FakeOpenAI.instances == []


def test_secrets_not_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    prompt_sys = "비밀시스템프롬프트본문"
    prompt_user = "비밀사용자프롬프트본문"
    FakeOpenAI.script = [bad_request(), "설명뿐", '{"a": 1}']
    C.chat_json("m-a", prompt_sys, prompt_user, question_id="Q-LOG")
    FakeOpenAI.script = [status_error(500)] * 3
    with pytest.raises(C.LLMError) as ei:
        C.chat_json("m-a", prompt_sys, prompt_user, question_id="Q-FAIL")

    text = caplog.text + str(ei.value)
    for secret in (SECRET_KEY, SECRET_URL, "secret-llm-host", prompt_sys, prompt_user):
        assert secret not in text
    assert "Q-LOG" in caplog.text and "m-a" in caplog.text
    rec = [r for r in caplog.records if "Q-LOG" in r.getMessage()][0]
    assert "attempts=3" in rec.getMessage()



# ---------------------------------------------------------------- 보강 (리뷰 후 추가)

@pytest.mark.parametrize("content", ["{깨짐", "[1, 2]", ""])
def test_mock_bad_json_is_llm_error(tmp_path, monkeypatch, content):
    monkeypatch.setenv("LAW_AGENT_MOCK", "1")
    monkeypatch.setenv("LAW_AGENT_MOCK_DIR", str(tmp_path))
    _write_mock(tmp_path, "m-a", "A-1", content)
    with pytest.raises(C.LLMParseError):
        C.chat_json("m-a", "s", "u", question_id="A-1")


def test_empty_choices_then_rerequest(monkeypatch):
    seq = [SimpleNamespace(choices=[]), SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
           ]
    FakeOpenAI.script = ["무시", "무시"]
    monkeypatch.setattr(FakeOpenAI, "_create",
                        lambda self, **kw: (self.calls.append(kw), seq.pop(0))[1])
    with pytest.raises(C.LLMParseError):
        C.chat_json("m-a", "s", "u", question_id="q")
    assert len(calls()) == 2


def test_json_mode_rejection_remembered_per_model():
    # 첫 요청에서 json 모드를 거부하면, 파싱 재요청과 다음 질문에서는 처음부터 빼고 보낸다
    FakeOpenAI.script = [bad_request(), "설명뿐", '{"a": 1}', '{"b": 2}', '{"c": 3}']
    counter = C.CallCounter(limit=10)
    r = C.chat_json("m-a", "s", "u", question_id="q1", counter=counter)
    assert r.data == {"a": 1} and r.attempts == 3 and counter.count == 3
    C.chat_json("m-a", "s", "u", question_id="q2", counter=counter)
    assert "response_format" not in calls()[3]
    C.chat_json("m-b", "s", "u", question_id="q3", counter=counter)  # 다른 모델은 다시 시도
    assert "response_format" in calls()[4]


# ---------------------------------------------------------------- 토큰 사용량

def _with_usage(monkeypatch, usages: list[Any]) -> None:
    """가짜 응답에 usage 를 차례로 붙인다 (None 이면 usage 없음)."""
    def fake_resp(text: str) -> Any:
        r = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        u = usages.pop(0)
        if u is not None:
            r.usage = u
        return r
    monkeypatch.setattr(sys.modules[__name__], "_resp", fake_resp)


def test_usage_summed_across_parse_retry(monkeypatch):
    _with_usage(monkeypatch, [
        SimpleNamespace(prompt_tokens=100, completion_tokens=10, total_tokens=110),
        {"prompt_tokens": 120, "completion_tokens": 5, "total_tokens": 125},  # dict 로 줘도 받는다
    ])
    FakeOpenAI.script = ["설명뿐", '{"a": 1}']
    r = C.chat_json("m-a", "s", "u", question_id="q")
    assert r.usage == {"prompt_tokens": 220, "completion_tokens": 15, "total_tokens": 235}


def test_usage_partial_and_bad_values(monkeypatch):
    _with_usage(monkeypatch, [SimpleNamespace(prompt_tokens=7, completion_tokens=None, total_tokens=True)])
    FakeOpenAI.script = ['{"a": 1}']
    assert C.chat_json("m-a", "s", "u", question_id="q").usage == {"prompt_tokens": 7}


def test_usage_empty_when_missing_or_mock(tmp_path, monkeypatch):
    FakeOpenAI.script = ['{"a": 1}']
    assert C.chat_json("m-a", "s", "u", question_id="q").usage == {}
    monkeypatch.setenv("LAW_AGENT_MOCK", "1")
    monkeypatch.setenv("LAW_AGENT_MOCK_DIR", str(tmp_path))
    _write_mock(tmp_path, "m-a", "A-1", "{}")
    assert C.chat_json("m-a", "s", "u", question_id="A-1").usage == {}

# ---------------------------------------------------------------- 직접 API (2026.11.18)

class _R:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, ""

    def json(self):
        return self._d


def test_direct_anthropic(monkeypatch):
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return _R(200, {"content": [{"type": "text", "text": '{"answer": "yes"}'}],
                        "usage": {"input_tokens": 10, "output_tokens": 2}})
    monkeypatch.setattr(C.httpx, "post", post)
    r = C.chat_json("anthropic/claude-x", "sys", "u", question_id="q")
    assert r.data == {"answer": "yes"} and r.usage["total_tokens"] == 12
    assert seen["url"].endswith("/api/v2/chat/direct/anthropic/v1/messages")
    assert seen["body"]["model"] == "claude-x" and seen["body"]["system"] == "sys"
    assert seen["headers"]["x-api-key"] == SECRET_KEY


def test_direct_google_and_retry(monkeypatch, _setup):
    script = [_R(503, {}), _R(200, {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}],
                                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1,
                                                      "totalTokenCount": 6}})]
    urls = []

    def post(url, headers, json, timeout):
        urls.append(url)
        return script.pop(0)
    monkeypatch.setattr(C.httpx, "post", post)
    r = C.chat_json("google/gemini-y", "s", "u", question_id="q")
    assert r.data == {"a": 1} and r.attempts == 2 and r.usage["prompt_tokens"] == 5
    assert urls[0].endswith("/google/v1beta/models/gemini-y:generateContent")


def test_direct_410_is_llm_error(monkeypatch):
    monkeypatch.setattr(C.httpx, "post", lambda url, headers, json, timeout: _R(410, {}))
    with pytest.raises(C.LLMError, match="410"):
        C.chat_json("anthropic/claude-x", "s", "u", question_id="q")
