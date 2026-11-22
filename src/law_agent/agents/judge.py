"""④ 판단 — 법 해석은 여기서만 한다. LLM 에게 질문 + 근거 묶음 + 사례 사실 + 앞 판단을 주고 정해진 형식의 답을 받는다.

대상: schedule.ready_questions(state). 약속: 대상 질문마다 answers[qid] 를 반드시 새로 넣는다 (실패해도 빈 목록).

방식 (settings llm.judge_mode)
  escalate (기본) : judge_models 앞 2개를 동시에 부른다. 둘 다 유효(인용·값 검사 통과)하고 같은 답이면 끝.
                    답이 다르거나, 하나가 무효이거나, 호출이 실패하면 3번째 모델을 부른다. 최종 판단은 verifier.
  all             : 3개 모두 부른다.
  single          : 첫 모델만 (개발·비용 측정용 — 합의 검사가 없어 verifier 가 min_judges 1 로 봐야 함)

근거 묶음이 비면 부르지 않는다 (answers=[] → verifier 가 넓게 재검색).

캐시 키 = (질문 id, 근거 묶음 해시, 입력 해시(사례 사실 + 앞 판단 + 사실 데이터), 모델, 프롬프트 판).
  프롬프트 판 = "judge_v1-" + 프롬프트 파일 내용 해시 앞 8자 → 프롬프트를 고치면 캐시가 자동으로 무효.
  앞 판단이 바뀌면 입력 해시가 바뀌어 다시 판단한다 (판단 누적).
상한: 사례당 호출 수(limits.max_llm_calls_per_case)·시간(limits.max_seconds_per_case). 차례가 여러 번이어도
  감사 기록(audit)의 지난 judge 호출 수·시작 시각을 이어 센다. 넘으면 남은 호출은 하지 않고
  errors 에 "budget_exceeded: …" 로 남긴다 (verifier 가 needs_human + budget_exceeded).
"""
from __future__ import annotations

import re

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from law_agent.agents.context import effective_facts, prior_findings, question_facts
from law_agent.agents.schedule import BUDGET_PREFIX, ready_questions
from law_agent.agents.verifier import _default_checker, drop_reason
from law_agent.settings import load_settings
from law_agent.state import (
    ALLOWED_ANSWERS, ANSWERS, AgentState, EvidencePack, JudgeAnswer, Question, cache_key, canonical_json,
    consensus_key, error_record, sha256_hex, to_jsonable,
)

NODE = "judge"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "judge_v1.md"
MAX_FACT_CODES = 30


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def prompt_version() -> str:
    return "judge_v1-" + sha256_hex(system_prompt())[:8]


@dataclass
class JudgeConfig:
    models: list[str] = field(default_factory=list)
    mode: str = "escalate"
    workers: int = 6
    max_calls: int = 100
    max_seconds: float = 600
    light_stages: tuple = ()     # 이 단계(G·H·I·J: 서류·자격·참고)는 모델 1개 → 실패·무효일 때만 2번째 (비용)

    @classmethod
    def from_settings(cls, settings: dict | None = None) -> "JudgeConfig":
        s = settings if settings is not None else load_settings()
        llm, lim = s.get("llm") or {}, s.get("limits") or {}
        return cls(models=list(llm.get("judge_models") or []), mode=str(llm.get("judge_mode", "escalate")),
                   workers=int(llm.get("judge_workers", 6)), max_calls=int(lim.get("max_llm_calls_per_case", 100)),
                   max_seconds=float(lim.get("max_seconds_per_case", 600)),
                   light_stages=tuple(llm.get("light_stages") or ()))


@dataclass
class JudgeContext:
    """사례 하나의 호출 수·시간 상한과 캐시. 차례가 바뀌어도 이어 센다."""
    counter: Any
    started: float
    deadline: float
    cache: Any = None
    prev_calls: int = 0

    @classmethod
    def for_state(cls, state: AgentState, cfg: JudgeConfig, cache: Any = "default") -> "JudgeContext":
        from law_agent.llm.client import CallCounter
        prev = [a for a in state.get("audit") or [] if a.get("node") == NODE]
        prev_calls = sum(int((a.get("summary") or {}).get("calls", 0)) for a in prev)
        started = min((float(a["started"]) for a in prev if a.get("started")), default=time.time())
        counter = CallCounter(cfg.max_calls)
        counter.count = prev_calls
        if cache == "default":
            try:
                from law_agent.store.cache import JudgeCache
                cache = JudgeCache()
            except Exception:                        # noqa: BLE001 — 저장 루트가 없으면 캐시 없이
                cache = None
        return cls(counter=counter, started=started, deadline=started + cfg.max_seconds, cache=cache,
                   prev_calls=prev_calls)


