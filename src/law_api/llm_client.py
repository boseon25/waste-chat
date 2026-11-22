"""llm_client.py — 판정 표 초안용 LLM 호출 모듈 (Anthropic Messages API)

다른 스크립트는 이 파일의 extract_rows() 하나만 쓴다.
- 출력 형식: 도구(tool) 호출을 강제해서 JSON 스키마에 맞는 행 목록만 받는다.
- 비용: 시스템 프롬프트·스키마·퓨샷은 프롬프트 캐시(cache_control)로 재사용.
- 재현성: temperature 0, 응답 원문은 호출한 쪽이 raw 로그로 남긴다.
- 테스트: 환경변수 LLM_MOCK=1 이면 API 를 부르지 않고 mock_fn 결과를 쓴다.

준비 (둘 중 하나, 환경변수 LLM_PROVIDER 로 고름. 키 값은 코드·문서에 쓰지 않음)
  anthropic (기본): uv add anthropic / ANTHROPIC_API_KEY
  timely          : uv add openai    / TIMELY_API_KEY, TIMELY_BASE_URL (타임리 AI "OpenAI 호환 주소")
                    모델 이름은 "anthropic/claude-sonnet-5.5" 형식 (MODELS_TIMELY)
"""
import os, time, json

MODELS = {  # 표별 기본 모델. 환경변수 LLM_MODEL_<표이름대문자> 로 바꿀 수 있음
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}

MODELS_TIMELY = {  # 타임리 OpenAI 호환 모드의 모델 이름 (models.list() 로 확인한 값)
    "opus": "anthropic/claude-opus-5.5",
    "sonnet": "anthropic/claude-sonnet-5.5",
    "haiku": "anthropic/claude-haiku-5.5",
}


def provider():
    return (os.environ.get("LLM_PROVIDER") or "anthropic").lower()


_client = None


def _get_client():
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("환경변수 ANTHROPIC_API_KEY 가 없습니다. (PowerShell: $env:ANTHROPIC_API_KEY=\"...\")")
        import anthropic  # uv add anthropic
        _client = anthropic.Anthropic()
    return _client


def resolve_model(table, default):
    v = os.environ.get(f"LLM_MODEL_{table.upper()}") or default
    table_map = MODELS_TIMELY if provider() == "timely" else MODELS
    return table_map.get(v, v)


def _get_timely():
    global _client
    if _client is None:
        for k in ("TIMELY_API_KEY", "TIMELY_BASE_URL"):
            if not os.environ.get(k):
                raise SystemExit(f"환경변수 {k} 가 없습니다. (타임리 AI 설정 > 연동 키 관리)")
        from openai import OpenAI  # uv add openai
        _client = OpenAI(api_key=os.environ["TIMELY_API_KEY"], base_url=os.environ["TIMELY_BASE_URL"])
    return _client


_FORCE_TOOL = {}   # 모델별: 함수 호출 강제(tool_choice 지정)를 받는지. 거부하면 auto로 바꿔 계속 씀


def _parse_text_rows(txt):
    """함수 호출 없이 글로 준 JSON에서 rows를 꺼낸다 (```json … ``` 감싸기 허용)."""
    t = (txt or "").strip()
    if "```" in t:
        t = t.split("```")[1]
        t = t[4:] if t.startswith("json") else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        return list(json.loads(t[i:j + 1]).get("rows", []))
    except (ValueError, AttributeError):
        return None


