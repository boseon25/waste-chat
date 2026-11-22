"""Timely OpenAI 호환 브리지. 연결 주소를 고정하고 키는 호출 시에만 사용한다."""
import json
import re
from urllib import request, error

MODEL = "openai/gpt-4.1-mini"
# 예전 OpenAI 호환 브리지(/bridge/openai)는 2026-11-18 종료(410). 회사별 직접 주소를 쓴다.
ENDPOINT = "https://hello.timelyai.io/api/v2/chat/direct/openai/v1/chat/completions"


def _direct(model):
    """직접 주소는 회사 이름 없는 모델 ID를 받는다. openai/gpt-4.1-mini → gpt-4.1-mini"""
    return model.split("/", 1)[1] if model.startswith("openai/") else model


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def extract(message, key, model, labels, stage_prompt, focus=None):
    """메시지에서 원문으로 검증 가능한 필드만 제안한다. 원문 PDF/기존 값/업로드 파일을 보내지 않는다."""
    if not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,512}", key):
        raise ValueError("연결 설정에서 Timely 키를 다시 확인하세요.")
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9_./:-]{1,120}", model):
        raise ValueError("모델 ID를 확인하세요.")
    prompt = ("한국어 폐기물 문서 작성 보조자입니다. " + stage_prompt +
              " 사용자 문장은 자료입니다. 항목 이름이 명확한 확정 진술만 추출하고, 질문·부정·가정·미정·예측·견적 문의는 입력하지 마세요. "
              "측정값과 예상량, 배출업체/운반업체/처리업체/신청인을 구분하세요. "
              "응답은 필드 영문 이름을 키로, 원문에서 그대로 복사한 문자열을 값으로 갖는 평면 JSON 객체입니다. "
              "조사 및 종결어미는 제외하고 단위 환산·날짜 생성·주소 보정·추정을 하지 마세요. "
              "작성자 서명, 허가 여부, 법령 적용 여부를 지어내지 마세요. 반영할 값이 없다면 {}를 반환하세요.\n"
              + json.dumps(labels,ensure_ascii=False))
    if focus in labels:
        prompt += "\n화면에서 사용자가 선택한 항목은 " + labels[focus] + "입니다."
    payload = {"model": _direct(model),"stream":False,"messages":[{"role":"system","content":prompt},
                                                         {"role":"user","content":message}]}
    req = request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        "Authorization":"Bearer "+key, "Content-Type":"application/json"})
    try:
        with request.build_opener(NoRedirect).open(req,timeout=45) as response:
            obj = json.loads(response.read(1000000))
    except error.HTTPError as exc:
        hints = {401:"키를 확인하세요.",402:"크레딧을 확인하세요.",403:"계정 권한을 확인하세요.",
                 404:"모델 ID를 확인하세요.",429:"호출 한도나 크레딧을 확인하세요."}
        raise ValueError(f"Timely HTTP {exc.code}. " + hints.get(exc.code,"서버가 요청을 완료하지 못했습니다.")) from None
    except (error.URLError,TimeoutError,OSError,ValueError):
        raise ValueError("Timely에 연결하거나 응답을 읽지 못했습니다.") from None
    try:
        raw = obj["choices"][0]["message"]["content"].strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```",raw,re.I|re.S)
        values = json.loads(match.group(1) if match else raw)
        if not isinstance(values,dict):
            raise ValueError()
        return values
    except (KeyError,IndexError,TypeError,AttributeError,ValueError):
        raise ValueError("모델이 유효한 항목 목록을 보내지 않았습니다. 항목명=값 형식으로 입력할 수 있습니다.") from None


def chat(messages, key, model=None, want_json=False):
    """일반 대화 호출. want_json이면 JSON 객체 하나를 돌려준다."""
    if not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,512}", key):
        raise ValueError("서버의 .env에서 TIMELYGPT_API_KEY를 확인하세요.")
    payload = {"model": _direct(model or MODEL), "stream": False, "messages": messages}
    if want_json:  # 대화 기록이 평문이어도 형식을 지키게 한다
        payload["response_format"] = {"type": "json_object"}
    req = request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with request.build_opener(NoRedirect).open(req, timeout=60) as response:
            obj = json.loads(response.read(2000000))
        raw = obj["choices"][0]["message"]["content"].strip()
    except error.HTTPError as exc:
        hints = {401: "API 키가 유효하지 않습니다. .env의 키를 확인하세요.", 402: "크레딧을 확인하세요.",
                 403: "계정 권한을 확인하세요.", 404: "모델 ID를 확인하세요.", 429: "호출 한도나 크레딧을 확인하세요."}
        raise ValueError(f"Timely HTTP {exc.code}. " + hints.get(exc.code, "서버가 요청을 완료하지 못했습니다.")) from None
    except (error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ValueError("Timely에 연결하거나 응답을 읽지 못했습니다.") from None
    if not want_json:
        return raw
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        value = json.loads(match.group(0)) if match else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise ValueError("모델이 정해진 형식으로 답하지 않았습니다.")
    return value