# ── 프롬프트 만들기 ───────────────────────────────────────────

def fact_data(pack: EvidencePack, eff: dict) -> dict[str, str]:
    """[사실 데이터]: 사례 분류번호와 근거에 나온 코드의 이름 (사실 데이터 표). 표가 없으면 빈 dict."""
    from law_agent.tools.law_tools import RX_ANY_CODE
    codes: list[str] = []
    values = eff.get("values", {})
    for c in [values.get("waste_code")] + [x for x in values.get("r_codes") or []]:
        if c:
            codes.append(c)
    for it in pack.items:
        codes += RX_ANY_CODE.findall(it.text)
    out: dict[str, str] = {}
    try:
        from law_agent.tools.fact_tools import check_code
        for c in list(dict.fromkeys(codes))[:MAX_FACT_CODES]:
            info = check_code(c)
            if info.get("exists") and info.get("name"):
                out[c] = str(info["name"])
    except Exception:                                # noqa: BLE001 — 사실 데이터는 보조 정보
        return {}
    return out


def render_user(q: Question, pack: EvidencePack, facts: dict, priors: dict, fdata: dict) -> str:
    allowed = " / ".join(ALLOWED_ANSWERS.get(q.answer_type, ANSWERS))
    parts = [
        f"[질문] {q.id}\n{q.text}",
        f"[답 형식] answer_type = {q.answer_type} (answer: {allowed})",
        "[사례 사실]\n" + json.dumps(facts, ensure_ascii=False, indent=1),
        "[앞 판단]\n" + (json.dumps(priors, ensure_ascii=False, indent=1) if priors else "없음"),
        "[사실 데이터] (코드 이름 — 법 원문이 아니므로 인용하지 않는다)\n"
        + ("\n".join(f"{k} = {v}" for k, v in fdata.items()) if fdata else "없음"),
        "[근거 묶음]",
    ]
    for i, it in enumerate(pack.items, 1):
        tag = " | 발췌" if it.excerpt else ""
        warn = f" | 주의: {'; '.join(it.problems)}" if it.problems else ""
        parts.append(f"<<근거 {i}>> ref={it.ref} | {it.title}{tag}{warn}\n{it.text}\n<<근거 {i} 끝>>")
    parts.append(f'JSON 객체 하나만 출력하라. question_id 는 "{q.id}".')
    return "\n\n".join(parts)


# ── 답 정리 ──────────────────────────────────────────────────

def _str_list(x: Any) -> list[str]:
    return [str(v) for v in x if isinstance(v, (str, int, float))] if isinstance(x, list) else []


# 모델이 missing_facts 에 '법 내용'을 적는 경우 (데모 실행: "R코드별 재활용 유형", "사업장폐기물 처리기준 원문").
# 이것은 배출업체가 답할 사실이 아니라 근거 묶음이 부족한 것 → evidence_gaps 로 옮겨 재검색·사람 검토로 보낸다.
LAW_GAP_RX = re.compile(r"원문|조문|법령|별표|별지|고시|시행규칙|시행령|규정|조항|기준표|처리기준|재활용 ?기준|"
                        r"R ?코드|R-\d|재활용 ?유형|처리방법|처분 ?방법|처리 ?방법 ?코드|허가 ?종류|준수사항|"
                        r"분류번호 *\d\d-\d\d")


# 이 시스템이 정할 것을 사례 사실로 묻는 경우 (데모 1 B-2: '재활용 예정 유형') → 버린다 (파트 1에 묻지 않음, 재검색도 아님)
CIRCULAR_RX = re.compile(r"(재활용|처리|처분|위탁)\s*(예정|계획|희망|방식|업체)")