def _extract_timely(*, model, system, tool, user, max_tokens, retries):
    """OpenAI 호환 주소로 같은 일: 함수 호출(submit_rows)로 JSON 행 목록만 받는다.
    일부 모델은 함수 호출 강제(tool_choice 지정)를 거부한다 → auto로 바꾸고 "반드시 submit_rows로 제출" 지시를 붙인다.
    그래도 글로 답하면 글 속 JSON을 읽는다. 프롬프트 캐시는 쓰지 않는다 (같은 청크는 draft_tables 캐시가 막음)."""
    client = _get_timely()
    fn = {"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                           "parameters": tool["input_schema"]}}
    sys_auto = system + f"\n\n결과는 반드시 {tool['name']} 도구를 한 번 호출해 rows 배열로 제출한다. 다른 글은 쓰지 않는다."
    last = None
    attempt = 0
    while attempt < retries:
        force = _FORCE_TOOL.get(model, True)
        kw = dict(model=model, max_tokens=max_tokens, temperature=0, tools=[fn],
                  messages=[{"role": "system", "content": system if force else sys_auto},
                            {"role": "user", "content": user}])
        kw["tool_choice"] = {"type": "function", "function": {"name": tool["name"]}} if force else "auto"
        try:
            resp = client.chat.completions.create(**kw)
        except Exception as e:
            if force and "tool_choice" in str(e):          # 강제 거부 → 바로 auto로 다시 (재시도 횟수 안 씀)
                _FORCE_TOOL[model] = False
                continue
            last = e
            attempt += 1
            time.sleep(2 ** attempt * 3)
            continue
        ch = resp.choices[0]
        u = resp.usage
        meta = {"model": model, "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(u, "completion_tokens", 0) or 0, "cache_read": 0, "cache_write": 0,
                "stop_reason": f"{ch.finish_reason}{'' if force else ' (tool_choice auto)'}"}
        for tc in ch.message.tool_calls or []:
            if tc.function.name == tool["name"]:
                return list(json.loads(tc.function.arguments or "{}").get("rows", [])), meta
        rows = _parse_text_rows(ch.message.content)
        if rows is not None:
            return rows, {**meta, "stop_reason": meta["stop_reason"] + " / text_json"}
        return [], {**meta, "stop_reason": meta["stop_reason"] + " / no_tool_call"}
    raise RuntimeError(f"LLM 호출 실패 (timely): {last}")


def extract_rows(*, model, system, schema, user, fewshot=None, max_tokens=8000, retries=3, mock_fn=None):
    """원문 청크 하나 → 스키마에 맞는 행 목록.
    system: 규칙 설명(고정) / schema: 한 행의 JSON 스키마 / user: 이번 청크 / fewshot: [(입력, 출력행목록), ...]
    반환: (rows, meta)  meta = {model, input_tokens, output_tokens, cache_read, cache_write, stop_reason}
    """
    if os.environ.get("LLM_MOCK") == "1":
        rows = mock_fn(user) if mock_fn else []
        return rows, {"model": "mock", "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0, "stop_reason": "mock"}

    tool = {
        "name": "submit_rows",
        "description": "원문에서 뽑은 표의 행 목록을 제출한다. 원문에 근거가 없으면 행을 만들지 않는다.",
        "input_schema": {"type": "object", "properties": {"rows": {"type": "array", "items": schema}}, "required": ["rows"]},
    }
    shots = ""
    if fewshot:
        parts = []
        for i, (inp, out) in enumerate(fewshot, 1):
            parts.append(f"<example id=\"{i}\">\n<source>\n{inp}\n</source>\n<rows>\n{json.dumps(out, ensure_ascii=False, indent=1)}\n</rows>\n</example>")
        shots = "\n\n<examples>\n" + "\n\n".join(parts) + "\n</examples>"
    if provider() == "timely":
        return _extract_timely(model=model, system=system + shots, tool=tool, user=user,
                               max_tokens=max_tokens, retries=retries)
    system_blocks = [{"type": "text", "text": system + shots, "cache_control": {"type": "ephemeral"}}]

    client = _get_client()
    last = None
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, temperature=0,
                system=system_blocks, tools=[tool], tool_choice={"type": "tool", "name": "submit_rows"},
                messages=[{"role": "user", "content": user}],
            )
            u = resp.usage
            meta = {"model": model, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                    "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                    "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
                    "stop_reason": resp.stop_reason}
            for b in resp.content:
                if b.type == "tool_use":
                    return list(b.input.get("rows", [])), meta
            return [], meta
        except Exception as e:  # 과부하·네트워크 → 지수 대기 후 재시도
            last = e
            time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"LLM 호출 실패: {last}")
