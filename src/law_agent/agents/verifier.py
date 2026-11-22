"""⑤ 검증 — 다섯 겹. LLM 을 부르지 않는다.

판단 대상: 이번 차례에 근거 묶음이 있고 아직 최종 판단이 없는 질문 (재검색 대기 retry 포함).

  1. 인용 대조   : 모든 quote(조건의 quote 포함)가 그 ref 의 근거 원문에 글자 그대로 있는지 (state.quote_in)
                   결론(yes/no/conditional)인데 인용이 없으면 버림. 너무 짧은 인용(MIN_QUOTE_CHARS 미만)도 버림
  2. 값 검사     : validate_value 통과, 코드(R코드·분류번호·처리방법 코드)가 사실 데이터에 있는지,
                   R코드·분류번호 항목은 인용 안에 있는지 (사실 데이터에만 있고 법 원문에 없는 코드를 막음),
                   compare 의 기준(criterion) 숫자가 인용 안에 있는지
  3. 합의        : 유효한 답이 min_judges 미만 → needs_human.
                   consensus_key 가 같은 답이 agree_ratio 이상이면 채택.
                   불일치 → retry_count < re_retrieve_max 면 retry (재검색), 아니면 needs_human
  4. 빠진 질문   : 묶음은 있는데 답이 없으면(모델 호출 전부 실패) needs_human.
                   차례가 MAX_ROUNDS 를 넘으면 남은 질문 모두 needs_human
  5. 안전 방향   : 채택 답이 yes/no 로 갈렸는데 유효한 소수 답이 다른 결론이면 → conditional + safety_veto
                   (질문마다 "가능" 쪽이 yes 인지 no 인지 다르므로 방향과 관계없이 만장일치가 아니면 낮춘다)
                   소수가 '모름 + 빠진 사실' 이어도 낮춘다. 파트 1 의심 신호를 뒤집는 A-2 '아니오' 도 낮춘다
                   (input_signal_guard).

채택 뒤:
  - 채택 답이 unknown + missing_facts 있음 → needs_info (배출업체에 물을 사실)
  - 채택 답이 unknown + missing_facts 없음 → 근거 부족: 1회차면 retry 한 번 (넓은 근거로), 그다음은 needs_human
  - 근거 묶음이 비었으면 같은 규칙 (retry 한 번 → 그래도 없으면 needs_human + low_retrieval)
  - 모델끼리 답이 갈린 재검색만 re_retrieve_max 회까지 (retry_hint 로 검색어가 달라지므로)
  - 근거 잘림·불완전 조문은 flags 로 표시 (composer 가 참고)
판단 뒤 when 이 거짓이 된 질문은 not_applicable 로 기록한다.

route_after_verify(state) → "retriever" (재검색 또는 다음 차례) | "composer"
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from law_agent.agents.schedule import BUDGET_PREFIX, MAX_ROUNDS, classify, question_map
from law_agent.settings import load_settings
from law_agent.state import (
    FLAG_BUDGET_EXCEEDED, FLAG_EVIDENCE_GAP, FLAG_EVIDENCE_TRUNCATED, FLAG_INCOMPLETE_ARTICLE, FLAG_JUDGE_FAILED,
    FLAG_LOW_RETRIEVAL, FLAG_SAFETY_VETO, AgentState, EvidencePack, JudgeAnswer, Question, Verdict, Vote,
    consensus_key, core_value, norm_text, quote_in, validate_value,
)

NODE = "verifier"
MIN_QUOTE_CHARS = 10            # 정규화 뒤 글자 수. "R-3-2" 같은 짧은 인용은 무엇이든 맞아 버리므로 막는다
DECIDED = ("yes", "no", "conditional")


@dataclass
class VerifierConfig:
    judge_models: list[str] = field(default_factory=list)
    min_judges: int = 2
    agree_ratio: float = 0.67
    re_retrieve_max: int = 2
    safety_veto: bool = True
    light_stages: tuple = ()     # judge 와 같은 값: 이 단계는 유효한 답 1개로 판단

    @classmethod
    def from_settings(cls, settings: dict | None = None) -> "VerifierConfig":
        s = settings if settings is not None else load_settings()
        llm, cons = s.get("llm") or {}, s.get("consensus") or {}
        # single 모드(모델 1개)는 합의가 없으므로 1개로 판단 — 개발·비용 측정용
        min_j = 1 if llm.get("judge_mode") == "single" else int(llm.get("min_judges", 2))
        return cls(judge_models=list(llm.get("judge_models") or []), min_judges=min_j,
                   agree_ratio=float(cons.get("agree_ratio", 0.67)),
                   re_retrieve_max=int(cons.get("re_retrieve_max", 2)),
                   safety_veto=bool(cons.get("safety_veto", True)),
                   light_stages=tuple(llm.get("light_stages") or ()) if llm.get("judge_mode", "escalate") == "escalate" else ())


CodeChecker = Callable[[str], dict]


def _default_checker(code: str) -> dict:
    from law_agent.tools.fact_tools import check_code
    return check_code(code)


# ── 1·2겹: 답 하나 검사 ────────────────────────────────────────

def check_quotes(ans: JudgeAnswer, pack: EvidencePack) -> str | None:
    """인용 대조. 문제가 있으면 버릴 이유(문장), 없으면 None."""
    pairs = [(q.get("ref", ""), q.get("text", ""), "인용") for q in ans.quotes]
    pairs += [(c.get("ref", ""), c.get("quote", ""), f"조건 '{c.get('text', '')[:20]}' 인용") for c in ans.conditions]
    if ans.answer in DECIDED and not ans.quotes:
        return "결론에 인용이 없음"
    for ref, text, label in pairs:
        item = pack.get(ref)
        if item is None:
            return f"{label}의 ref 가 근거 묶음에 없음: {ref}"
        if len(norm_text(text)) < MIN_QUOTE_CHARS:
            return f"{label}이 너무 짧음 ({len(norm_text(text))}자)"
        if not quote_in(text, item.text):
            # ref 만 틀리고 같은 묶음의 다른 조문에 글자 그대로 있으면 인정 (문장은 원문 그대로이므로)
            if not any(quote_in(text, it.text) for it in pack.items):
                return f"{label}이 원문에 없음: {text[:40]}"
    for c in ans.conditions:
        if not (c.get("text") or "").strip():
            return "조건 문장이 비어 있음"
    return None


RX_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def check_values(ans: JudgeAnswer, q: Question, checker: CodeChecker) -> str | None:
    """값 검사. 문제가 있으면 버릴 이유, 없으면 None."""
    problems = validate_value(q.answer_type, ans.value, ans.answer)
    if problems:
        return "형식: " + "; ".join(problems)
    quoted = norm_text(" ".join(x.get("text", "") for x in ans.quotes)
                       + " " + " ".join(c.get("quote", "") for c in ans.conditions))
    for code in ans.value.get("items") or []:
        info = checker(code)
        if info.get("exists") is False:
            return f"사실 데이터에 없는 코드: {code}"
        if info.get("kind") in ("r_code", "waste_code") and norm_text(code) not in quoted:
            return f"코드가 인용 안에 없음: {code}"
    if q.answer_type == "compare" and ans.answer in DECIDED:
        for n in RX_NUM.findall(ans.value.get("criterion") or ""):
            if norm_text(n) not in quoted:
                return f"기준 숫자 {n} 이 인용 안에 없음"
    return None


def drop_reason(ans: JudgeAnswer, q: Question, pack: EvidencePack, checker: CodeChecker) -> str:
    """버릴 이유 ("" 이면 유효)."""
    if ans.question_id != q.id:
        return f"질문 id 가 다름: {ans.question_id}"
    return check_quotes(ans, pack) or check_values(ans, q, checker) or check_method_names(ans, q, pack) or ""


def check_method_names(ans: JudgeAnswer, q: Question, pack: EvidencePack) -> str | None:
    """F-1 처분 방법 이름은 근거 원문에 있는 말이어야 한다 (지어낸 방법 막기). 인용 안이 아니라 근거 묶음 전체에서 찾는다
    — 인용이 잘려 '매립'이 빠져도 답을 버리지 않게 (2026.11.17 데모 1)."""
    if q.base_id != "F-1":
        return None
    text = norm_text(" ".join(i.text or "" for i in pack.items))
    for it in ans.value.get("items") or []:
        if not any(ch.isdigit() for ch in it) and norm_text(it) not in text:
            return f"처분 방법이 근거 원문에 없음: {it}"
    return None


# ── 3·5겹: 합의 ───────────────────────────────────────────────

def _model_order(cfg: VerifierConfig, model: str) -> int:
    return cfg.judge_models.index(model) if model in cfg.judge_models else len(cfg.judge_models)


def _merge_conditions(answers: list[JudgeAnswer]) -> list[dict]:
    out, seen = [], set()
    for a in answers:
        for c in a.conditions:
            k = (norm_text(c.get("text", "")), c.get("ref", ""))
            if k not in seen:
                seen.add(k)
                out.append(dict(c))
    return out


def _merge_quotes(answers: list[JudgeAnswer]) -> list[dict]:
    out, seen = [], set()
    for a in answers:
        for x in a.quotes:
            k = (x.get("ref", ""), norm_text(x.get("text", "")))
            if k not in seen:
                seen.add(k)
                out.append(dict(x))
    return out


def _retry_hint(q: Question, valid: list[JudgeAnswer]) -> list[str]:
    """재검색 검색어에 더할 말: 모델마다 달랐던 항목 + 조건 문장 몇 개."""
    sets = [set(a.value.get("items") or []) for a in valid]
    diff = sorted(set().union(*sets) - set.intersection(*sets)) if sets else []
    conds = [c.get("text", "") for a in valid for c in a.conditions if c.get("text")]
    gaps = [g for a in valid for g in a.evidence_gaps]          # 모델이 "근거에 없다"고 한 법 내용 → 검색어로
    return list(dict.fromkeys(diff + gaps[:4] + conds[:3]))


def decide(q: Question, pack: EvidencePack, answers: list[JudgeAnswer], failed: list[dict],
           retry_count: int, cfg: VerifierConfig, checker: CodeChecker) -> Verdict:
    """질문 하나의 Verdict. failed = 이 질문에서 호출 실패한 errors 기록."""
    votes: list[Vote] = []
    valid: list[JudgeAnswer] = []
    for a in sorted(answers, key=lambda a: _model_order(cfg, a.model)):
        reason = drop_reason(a, q, pack, checker)
        votes.append(Vote(model=a.model, answer=a.answer, valid=not reason, drop_reason=reason))
        if not reason:
            valid.append(a)
    for e in failed:
        votes.append(Vote(model=e.get("model") or "", answer="", valid=False,
                          drop_reason=f"호출 실패: {e.get('message', '')[:80]}"))

    flags: list[str] = []
    if pack.truncated:
        flags.append(FLAG_EVIDENCE_TRUNCATED)
    if any(not it.complete for it in pack.items):
        flags.append(FLAG_INCOMPLETE_ARTICLE)
    base = dict(question_id=q.id, votes=votes, retries=retry_count, pack_hash=pack.pack_hash)

    if not pack.items:
        if retry_count == 0:                          # 1회차(좁게)에 근거가 없으면 넓게 한 번만 다시 찾는다
            return Verdict(status="retry", flags=flags + [FLAG_LOW_RETRIEVAL], **{**base, "retries": retry_count + 1})
        return Verdict(status="needs_human", flags=flags + [FLAG_LOW_RETRIEVAL], **base)
    min_judges = 1 if (q.stage in cfg.light_stages or q.base_id in cfg.light_stages) else cfg.min_judges
    if len(valid) < min_judges:
        f = flags + [FLAG_JUDGE_FAILED] if failed or not answers else flags
        if any((e.get("message") or "").startswith(BUDGET_PREFIX) for e in failed):
            f = f + [FLAG_BUDGET_EXCEEDED]
        return Verdict(status="needs_human", flags=f, **base)

    # 결론을 낸 답끼리는 같고, 나머지가 '빠진 사실'을 댄 모름뿐이면 — 근거를 넓혀도 사실은 생기지 않으므로
    # 재검색하지 않는다. 결론 쪽이 다수면 아래(5겹)에서 conditional 로, 아니면 needs_info 로.
    decided = [a for a in valid if a.answer in DECIDED]
    unk_missing = [a for a in valid if a.answer == "unknown" and a.missing_facts]
    decided_keys = {consensus_key(q.answer_type, a.answer, a.value) for a in decided}
    if decided and unk_missing and len(decided_keys) == 1 and len(decided) + len(unk_missing) == len(valid) \
            and round(len(decided) / len(valid), 2) < cfg.agree_ratio:
        missing = sorted({m for a in unk_missing for m in a.missing_facts})
        return Verdict(status="needs_info", answer="unknown", quotes=_merge_quotes(valid), missing_facts=missing,
                       flags=flags, **base)

    if q.answer_type == "list" and len(valid) >= 2 and all(a.answer in ("yes", "conditional") for a in valid):
        merged = merge_list_answers(q, valid, cfg, flags, base)
        if merged is not None:
            return merged

    groups = Counter(consensus_key(q.answer_type, a.answer, a.value) for a in valid)
    top_key, top_n = groups.most_common(1)[0]
    tied = sum(1 for n in groups.values() if n == top_n) > 1
    # agree_ratio 0.67 은 '2/3 이상' 의 뜻 → 비율을 소수 둘째 자리로 반올림해 비교 (2/3 = 0.6667 → 0.67)
    if tied or round(top_n / len(valid), 2) < cfg.agree_ratio:
        if retry_count < cfg.re_retrieve_max:
            return Verdict(status="retry", retry_hint=_retry_hint(q, valid), flags=flags,
                           **{**base, "retries": retry_count + 1})
        return Verdict(status="needs_human", flags=flags, **base)

    major = [a for a in valid if consensus_key(q.answer_type, a.answer, a.value) == top_key]
    rep = major[0]                                    # settings.judge_models 순서로 첫 모델
    answer = rep.answer
    conditions = _merge_conditions(major)
    quotes = _merge_quotes(major)

    # 5겹: 유효한 소수 답이 다른 결론이면 conditional 로 낮춤.
    #  소수가 '모름' 이면서 구체적인 빠진 사실을 댔으면 그것도 낮춤 (예: "세척용제 성분 — 금지물질 해당 여부")
    #  — 사실이 확인되기 전까지 확정하지 않고, 그 사실을 파트 1 질문으로 남긴다.
    minority = [a for a in valid if a not in major and a.answer in DECIDED]
    minority_missing = [a for a in valid if a not in major and a.answer == "unknown" and a.missing_facts]
    if cfg.safety_veto and answer in ("yes", "no") and not minority and minority_missing:
        flags.append(FLAG_SAFETY_VETO)
        mf = sorted({m for a in minority_missing for m in a.missing_facts})
        if q.answer_type == "yes_no":
            return Verdict(status="needs_info", answer="unknown", quotes=quotes, missing_facts=mf, flags=flags, **base)
        conditions.append({"text": f"{minority_missing[0].model} 은(는) 다음 사실이 없어 판단을 보류함: {', '.join(mf)} — "
                                   f"확인되면 '{answer}' 로 확정", "quote": (quotes or [{}])[0].get("text", ""),
                           "ref": (quotes or [{}])[0].get("ref", "")})
        return Verdict(status="agreed", answer="conditional", value=dict(rep.value), conditions=conditions,
                       quotes=quotes, missing_facts=mf, flags=flags, **base)
    if cfg.safety_veto and answer in DECIDED and minority:
        flags.append(FLAG_SAFETY_VETO)
        if q.answer_type == "yes_no":                 # conditional 을 쓸 수 없는 형식 → 사람에게
            return Verdict(status="needs_human", answer="unknown", flags=flags, **base)
        ref = (minority[0].quotes or [{}])[0].get("ref", "")
        quote = (minority[0].quotes or [{}])[0].get("text", "")
        conditions.append({"text": f"모델 간 이견 — {minority[0].model} 은(는) '{minority[0].answer}' 로 판단. 사람 확인 필요",
                           "quote": quote, "ref": ref, "kind": "model_disagreement"})
        for c in minority[0].conditions:                # 소수 쪽이 댄 조건은 실제 조건으로 (경로에 붙음)
            if c.get("text") and not any(x.get("text") == c["text"] for x in conditions):
                conditions.append({**c, "kind": "minority_condition"})
        quotes = _merge_quotes([*major, *minority])
        answer = "conditional"

    if answer == "unknown":
        if any(a.evidence_gaps for a in major):
            flags.append(FLAG_EVIDENCE_GAP)
        missing = sorted({m for a in major for m in a.missing_facts})
        if missing:
            return Verdict(status="needs_info", answer="unknown", value=dict(rep.value),
                           quotes=quotes, missing_facts=missing, flags=flags, **base)
        if retry_count == 0:                          # 사실은 있는데 모름 = 근거 부족 → 넓게 한 번만 다시 찾는다
                                                      # (이미 넓게 찾은 근거로 또 모르면 같은 근거로 다시 물어도 소용없음)
            return Verdict(status="retry", retry_hint=_retry_hint(q, valid), flags=flags,
                           **{**base, "retries": retry_count + 1})
        return Verdict(status="needs_human", answer="unknown", value=dict(rep.value),
                       quotes=quotes, flags=flags, **base)
    # 조건부 판단이 기다리는 사실(예: 석면 함량) — composer 가 파트 1 질문으로 보낸다
    missing = sorted({m for a in major for m in a.missing_facts}) if answer == "conditional" else []
    return Verdict(status="agreed", answer=answer, value=dict(rep.value), conditions=conditions, quotes=quotes,
                   missing_facts=missing, flags=flags, **base)


def merge_list_answers(q: Question, valid: list[JudgeAnswer], cfg: VerifierConfig, flags: list[str],
                       base: dict) -> Verdict | None:
    """목록 답(R코드·처분 방법)은 항목별로 투표한다 (2026.11.17 데모 1: D-1 세 모델이 같은 R코드 목록을 냈는데
    yes/conditional 과 R-10 의 사전 분석 표시만 달라 '합의 없음' → 사람 검토가 됨).
      항목  : agree_ratio 이상이 낸 것만 채택. 소수만 낸 항목은 빼고 검토 메모로 (허용 목록을 넓히는 쪽은 다수만)
      답    : 하나라도 conditional 이면 conditional (더 엄격한 쪽)
      attrs : 참·거짓 값은 하나라도 참이면 참 (사전 분석 필요 등 — 더 엄격한 쪽)
    채택 항목이 없으면 None (기존 방식으로)."""
    n = len(valid)
    order: list[str] = []
    count: Counter = Counter()
    for a in valid:
        for it in dict.fromkeys(a.value.get("items") or []):
            count[it] += 1
            if it not in order:
                order.append(it)
    items = [it for it in order if round(count[it] / n, 2) >= cfg.agree_ratio]
    if not items:
        return None
    minor = [it for it in order if it not in items]
    attrs: dict = {}
    for it in items:
        merged: dict = {}
        for a in valid:
            for k, v in ((a.value.get("attrs") or {}).get(it) or {}).items():
                if isinstance(v, bool):
                    merged[k] = bool(merged.get(k)) or v
                else:
                    merged.setdefault(k, v)
        if merged:
            attrs[it] = merged
    answers = {a.answer for a in valid}
    partial = [it for it in items if count[it] < n]             # 채택됐지만 어떤 모델은 빼 놓은 항목 (더 엄격한 소수)
    answer = "conditional" if "conditional" in answers or partial else valid[0].answer
    conditions = _merge_conditions(valid)
    if partial:
        conditions.append({"text": f"다수가 포함했지만 일부 모델은 빼 놓은 항목: {', '.join(partial)} — 사람 확인",
                           "quote": "", "ref": "", "kind": "model_disagreement"})
    if minor:
        conditions.append({"text": f"일부 모델만 포함한 항목: {', '.join(minor)} — 사람 확인 (채택하지 않음)",
                           "quote": "", "ref": "", "kind": "model_disagreement"})
    if minor or len(answers) > 1 or any(core_value(q.answer_type, a.value) != core_value(q.answer_type, valid[0].value)
                                        for a in valid):
        flags = flags + [FLAG_SAFETY_VETO]
    value = {**valid[0].value, "items": items}
    if attrs:
        value["attrs"] = attrs
    missing = sorted({m for a in valid for m in a.missing_facts}) if answer == "conditional" else []
    return Verdict(status="agreed", answer=answer, value=value, conditions=conditions, quotes=_merge_quotes(valid),
                   missing_facts=missing, flags=flags, **base)


# ── 노드 ───────────────────────────────────────────────────────

SUSPECT_STATUSES = ("designated", "suspect", "designated_suspect")


def input_signal_guard(q: Question, v: Verdict, case: dict, cfg: VerifierConfig) -> Verdict:
    """파트 1 의심 신호를 뒤집는 느슨한 확정을 막는다 (시험세트 PRE-08, 2026.11.17).
    파트 1이 지정 의심(hazardous_suspect=true) 또는 지정(designated_waste_status)으로 보냈는데
    A-2(지정폐기물인가)가 '아니오' 로 합의되면 → conditional + 파트 1 질문.
    법 해석이 아니라 안전 규칙: 확인 전에는 입력 신호보다 느슨하게 확정하지 않는다. 반대 방향(더 엄격)은 그대로."""
    if not cfg.safety_veto or q.base_id != "A-2" or v.status != "agreed" or v.answer != "no":
        return v
    signals = []
    if case.get("hazardous_suspect") is True:
        signals.append("지정폐기물 의심(hazardous_suspect)")
    if str(case.get("designated_waste_status") or "") in SUSPECT_STATUSES:
        signals.append(f"지정 여부 '{case.get('designated_waste_status')}'")
    if not signals:
        return v
    q0 = (v.quotes or [{}])[0]
    cond = {"text": f"파트 1이 {', '.join(signals)} 로 보냈으나 판단은 '아니오' — 성분 분석 등으로 지정폐기물 "
                    f"해당 여부를 확인하기 전까지 확정하지 않음", "quote": q0.get("text", ""), "ref": q0.get("ref", "")}
    return Verdict(question_id=v.question_id, status="agreed", answer="conditional", value=dict(v.value),
                   conditions=list(v.conditions) + [cond], quotes=list(v.quotes), votes=list(v.votes),
                   retries=v.retries, missing_facts=sorted(set(v.missing_facts) | {"지정폐기물 해당 여부 확인(성분 분석)"}),
                   flags=list(v.flags) + [FLAG_SAFETY_VETO], pack_hash=v.pack_hash)


def _rounds(state: AgentState) -> int:
    return sum(1 for a in state.get("audit") or [] if a.get("node") == NODE)


def node(state: AgentState, cfg: VerifierConfig | None = None, checker: CodeChecker | None = None) -> dict:
    """verifier 노드. 바뀐 칸: verdicts, retry_count, audit."""
    cfg = cfg or VerifierConfig.from_settings()
    checker = checker or _default_checker
    qm = question_map(state)
    old = state.get("verdicts") or {}
    packs = state.get("packs") or {}
    answers = state.get("answers") or {}
    retry_count = dict(state.get("retry_count") or {})
    errors = [e for e in state.get("errors") or [] if e.get("node") == "judge"]

    new: dict[str, Verdict] = {}
    for qid, q in qm.items():
        v = old.get(qid)
        if qid not in packs or (v is not None and v.final):
            continue
        pack = packs[qid]
        failed = [e for e in errors if e.get("question_id") == qid
                  and not any(a.model == e.get("model") for a in answers.get(qid, []))]
        verdict = decide(q, pack, answers.get(qid, []), failed, retry_count.get(qid, 0), cfg, checker)
        new[qid] = verdict
        if verdict.status == "retry":
            retry_count[qid] = verdict.retries

    for qid, v in list(new.items()):
        guarded = input_signal_guard(qm[qid], v, state.get("case") or {}, cfg)
        if guarded is not v:
            new[qid] = guarded

    merged = {**old, **new}
    rounds = _rounds(state) + 1
    view = AgentState(questions=state.get("questions") or [], verdicts=merged)
    cls = classify(view)
    for qid in cls["skip"]:
        new[qid] = Verdict(question_id=qid, status="not_applicable")
    if rounds >= MAX_ROUNDS:                          # 4겹: 차례가 너무 많으면 남은 질문은 사람에게
        for qid in cls["ready"] + cls["pending"]:
            new[qid] = Verdict(question_id=qid, status="needs_human", retries=retry_count.get(qid, 0),
                               flags=[FLAG_BUDGET_EXCEEDED])

    summary = dict(Counter(v.status for v in new.values()))
    audit = {"node": NODE, "round": rounds, "summary": summary,
             "output": {qid: {"status": v.status, "answer": v.answer, "flags": v.flags,
                              "votes": [(x["model"], x["answer"], x["valid"], x["drop_reason"]) for x in v.votes]}
                        for qid, v in new.items()}}
    out: dict = {"verdicts": new, "audit": [audit]}
    changed = {k: n for k, n in retry_count.items() if (state.get("retry_count") or {}).get(k) != n}
    if changed:
        out["retry_count"] = changed
    return out


def route_after_verify(state: AgentState) -> str:
    """graph 조건 분기: 처리할 질문(재검색·다음 차례)이 남았으면 "retriever", 아니면 "composer"."""
    return "retriever" if classify(state)["ready"] else "composer"