def split_gaps(missing: list[str], gaps: list[str]) -> tuple[list[str], list[str]]:
    """(사례 사실, 근거 부족). 법 내용으로 보이는 이름은 근거 부족 쪽으로, 이 시스템이 정할 것은 버린다."""
    missing = [m for m in missing if not CIRCULAR_RX.search(m)]
    facts = [m for m in missing if not LAW_GAP_RX.search(m)]
    return facts, list(dict.fromkeys(gaps + [m for m in missing if LAW_GAP_RX.search(m)]))


def to_answer(data: dict, q: Question, model: str, raw: str, pv: str) -> JudgeAnswer:
    """모델 JSON → JudgeAnswer. 형식만 맞추고 판단은 바꾸지 않는다. question_id 는 실제 id 로 고정."""
    answer = str(data.get("answer", "unknown")).strip().lower()
    if answer not in ANSWERS:
        answer = "unknown"
    value = data.get("value") if isinstance(data.get("value"), dict) else {}
    conds = [{"text": str(c.get("text", "")), "quote": str(c.get("quote", "")), "ref": str(c.get("ref", ""))}
             for c in data.get("conditions") or [] if isinstance(c, dict)]
    quotes = [{"ref": str(x.get("ref", "")), "text": str(x.get("text", ""))}
              for x in data.get("quotes") or [] if isinstance(x, dict)]
    facts, gaps = split_gaps(_str_list(data.get("missing_facts")), _str_list(data.get("evidence_gaps")))
    return JudgeAnswer(question_id=q.id, model=model, answer=answer, value=value, conditions=conds, quotes=quotes,
                       missing_facts=facts, reason=str(data.get("reason", ""))[:1000],
                       raw=raw, prompt_version=pv, evidence_gaps=gaps)


# ── 호출 ─────────────────────────────────────────────────────

def call_model(model: str, q: Question, system: str, user: str, key: str, ctx: JudgeContext,
               pv: str) -> tuple[JudgeAnswer | None, dict | None, dict]:
    """(답, 오류, 기록). 캐시 → 시간 상한 → 호출."""
    from law_agent.llm.client import BudgetExceeded, LLMError, chat_json
    meta: dict[str, Any] = {"model": model, "cached": False}
    if ctx.cache is not None:
        hit = ctx.cache.get(key)
        if hit is not None:
            meta["cached"] = True
            return JudgeAnswer.from_dict(hit), None, meta
    if time.time() > ctx.deadline:
        return None, error_record(NODE, f"{BUDGET_PREFIX}: 사례당 시간 상한 초과", q.id, model), meta
    try:
        reply = chat_json(model, system, user, question_id=q.id, counter=ctx.counter)
    except BudgetExceeded as e:
        return None, error_record(NODE, f"{BUDGET_PREFIX}: {e}", q.id, model), meta
    except LLMError as e:
        return None, error_record(NODE, f"{type(e).__name__}: {str(e)[:200]}", q.id, model), meta
    ans = to_answer(reply.data, q, model, reply.raw, pv)
    meta.update(seconds=round(reply.seconds, 2), attempts=reply.attempts, usage=dict(reply.usage or {}))
    if ctx.cache is not None:
        try:
            ctx.cache.put(key, to_jsonable(ans))
        except Exception:                            # noqa: BLE001 — 캐시 실패는 판단에 영향 없음
            pass
    return ans, None, meta


Checker = Callable[[str], dict]


