"""타임리(Timely) 직접 API 에 JSON 답을 요청하는 클라이언트.

2026.11.18: 예전 OpenAI 호환 주소(브리지)가 종료(HTTP 410 endpoint_retired) → 회사별 직접 주소로.
  모델 이름 "회사/모델" 의 회사로 주소를 고른다 (이름 앞부분은 떼고 보냄):
    openai/…    → {host}/api/v2/chat/direct/openai/v1   (OpenAI SDK, chat.completions)
    anthropic/… → {host}/api/v2/chat/direct/anthropic   (POST /v1/messages, x-api-key)
    google/…    → {host}/api/v2/chat/direct/google      (POST /v1beta/models/{m}:generateContent, x-goog-api-key)
  host = settings llm.direct_host (기본 https://hello.timelyai.io). 키는 같은 TIMELY_API_KEY.
  회사 앞부분이 없는 이름은 예전처럼 base_url_env 주소의 OpenAI SDK (테스트용).

인증값은 환경변수에서만 읽고 코드·로그에 남기지 않는다.
로그에는 model, question_id, attempts, seconds 만 남긴다.
LAW_AGENT_MOCK=1 이면 네트워크 없이 mock 파일을 읽는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import openai
from openai import OpenAI

from law_agent.settings import env, load_settings
from law_agent.state import safe_name

logger = logging.getLogger("law_agent.llm")

BACKOFF_SECONDS = (1.0, 2.0)
RATE_BACKOFF_SECONDS = (5.0, 10.0, 20.0, 40.0)   # 429(요청 한도) 는 더 길게 기다림 (2026.11.18 혼합물 사례에서 3회 만에 실패)
JSON_ONLY_MSG = "JSON 객체 하나만 출력하라."
_DEFAULT_MOCK_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "mock_llm"
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class JsonReply:
    data: dict
    raw: str
    model: str
    attempts: int
    seconds: float
    # 토큰 사용량 합계 (파싱 재요청까지 더함). 서버가 안 주거나 mock 이면 {}
    usage: dict[str, int] = field(default_factory=dict)


USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _usage_of(resp: object) -> dict[str, int]:
    """응답의 usage → {prompt_tokens, completion_tokens, total_tokens} 중 정수인 것만."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out: dict[str, int] = {}
    for k in USAGE_FIELDS:
        v = u.get(k) if isinstance(u, dict) else getattr(u, k, None)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out