def judge_question(q: Question, pack: EvidencePack, state: AgentState, ctx: JudgeContext, cfg: JudgeConfig,
                   checker: Checker, eff: dict | None = None) -> tuple[list[JudgeAnswer], list[dict], dict]:
    """질문 하나 → (답 목록, 오류 목록, 기록)."""
    if not pack.items:
        return [], [], {"skipped": "근거 묶음이 비어 부르지 않음"}
    eff = eff if eff is not None else effective_facts(state)
    facts = question_facts(q, state, eff)
    priors = prior_findings(q, state)
    fdata = fact_data(pack, eff)
    system, user, pv = system_prompt(), render_user(q, pack, facts, priors, fdata), prompt_version()
    input_hash = sha256_hex(canonical_json({"facts": facts, "priors": priors, "fact_data": fdata}))

    def run(models: list[str]) -> list[tuple[JudgeAnswer | None, dict | None, dict]]:
        keys = [cache_key(q.id, pack.pack_hash, input_hash, m, pv) for m in models]
        if len(models) == 1:
            return [call_model(models[0], q, system, user, keys[0], ctx, pv)]
        with ThreadPoolExecutor(len(models)) as ex:
            return list(ex.map(lambda mk: call_model(mk[0], q, system, user, mk[1], ctx, pv), zip(models, keys)))

    models = cfg.models
    light = cfg.mode == "escalate" and (q.stage in cfg.light_stages or q.base_id in cfg.light_stages)
    if cfg.mode == "single":
        first, rest = models[:1], []
    elif light:
        first, rest = models[:1], models[1:2]
    elif cfg.mode == "all":
        first, rest = models[:3], []
    else:
        first, rest = models[:2], models[2:3]
    results = run(first)
    escalated = ""
    if rest:
        got = [a for a, _e, _m in results if a is not None]
        valid = [a for a in got if not drop_reason(a, q, pack, checker)]
        if len(got) < len(first):
            escalated = "호출 실패"
        elif len(valid) < len(first):
            escalated = "무효 답 (인용·값 검사 실패)"
        elif len({consensus_key(q.answer_type, a.answer, a.value) for a in valid}) > 1:
            escalated = "답이 다름"
        if light and escalated:
            escalated = "가벼운 질문: " + escalated
        if escalated:
            if any(e and e["message"].startswith(BUDGET_PREFIX) for _a, e, _m in results):
                escalated += " (상한으로 3번째를 부르지 않음)"
            else:
                results += run(rest)
    answers = [a for a, _e, _m in results if a is not None]
    errors = [e for _a, e, _m in results if e is not None]
    meta = {"models": [m for _a, _e, m in results], "escalated": escalated, "input_hash": input_hash[:12],
            "user_chars": len(user), "light": light,
            "inputs": {"facts": facts, "priors": priors, "fact_data": fdata}}   # 모델에게 준 사실·앞 판단 (관리자 화면)
    return answers, errors, meta


def node(state: AgentState, cfg: JudgeConfig | None = None, ctx: JudgeContext | None = None,
         checker: Checker | None = None) -> dict:
    """judge 노드. 바뀐 칸: answers, errors, audit."""
    cfg = cfg or JudgeConfig.from_settings()
    ctx = ctx or JudgeContext.for_state(state, cfg)
    checker = checker or _default_checker
    packs = state.get("packs") or {}
    eff = effective_facts(state)
    qs = ready_questions(state)
    empty = EvidencePack(question_id="", items=[])

    def one(q: Question):
        return q.id, judge_question(q, packs.get(q.id, empty), state, ctx, cfg, checker, eff)

    with ThreadPoolExecutor(max(1, cfg.workers)) as ex:
        results = list(ex.map(one, qs))

    answers: dict[str, list[JudgeAnswer]] = {}
    errors: list[dict] = []
    out_audit: dict[str, dict] = {}
    usage: dict[str, int] = {}
    cached = escalated = 0
    for qid, (ans, errs, meta) in results:
        answers[qid] = ans
        errors += errs
        out_audit[qid] = {**meta, "answers": [(a.model, a.answer) for a in ans], "errors": [e["message"][:80] for e in errs]}
        for m in meta.get("models", []):
            cached += bool(m.get("cached"))
            for k, n in (m.get("usage") or {}).items():
                usage[k] = usage.get(k, 0) + int(n)
        escalated += bool(meta.get("escalated")) and "상한으로" not in meta.get("escalated", "")
    audit = {"node": NODE, "started": ctx.started, "summary": {
        "questions": len(qs), "calls": ctx.counter.count - ctx.prev_calls, "total_calls": ctx.counter.count,
        "cached": cached, "escalated": escalated, "usage": usage, "mode": cfg.mode,
        "prompt_version": prompt_version()}, "output": out_audit}
    out: dict = {"answers": answers, "audit": [audit]}
    if errors:
        out["errors"] = errors
    return out