def _add_usage(total: dict[str, int], part: dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + v


class LLMError(Exception):
    """LLM 호출 실패."""


class LLMParseError(LLMError):
    """재요청 후에도 응답에서 JSON 객체를 꺼내지 못함."""


class BudgetExceeded(LLMError):
    """사례당 LLM 호출 상한 초과."""


class CallCounter:
    """사례 하나당 하나. 실제 요청 직전에 add() (judge 병렬 호출 대비 잠금)."""

    def __init__(self, limit: int | None = None) -> None:
        if limit is None:
            limit = int(load_settings()["limits"]["max_llm_calls_per_case"])
        self.limit = limit
        self.count = 0
        self._lock = threading.Lock()

    def add(self) -> None:
        with self._lock:
            if self.count >= self.limit:
                raise BudgetExceeded(f"사례당 LLM 호출 상한({self.limit}회) 초과")
            self.count += 1


_client: OpenAI | None = None
_direct_openai: OpenAI | None = None
_client_lock = threading.Lock()
DEFAULT_HOST = "https://hello.timelyai.io"
VENDORS = ("openai", "anthropic", "google")


def split_model(model: str) -> tuple[str | None, str]:
    """"anthropic/claude-sonnet-5.5" → ("anthropic", "claude-sonnet-5.5"). 회사가 아니면 (None, model)."""
    v, _, name = model.partition("/")
    return (v, name) if name and v in VENDORS else (None, model)


def _host() -> str:
    return str((load_settings().get("llm") or {}).get("direct_host") or DEFAULT_HOST).rstrip("/")


def _api_key() -> str:
    try:
        return env("api_key_env", section="llm")
    except (KeyError, RuntimeError) as e:
        raise LLMError(f"LLM 설정 오류: {e}") from None


def _timeout() -> float:
    return float((load_settings().get("llm") or {}).get("timeout_seconds") or 60)


def _get_direct_openai() -> OpenAI:
    global _direct_openai
    with _client_lock:
        if _direct_openai is None:
            _direct_openai = OpenAI(base_url=f"{_host()}/api/v2/chat/direct/openai/v1", api_key=_api_key(),
                                    timeout=_timeout(), max_retries=0)
        return _direct_openai
# response_format(json 모드)를 거부한 모델. 한 번 거부되면 다음 요청부터 빼서 호출 수를 아낀다
_no_json_mode: set[str] = set()


def _get_client() -> OpenAI:
    global _client
    with _client_lock:  # judge 병렬 호출이 동시에 만들지 않게
        if _client is not None:
            return _client
        cfg = load_settings().get("llm") or {}
        try:
            base_url = env("base_url_env", section="llm")
            api_key = env("api_key_env", section="llm")
        except (KeyError, RuntimeError) as e:
            # settings.env 메시지에는 환경변수 "이름"만 있다
            raise LLMError(f"LLM 설정 오류: {e}") from None
        _client = OpenAI(base_url=base_url, api_key=api_key,
                         timeout=float(cfg.get("timeout_seconds") or 60),
                         max_retries=0)  # 재시도는 여기서 직접 해서 호출 수를 정확히 센다
        return _client


def reset_client() -> None:
    """테스트용: 만들어 둔 클라이언트를 지운다."""
    global _client, _direct_openai
    with _client_lock:
        _client = None
        _direct_openai = None
    _no_json_mode.clear()


def parse_json_text(text: str) -> dict:
    """```json 울타리 제거 → 첫 "{" 부터 raw_decode. 실패하면 ValueError."""
    m = _FENCE_RE.search(text)
    body = m.group(1) if m else text
    start = body.find("{")
    if start < 0:
        raise ValueError("응답에 '{' 가 없음")
    try:
        obj, _ = json.JSONDecoder().raw_decode(body, start)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 해석 실패: {e.msg}") from None
    if not isinstance(obj, dict):
        raise ValueError("JSON 객체가 아님")
    return obj


def _retriable(e: Exception) -> bool:
    """네트워크 오류·타임아웃·429·5xx."""
    return isinstance(e, (openai.APIConnectionError, openai.RateLimitError)) or (
        isinstance(e, openai.APIStatusError) and e.status_code >= 500)


class _HTTPStatus(Exception):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.status_code = code


def _post(url: str, headers: dict, body: dict) -> dict:
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=_timeout())
    except httpx.HTTPError as e:                        # 연결·타임아웃 → 재시도 대상
        raise ConnectionError(type(e).__name__) from None
    if r.status_code >= 400:
        e = _HTTPStatus(r.status_code)
        e.args = (f"HTTP {r.status_code} {r.text[:200]}",)
        raise e
    return r.json()


def _anthropic(name: str, messages: list[dict], temperature: float) -> tuple[str, dict]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    max_tokens = int((load_settings().get("llm") or {}).get("max_output_tokens") or 4096)
    d = _post(f"{_host()}/api/v2/chat/direct/anthropic/v1/messages",
              {"x-api-key": _api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
              {"model": name, "max_tokens": max_tokens, "system": system, "messages": msgs})
    # temperature 는 보내지 않음: 새 claude 모델은 "deprecated" 로 400 (2026.11.18)
    text = "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
    u = d.get("usage") or {}
    pt, ct = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    return text, ({"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct} if u else {})


def _google(name: str, messages: list[dict], temperature: float) -> tuple[str, dict]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages if m["role"] != "system"]
    d = _post(f"{_host()}/api/v2/chat/direct/google/v1beta/models/{name}:generateContent",
              {"x-goog-api-key": _api_key(), "content-type": "application/json"},
              {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
               "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}})
    cands = d.get("candidates") or []
    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    u = d.get("usageMetadata") or {}
    out = {k2: int(u[k1]) for k1, k2 in (("promptTokenCount", "prompt_tokens"),
                                          ("candidatesTokenCount", "completion_tokens"),
                                          ("totalTokenCount", "total_tokens")) if isinstance(u.get(k1), int)}
    return text, out


def _request(model: str, messages: list[dict], counter: CallCounter | None, tries: list[int],
             usage: dict[str, int]) -> str:
    """요청 1건 (회사별 주소로). 일시 오류(연결·429·5xx)는 백오프 재시도. 응답 텍스트를 돌려준다."""
    vendor, name = split_model(model)
    if vendor in (None, "openai"):
        return _request_openai(model if vendor is None else name, messages, counter, tries, usage,
                               _get_client() if vendor is None else _get_direct_openai(),
                               send_temperature=vendor is None)
    temperature = (load_settings().get("llm") or {}).get("temperature", 0)
    fn = _anthropic if vendor == "anthropic" else _google
    retries = 0
    while True:
        if counter is not None:
            counter.add()
        tries[0] += 1
        try:
            text, u = fn(name, messages, temperature)
            _add_usage(usage, u)
            return text
        except (ConnectionError, _HTTPStatus) as e:
            code = getattr(e, "status_code", None)
            waits = RATE_BACKOFF_SECONDS if code == 429 else BACKOFF_SECONDS
            if (code is None or code == 429 or code >= 500) and retries < len(waits):
                time.sleep(waits[retries])
                retries += 1
                continue
            raise LLMError(f"LLM 호출 실패: {vendor} ({e})") from e


def _request_openai(model: str, messages: list[dict], counter: CallCounter | None, tries: list[int],
                    usage: dict[str, int], client: OpenAI, send_temperature: bool = True) -> str:
    """OpenAI SDK 요청 1건. json 모드 거부 시 빼고 재호출, 일시 오류는 백오프 재시도. 응답 텍스트를 돌려준다.
    받은 응답의 토큰 사용량은 usage 에 더한다."""
    temperature = (load_settings().get("llm") or {}).get("temperature", 0)
    json_mode, retries = model not in _no_json_mode, 0
    while True:
        if counter is not None:
            counter.add()
        tries[0] += 1
        kwargs: dict = {"model": model, "messages": messages}
        if send_temperature:      # 직접 API 의 gpt 는 temperature 0 을 거부(기본값 1만) → 안 보냄 (2026.11.18)
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
            _add_usage(usage, _usage_of(resp))
            # choices 가 비었거나 content 가 없으면 빈 문자열 → chat_json 이 파싱 실패로 보고 1회 재요청
            choices = getattr(resp, "choices", None) or []
            message = getattr(choices[0], "message", None) if choices else None
            return (getattr(message, "content", None) or "") if message is not None else ""
        except openai.BadRequestError as e:
            if json_mode:
                json_mode = False
                _no_json_mode.add(model)
                continue
            raise LLMError("LLM 요청 거부 (HTTP 400)") from e
        except openai.OpenAIError as e:
            waits = RATE_BACKOFF_SECONDS if isinstance(e, openai.RateLimitError) else BACKOFF_SECONDS
            if _retriable(e) and retries < len(waits):
                time.sleep(waits[retries])
                retries += 1
                continue
            code = getattr(e, "status_code", None)
            raise LLMError(f"LLM 호출 실패: {type(e).__name__}" + (f" (HTTP {code})" if code else "")) from e


def _mock(model: str, question_id: str, counter: CallCounter | None) -> tuple[dict, str]:
    if counter is not None:
        counter.add()
    path = Path(os.environ.get("LAW_AGENT_MOCK_DIR") or _DEFAULT_MOCK_DIR) / \
        f"{safe_name(model)}_{safe_name(question_id)}.json"
    if not path.is_file():
        raise LLMError(f"mock 응답 없음: {path}")
    raw = path.read_text(encoding="utf-8")
    try:
        return parse_json_text(raw), raw
    except ValueError as e:
        # 실제 호출과 같이 LLMError 계열로 (judge 가 LLMError 만 잡아도 되게)
        raise LLMParseError(f"mock 응답이 JSON 객체가 아님: {path.name}: {e}") from None


def chat_json(model: str, system: str, user: str, *, question_id: str,
              counter: CallCounter | None = None, schema: dict | None = None) -> JsonReply:
    """JSON 객체 답을 받는다. schema 는 system 끝에 덧붙인다 (서버가 강제 기능 미지원)."""
    t0 = time.monotonic()
    tries = [0]
    usage: dict[str, int] = {}
    try:
        if os.environ.get("LAW_AGENT_MOCK") == "1":
            tries[0] = 1
            data, raw = _mock(model, question_id, counter)
        else:
            if schema:
                system += "\n\n아래 JSON 스키마를 따르는 JSON 객체 하나만 출력하라.\n" + \
                    json.dumps(schema, ensure_ascii=False)
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            raw = _request(model, messages, counter, tries, usage)
            try:
                data = parse_json_text(raw)
            except ValueError:
                messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": JSON_ONLY_MSG}]
                raw = _request(model, messages, counter, tries, usage)
                try:
                    data = parse_json_text(raw)
                except ValueError as e:
                    raise LLMParseError(f"JSON 파싱 실패 (재요청 후): {e}") from None
    except LLMError as e:
        logger.warning("LLM 실패 model=%s question_id=%s attempts=%d seconds=%.3f reason=%s %s",
                       model, question_id, tries[0], time.monotonic() - t0, type(e).__name__, str(e)[:160])
        raise
    seconds = time.monotonic() - t0
    logger.info("LLM 호출 model=%s question_id=%s attempts=%d seconds=%.3f",
                model, question_id, tries[0], seconds)
    return JsonReply(data=data, raw=raw, model=model, attempts=tries[0], seconds=seconds, usage=usage)