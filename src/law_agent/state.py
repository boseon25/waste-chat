"""law_agent 공통 자료형 — 모든 에이전트가 주고받는 값의 형식 (v1, 2026.11.16).

이 파일은 모든 모듈이 같이 쓴다. 다른 모듈을 만들 때 이 파일을 고치지 않는다.
바꿔야 하면 이 파일만 따로 고치고 모든 모듈 테스트를 다시 돌린다.

들어 있는 것
  1. 고정 값        : 답(yes/no/...), answer_type, Verdict 상태
  2. 자료형         : Question, Candidate, EvidenceItem, EvidencePack, JudgeAnswer, Verdict
  3. LangGraph 상태 : AgentState (+ reducer: 어느 칸이 덮어쓰기이고 어느 칸이 합치기인지)
  4. value 형식     : answer_type 별 value 모양, validate_value, consensus_key
  5. 해시           : canonical_json, pack 해시, 사례 사실 해시, 판단 캐시 키
  6. 조문 ref       : "{LAW}:{조문키}@{시행일}#{해시}" 만들기·풀기
  7. when 조건      : checklist.yaml 의 when 식 평가 (verifier·composer·graph 가 같이 씀)
  8. 사례 사실      : 파트 1 case → checklist 의 needs_facts 이름
  9. 직렬화         : JSON 저장·복원

node 함수 규칙: node(state: AgentState) -> dict  (바뀐 칸만 돌려준다)
  - candidates / packs / answers / verdicts / retry_count : 질문 id 단위로 합친다 (돌려준 질문만 바뀜)
  - audit / errors : 뒤에 덧붙인다 (지난 기록을 지우지 않음)
  - 나머지 칸 : 덮어쓴다
"""
from __future__ import annotations

import hashlib
import json
import operator
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

# ════════════════════════════════════════════════════════════════
# 1. 고정 값
# ════════════════════════════════════════════════════════════════

Answer = Literal["yes", "no", "conditional", "unknown"]
ANSWERS: tuple[str, ...] = ("yes", "no", "conditional", "unknown")

AnswerType = Literal["yes_no", "yes_no_cond", "list", "compare", "text"]
ANSWER_TYPES: tuple[str, ...] = ("yes_no", "yes_no_cond", "list", "compare", "text")

# answer_type 별로 쓸 수 있는 답
ALLOWED_ANSWERS: dict[str, tuple[str, ...]] = {
    "yes_no": ("yes", "no", "unknown"),
    "yes_no_cond": ANSWERS,
    "list": ANSWERS,
    "compare": ANSWERS,
    "text": ANSWERS,
}

# Verdict 상태
#   agreed         : 합의 + 인용 통과 → 채택
#   needs_info     : 배출업체 사실이 없어서 판단 못 함 (파트 1에 질문)
#   needs_human    : 근거 부족·모델 불일치·호출 실패 → 사람 검토
#   retry          : 불일치라 재검색 대기 (임시 상태, graph 가 retriever 로 되돌림)
#   not_applicable : when 조건이 거짓이라 묻지 않음
VerdictStatus = Literal["agreed", "needs_info", "needs_human", "retry", "not_applicable"]
VERDICT_STATUSES: tuple[str, ...] = ("agreed", "needs_info", "needs_human", "retry", "not_applicable")
FINAL_STATUSES: tuple[str, ...] = ("agreed", "needs_info", "needs_human", "not_applicable")

CandidateSource = Literal["min_laws", "search", "citation"]
CANDIDATE_SOURCES: tuple[str, ...] = ("min_laws", "search", "citation")

CompareResult = Literal["met", "not_met", "unknown"]
COMPARE_RESULTS: tuple[str, ...] = ("met", "not_met", "unknown")

# Verdict.flags 에 쓰는 이름 (자유 문자열 대신 이것만)
FLAG_SAFETY_VETO = "safety_veto"              # 한 모델이 유효 인용으로 "불가" → conditional 로 낮춤
FLAG_EVIDENCE_TRUNCATED = "evidence_truncated"  # 근거 묶음이 크기 제한으로 잘림
FLAG_INCOMPLETE_ARTICLE = "incomplete_article"  # 호 개수 검증 실패 조문이 근거에 있음
FLAG_LOW_RETRIEVAL = "low_retrieval"          # 검색 결과 없음·점수 낮음
FLAG_JUDGE_FAILED = "judge_failed"            # 모델 호출 실패로 min_judges 미만
FLAG_BUDGET_EXCEEDED = "budget_exceeded"      # 사례당 호출·시간 상한 초과
FLAG_EVIDENCE_GAP = "evidence_gap"            # 모델이 "근거 묶음에 필요한 법 내용이 없다"고 함
FLAG_NOT_RELEVANT = "not_relevant"            # planner: 사례에 관련 신호가 없어 묻지 않음 (relevant_if)
FLAGS: tuple[str, ...] = (FLAG_SAFETY_VETO, FLAG_EVIDENCE_TRUNCATED, FLAG_INCOMPLETE_ARTICLE,
                          FLAG_LOW_RETRIEVAL, FLAG_JUDGE_FAILED, FLAG_BUDGET_EXCEEDED)


# ════════════════════════════════════════════════════════════════
# 2. 자료형
# ════════════════════════════════════════════════════════════════

class QuoteRef(TypedDict):
    """원문 인용. ref 는 근거 묶음 EvidenceItem.ref 그대로, text 는 원문 그대로 복사."""
    ref: str
    text: str


class Condition(TypedDict):
    """조건 하나. quote 는 그 조건의 근거 원문 (ref 의 text 안에 글자 그대로 있어야 함)."""
    text: str
    quote: str
    ref: str


class Vote(TypedDict):
    """Verdict 에 남기는 모델별 표."""
    model: str
    answer: str
    valid: bool
    drop_reason: str           # valid 이면 ""


@dataclass
class MinLaw:
    """체크리스트가 지정한 최소 조문. no 는 조문 번호("13의2", "B4의3"), 고시처럼 번호가 없으면 ""."""
    law: str
    no: str
    title: str
    section: dict = field(default_factory=dict)   # 긴 별표에서 폐기물 구분별로 넣을 호 {general|designated|medical: 호 머리 말}
    subsection: str = ""                            # 그 호 안에서 넣을 목 머리 말 (예: "처리의 경우")

    @classmethod
    def from_dict(cls, d: dict) -> "MinLaw":
        # 주의: YAML 1.1(PyYAML)은 따옴표 없는 키 no 를 불리언 False 로 읽는다 → False 키도 받는다.
        #       checklist.yaml 은 article: 로 쓰거나 "no": 로 따옴표를 치는 것을 권장.
        no = d.get("article", d.get("no", d.get(False)))
        return cls(law=str(d["law"]), no="" if no is None else str(no), title=str(d.get("title") or ""),
                   section={str(k): str(v) for k, v in (d.get("section") or {}).items()},
                   subsection=str(d.get("subsection") or ""))


@dataclass
class Question:
    """질문 하나. checklist.yaml 한 항목 또는 planner 가 더한 하위 질문.

    id        : "D-1", 하위 질문은 sub_question_id("D-1", 1) = "D-1.c1"
    text      : 질문 문장 (checklist 의 question)
    when      : 묻는 조건 식 (None 이면 항상). 하위 질문은 부모의 when 을 그대로 가짐
    component : 하위 질문이면 사례 components 의 몇 번째 성분인지 (1부터)
    search    : True 면 최소 조문 외에 2등급 법 전체 검색 (J 단계)
    baseline  : route_agent 에서 같은 내용을 판단하는 곳 (eval 만 씀)
    """
    id: str
    stage: str
    text: str
    answer_type: str
    min_laws: list[MinLaw] = field(default_factory=list)
    needs_facts: list[str] = field(default_factory=list)
    when: str | None = None
    feeds: list[str] = field(default_factory=list)
    parent_id: str | None = None
    component: int | None = None
    search: bool = False
    baseline: str | None = None
    relevant_if: dict = field(default_factory=dict)   # 이 신호가 하나도 없으면 planner 가 묻지 않음 (비용, J 문항)
    attach: bool = False      # 모델을 부르지 않고 근거 원문(발췌)을 답으로 붙임 (설명형 질문, 2026.11.18 비용)
    generic: bool = False     # 사례 이름을 검색어에서 뺌 → 근거가 사례끼리 같아 캐시를 함께 씀

    @property
    def base_id(self) -> str:
        """하위 질문이면 부모 id, 아니면 자기 id. when 식과 composer 는 이 id 로 찾는다."""
        return self.parent_id or self.id

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        return cls(
            id=str(d["id"]), stage=str(d["stage"]), text=str(d["text"]),
            answer_type=str(d["answer_type"]),
            min_laws=[MinLaw.from_dict(m) for m in d.get("min_laws") or []],
            needs_facts=list(d.get("needs_facts") or []),
            when=d.get("when") or None,
            feeds=list(d.get("feeds") or []),
            parent_id=d.get("parent_id"),
            component=d.get("component"),
            search=bool(d.get("search", False)),
            baseline=d.get("baseline"),
            relevant_if=dict(d.get("relevant_if") or {}),
            attach=bool(d.get("attach", False)),
            generic=bool(d.get("generic", False)),
        )

    @classmethod
    def from_checklist(cls, entry: dict) -> "Question":
        """checklist.yaml 항목 → Question. 항목의 질문 문장 칸 이름은 question."""
        d = dict(entry)
        d["text"] = d.pop("question")
        q = cls.from_dict(d)
        problems = check_question(q)
        if problems:
            raise ValueError(f"체크리스트 {q.id}: " + "; ".join(problems))
        return q


def load_checklist(path: str | Path) -> list[Question]:
    """checklist.yaml → [Question]. 모든 모듈은 이 함수로 읽는다 (YAML 'no' 키 문제를 한 곳에서 처리).
    id 중복, when 식이 없는 질문을 기다림 → ValueError."""
    import yaml                                         # 의존성 pyyaml (state.py 를 쓰는 곳 모두 이미 있음)
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    qs = [Question.from_checklist(e) for e in data.get("questions") or []]
    ids = [q.id for q in qs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"체크리스트 id 중복: {dup}")
    for q in qs:
        unknown = [d for d in when_dependencies(q.when) if d not in ids]
        if unknown:
            raise ValueError(f"체크리스트 {q.id}: when 이 없는 질문 {unknown} 를 기다림")
    return qs


def sub_question_id(parent_id: str, component: int) -> str:
    """혼합 사례의 성분별 하위 질문 id. 예: ("D-1", 2) → "D-1.c2"."""
    return f"{parent_id}.c{component}"


def check_question(q: Question) -> list[str]:
    """Question 형식 검사. 문제 목록 (비어 있으면 통과)."""
    problems: list[str] = []
    if not re.fullmatch(r"[A-Z]-\d+(?:\.c\d+)?", q.id):
        problems.append(f"id 형식이 아님: {q.id}")
    if q.answer_type not in ANSWER_TYPES:
        problems.append(f"answer_type 이 아님: {q.answer_type}")
    if q.when:
        try:
            parse_when(q.when)
        except WhenSyntaxError as e:
            problems.append(str(e))
    if q.parent_id and not q.id.startswith(q.parent_id + ".c"):
        problems.append(f"하위 질문 id 가 부모와 안 맞음: {q.id} / {q.parent_id}")
    return problems


@dataclass
class Candidate:
    """retriever 결과: 질문 하나에 대한 조문 후보.

    key    : 조문 키 ("16의13", "B4의3"). 항·호 키가 아니라 조문·별표 단위
    source : min_laws(체크리스트 지정) / search(검색) / citation(인용 따라가기)
    via    : citation 이면 출발 조문 "{law}:{key}"
    moved  : min_laws 를 번호로 못 찾아 제목으로 다시 찾았으면 True (조문 번호 이동)
    """
    law: str
    key: str
    title: str
    score: float
    source: str
    via: str | None = None
    moved: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(**{k: d.get(k) for k in ("law", "key", "title", "score", "source", "via")},
                   moved=bool(d.get("moved", False)))


@dataclass
class EvidenceItem:
    """근거 묶음에 들어간 조문 하나 (조문 통째 또는 별표 발췌).

    ref      : make_ref() 형식 전체 ("WMA_RULE:B4의3@20261112#06131c0d8446eddb")
    text     : judge 에게 보이는 원문. 인용 대조도 이 글로 한다
    excerpt  : 긴 별표에서 분류번호·R코드 행만 잘라 넣었으면 True (요약 금지, 행 그대로)
    complete : 호 개수 검증 통과 여부 (lawstore.articles.article 의 complete)
    """
    ref: str
    law: str
    key: str
    title: str
    effective: str
    text: str
    complete: bool = True
    problems: list[str] = field(default_factory=list)
    source: str = "search"
    excerpt: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceItem":
        return cls(
            ref=d["ref"], law=d["law"], key=d["key"], title=d.get("title", ""),
            effective=str(d.get("effective", "")), text=d.get("text", ""),
            complete=bool(d.get("complete", True)), problems=list(d.get("problems") or []),
            source=d.get("source", "search"), excerpt=bool(d.get("excerpt", False)),
        )


@dataclass
class EvidencePack:
    """질문 하나의 근거 묶음. 만들 때는 make_pack() 을 쓴다 (정렬·해시를 맞추기 위해)."""
    question_id: str
    items: list[EvidenceItem]
    truncated: bool = False
    pack_hash: str = ""

    def get(self, ref: str) -> EvidenceItem | None:
        """ref 로 조문 찾기. 정확히 같은 ref 가 없으면 해시를 뺀 ref(법령:키@시행일)로 찾는다."""
        for it in self.items:
            if it.ref == ref:
                return it
        base = ref_base(ref)
        if base is None:
            return None
        hits = [it for it in self.items if ref_base(it.ref) == base]
        return hits[0] if len(hits) == 1 else None

    @classmethod
    def from_dict(cls, d: dict) -> "EvidencePack":
        return cls(question_id=d["question_id"], items=[EvidenceItem.from_dict(i) for i in d.get("items") or []],
                   truncated=bool(d.get("truncated", False)), pack_hash=d.get("pack_hash", ""))


def make_pack(question_id: str, items: list[EvidenceItem], truncated: bool = False) -> EvidencePack:
    """근거 묶음 만들기: (law, key, ref) 순 정렬 + 해시. 같은 조문(ref)이 두 번 들어오면 하나만 남긴다."""
    seen: dict[str, EvidenceItem] = {}
    for it in items:
        seen.setdefault(it.ref, it)
    ordered = sorted(seen.values(), key=lambda it: (it.law, it.key, it.ref))
    return EvidencePack(question_id=question_id, items=ordered, truncated=truncated,
                        pack_hash=compute_pack_hash(ordered, truncated))


@dataclass
class JudgeAnswer:
    """judge 모델 하나의 답 (검증 전).

    value         : answer_type 별 형식 (4절)
    missing_facts : 판단에 필요한데 사례에 없는 사실 이름 (needs_facts 이름 체계)
    raw           : 모델 원문 응답 (감사용). 인증값이 들어갈 일은 없지만 로그에 넣기 전 그대로 둔다
    """
    question_id: str
    model: str
    answer: str
    value: dict = field(default_factory=dict)
    conditions: list[Condition] = field(default_factory=list)
    quotes: list[QuoteRef] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    reason: str = ""
    raw: str = ""
    prompt_version: str = ""
    evidence_gaps: list[str] = field(default_factory=list)   # 근거 묶음에 없는 법 내용 (사례 사실 아님 → 재검색)

    @classmethod
    def from_dict(cls, d: dict) -> "JudgeAnswer":
        return cls(
            question_id=d["question_id"], model=d.get("model", ""), answer=d.get("answer", "unknown"),
            value=dict(d.get("value") or {}), conditions=list(d.get("conditions") or []),
            quotes=list(d.get("quotes") or []), missing_facts=list(d.get("missing_facts") or []),
            reason=d.get("reason", ""), raw=d.get("raw", ""), prompt_version=d.get("prompt_version", ""),
            evidence_gaps=list(d.get("evidence_gaps") or []),
        )


@dataclass
class Verdict:
    """verifier 결과: 질문 하나의 최종 판단.

    answer/value/conditions/quotes : 채택된 답 (채택 못 했으면 answer="unknown", 나머지 비움)
    retries    : 이 질문을 재검색한 횟수
    retry_hint : 재검색할 때 검색어에 더할 말 (빠진 조문 이름 등). retriever 가 읽음
    missing_facts : needs_info 일 때 물어야 할 사실 이름
    flags      : FLAGS 중 해당하는 것
    pack_hash  : 판단에 쓴 근거 묶음 해시 (재현용)
    """
    question_id: str
    status: str
    answer: str = "unknown"
    value: dict = field(default_factory=dict)
    conditions: list[Condition] = field(default_factory=list)
    quotes: list[QuoteRef] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    retries: int = 0
    retry_hint: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    pack_hash: str = ""

    @property
    def final(self) -> bool:
        return self.status in FINAL_STATUSES

    @classmethod
    def from_dict(cls, d: dict) -> "Verdict":
        return cls(
            question_id=d["question_id"], status=d["status"], answer=d.get("answer", "unknown"),
            value=dict(d.get("value") or {}), conditions=list(d.get("conditions") or []),
            quotes=list(d.get("quotes") or []), votes=list(d.get("votes") or []),
            retries=int(d.get("retries", 0)), retry_hint=list(d.get("retry_hint") or []),
            missing_facts=list(d.get("missing_facts") or []), flags=list(d.get("flags") or []),
            pack_hash=d.get("pack_hash", ""),
        )


# ════════════════════════════════════════════════════════════════
# 3. LangGraph 상태
# ════════════════════════════════════════════════════════════════

def merge_dict(old: dict | None, new: dict | None) -> dict:
    """질문 id 단위로 합치는 reducer. new 에 있는 질문만 바뀐다 (값은 통째로 교체)."""
    out = dict(old or {})
    out.update(new or {})
    return out


class AgentState(TypedDict, total=False):
    """LangGraph 상태.

    case        : 파트 1 case 원본 (dict, 고치지 않음)
    facts       : extract_facts(case) 결과 {"values", "missing", "not_applicable"}
    questions   : planner 결과 (체크리스트 전부 + 하위 질문)
    candidates  : {qid: [Candidate]}        retriever
    packs       : {qid: EvidencePack}       evidence
    answers     : {qid: [JudgeAnswer]}      judge (재판단하면 그 질문 목록 통째 교체)
    verdicts    : {qid: Verdict}            verifier
    retry_count : {qid: int}                verifier 가 재검색 보낼 때 올림
    routes      : [RouteDraft]              composer (gates.models.RouteDraft)
    audit       : [dict]                    노드별 기록 (덧붙이기)
    errors      : [dict]                    {"node", "question_id", "model", "message"} (덧붙이기)
    """
    case: dict
    facts: dict
    questions: list[Question]
    candidates: Annotated[dict[str, list[Candidate]], merge_dict]
    packs: Annotated[dict[str, EvidencePack], merge_dict]
    answers: Annotated[dict[str, list[JudgeAnswer]], merge_dict]
    verdicts: Annotated[dict[str, Verdict], merge_dict]
    retry_count: Annotated[dict[str, int], merge_dict]
    routes: list
    audit: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]


def initial_state(case: dict) -> AgentState:
    """graph 시작 상태. facts 는 여기서 한 번 뽑는다."""
    return AgentState(case=case, facts=extract_facts(case), questions=[], candidates={}, packs={},
                      answers={}, verdicts={}, retry_count={}, routes=[], audit=[], errors=[])


def error_record(node: str, message: str, question_id: str | None = None, model: str | None = None) -> dict:
    """errors 칸에 넣을 기록 하나. message 에 인증값을 넣지 않는다."""
    return {"node": node, "question_id": question_id, "model": model, "message": message}


# ════════════════════════════════════════════════════════════════
# 4. answer_type 별 value 형식
# ════════════════════════════════════════════════════════════════
#
#   yes_no, yes_no_cond : {}  또는 {"items": [...]}  — 판단에 딸린 코드 목록이 있을 때만
#                         (예: A-1 맞는 분류번호, A-4 섞인 채로 보는 분류번호). 조건은 JudgeAnswer.conditions 에
#   list    : {"items": ["R-3-2", "R-3-3"], "attrs": {"R-3-2": {"pre_analysis": false}}}
#             items 는 코드(R코드·처리방법 코드·분류번호·허가 종류). attrs 는 선택, 항목별 부가 값
#             answer: yes = 항목 있음 / no = 해당 없음(items 빈 목록) / conditional = 조건부 항목 / unknown
#   compare : {"criterion": "월 평균 1톤 이상", "case_value": 1.21, "unit": "ton/월", "result": "met"}
#             result: met / not_met / unknown. answer 가 yes·no 이면 result 는 unknown 이 아니어야 함
#   text    : {"text": "수탁처리능력 확인서를 받고 서면으로 계약한다 …"}
#
#   answer 가 unknown 이면 value 는 {} 여도 된다.

class ItemsValue(TypedDict, total=False):
    items: list[str]


class ListValue(TypedDict, total=False):
    items: list[str]
    attrs: dict[str, dict[str, Any]]


class CompareValue(TypedDict, total=False):
    criterion: str
    case_value: str | int | float | None
    unit: str
    result: str


class TextValue(TypedDict):
    text: str


VALUE_KEYS: dict[str, set[str]] = {
    "yes_no": {"items"},
    "yes_no_cond": {"items"},
    "list": {"items", "attrs"},
    "compare": {"criterion", "case_value", "unit", "result"},
    "text": {"text"},
}

_SCALAR = (str, int, float, bool, type(None))


def _check_items(value: dict, problems: list[str]) -> list[str]:
    items = value.get("items", [])
    if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
        problems.append("items 는 빈 칸 없는 문자열 목록이어야 함")
        return []
    if len(set(items)) != len(items):
        problems.append("items 에 같은 값이 두 번 있음")
    return items


def validate_value(answer_type: str, value: Any, answer: str | None = None) -> list[str]:
    """value 형식 검사. 문제 목록을 돌려준다 (비어 있으면 통과).

    answer 를 주면 answer 와 value 가 서로 맞는지도 본다 (예: list 인데 no 이면 items 가 비어야 함).
    코드가 사실 데이터에 있는지는 여기서 보지 않는다 (verifier 2겹, fact_tools).
    """
    problems: list[str] = []
    if answer_type not in ANSWER_TYPES:
        return [f"answer_type 이 아님: {answer_type}"]
    if answer is not None and answer not in ALLOWED_ANSWERS[answer_type]:
        problems.append(f"{answer_type} 에 쓸 수 없는 answer: {answer}")
    if not isinstance(value, dict):
        return problems + ["value 는 객체(dict)여야 함"]
    extra = set(value) - VALUE_KEYS[answer_type]
    if extra:
        problems.append(f"{answer_type} value 에 없는 칸: {sorted(extra)}")
    decided = answer is not None and answer != "unknown"

    if answer_type in ("yes_no", "yes_no_cond"):
        if "items" in value:
            _check_items(value, problems)

    elif answer_type == "list":
        items = _check_items(value, problems)
        attrs = value.get("attrs", {})
        if not isinstance(attrs, dict):
            problems.append("attrs 는 객체(dict)여야 함")
        else:
            for k, v in attrs.items():
                if k not in items:
                    problems.append(f"attrs 의 {k} 가 items 에 없음")
                if not isinstance(v, dict) or not all(isinstance(x, _SCALAR) for x in v.values()):
                    problems.append(f"attrs[{k}] 는 단순 값만 든 객체여야 함")
        if answer == "no" and items:
            problems.append("answer 가 no 인데 items 가 있음")
        if answer in ("yes", "conditional") and not items:
            problems.append(f"answer 가 {answer} 인데 items 가 비어 있음")

    elif answer_type == "compare":
        result = value.get("result")
        if result is not None and result not in COMPARE_RESULTS:
            problems.append(f"result 는 {COMPARE_RESULTS} 중 하나: {result}")
        if "case_value" in value and not isinstance(value["case_value"], (str, int, float, type(None))):
            problems.append("case_value 는 숫자·문자열·null")
        for k in ("criterion", "unit"):
            if k in value and not isinstance(value[k], str):
                problems.append(f"{k} 는 문자열")
        if decided:
            if not value.get("criterion"):
                problems.append("criterion 이 없음")
            if result is None:
                problems.append("result 가 없음")
        if answer in ("yes", "no") and result == "unknown":
            problems.append(f"answer 가 {answer} 인데 result 가 unknown")

    elif answer_type == "text":
        text = value.get("text")
        if text is not None and not isinstance(text, str):
            problems.append("text 는 문자열")
        if answer in ("yes", "conditional") and not (isinstance(text, str) and text.strip()):
            problems.append(f"answer 가 {answer} 인데 text 가 비어 있음")
    return problems


def core_value(answer_type: str, value: dict) -> Any:
    """합의 비교에 쓰는 핵심 값 (해시 가능). 목록은 집합으로 비교한다.

    yes_no*  : items 집합 (없으면 None)
    list     : (항목, attrs 중 bool·숫자 값) 의 집합 — 문자열 attrs(설명)는 모델마다 달라서 뺀다
    compare  : result
    text     : None (문장은 모델마다 달라서 answer 만 비교)
    """
    value = value or {}
    if answer_type in ("yes_no", "yes_no_cond"):
        items = value.get("items")
        return frozenset(items) if items else None
    if answer_type == "list":
        attrs = value.get("attrs") or {}
        out = []
        for it in value.get("items") or []:
            a = attrs.get(it) or {}
            out.append((it, tuple(sorted((k, v) for k, v in a.items()
                                         if isinstance(v, (bool, int, float))))))
        return frozenset(out)
    if answer_type == "compare":
        return value.get("result")
    return None


def consensus_key(answer_type: str, answer: str, value: dict) -> tuple:
    """두 답이 '같다' 의 기준. verifier 합의 투표는 이 값이 같은 답끼리 센다."""
    return (answer, core_value(answer_type, value))


# ════════════════════════════════════════════════════════════════
# 5. 해시
# ════════════════════════════════════════════════════════════════

def to_jsonable(obj: Any) -> Any:
    """dataclass·집합·enum 이 섞인 값을 JSON 에 넣을 수 있게. to_dict() 가 있으면 그것을 쓴다 (RouteDraft)."""
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return to_jsonable(obj.to_dict())
    if is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((to_jsonable(v) for v in obj), key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    return obj


def canonical_json(obj: Any) -> str:
    """해시용 고정 직렬화: 키 정렬, 공백 없음, 한글 그대로."""
    return json.dumps(to_jsonable(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_pack_hash(items: list[EvidenceItem], truncated: bool) -> str:
    """근거 묶음 해시. 정렬된 (ref, text, complete, excerpt) + truncated.
    source(min_laws/search) 는 넣지 않는다 — 같은 원문이면 어떻게 찾았든 같은 판단이어야 하므로."""
    body = {
        "items": [{"ref": it.ref, "text": it.text, "complete": it.complete, "excerpt": it.excerpt}
                  for it in sorted(items, key=lambda it: (it.law, it.key, it.ref))],
        "truncated": truncated,
    }
    return sha256_hex(canonical_json(body))


def facts_hash(facts: dict, names: list[str] | None = None) -> str:
    """사례 사실 해시. names 를 주면 그 사실만 (질문의 needs_facts) — 관계없는 사실이 바뀌어도 캐시 유지."""
    values = facts.get("values", facts)
    if names is not None:
        values = {n: values.get(n) for n in sorted(set(names))}
    return sha256_hex(canonical_json(values))


def cache_key(question_id: str, pack_hash: str, facts_hash_: str, model: str, prompt_version: str) -> str:
    """판단 캐시 키 (judge·store/cache.py 가 같이 씀)."""
    return sha256_hex(canonical_json([question_id, pack_hash, facts_hash_, model, prompt_version]))


def safe_name(text: str) -> str:
    """파일 이름에 쓸 수 있게. "anthropic/claude-sonnet-5.5" → "anthropic__claude-sonnet-5.5".
    mock 응답 파일: tests/fixtures/mock_llm/{safe_name(model)}_{safe_name(question_id)}.json"""
    return re.sub(r"[/\\:*?\"<>|\s]", "__", text)


# ════════════════════════════════════════════════════════════════
# 6. 조문 ref
# ════════════════════════════════════════════════════════════════
# 형식: "{LAW}:{조문키}@{시행일YYYYMMDD}#{해시}"   (lawstore.articles.article 의 ref 와 같음)
# 예   : "WMA_RULE:B4의3@20261112#06131c0d8446eddb"
# judge 가 해시를 빼고 "WMA_RULE:B4의3@20261112" 로 적어도 EvidencePack.get 이 찾는다.

RX_REF = re.compile(r"^(?P<law>[A-Z][A-Z0-9_]*):(?P<key>[^@#]+)@(?P<effective>\d{8})(?:#(?P<hash>[0-9A-Za-z]+))?$")


def make_ref(law: str, key: str, effective: str, hash_: str | None = None) -> str:
    ref = f"{law}:{key}@{effective}"
    return f"{ref}#{hash_}" if hash_ else ref


def parse_ref(ref: str) -> dict | None:
    """ref → {"law", "key", "effective", "hash"} (hash 없으면 None). 형식이 아니면 None."""
    m = RX_REF.match((ref or "").strip())
    return m.groupdict() if m else None


def ref_base(ref: str) -> str | None:
    """해시를 뺀 ref. 형식이 아니면 None."""
    p = parse_ref(ref)
    return make_ref(p["law"], p["key"], p["effective"]) if p else None


def norm_text(text: str) -> str:
    """인용 대조용 정규화: 공백·줄바꿈·가운뎃점(· ㆍ ・ ‧ ･)·따옴표(" “ ” ' ‘ ’ 「 」 『 』)를 지운다. 그 밖의 글자는 그대로.
    따옴표: 원문은 “ ” 인데 모델은 " 로 옮겨 적어 인용이 버려졌음 (G-3, 2026.11.18)."""
    return re.sub(r"[\s·ㆍ・‧･\"“”„‟'‘’「」『』]+", "", text or "")


RX_DEF_PAREN = re.compile(r"\(이하\s*[\"“'‘]?([^()\"“”'‘’]{1,30})[\"”'’]?\s*(?:이)?라\s*한다\)")


def quote_in(quote: str, text: str) -> bool:
    """quote 가 text 안에 글자 그대로 있는가 (norm_text 후 부분 문자열). 빈 인용은 False.
    정의 괄호 '(이하 "…"라 한다)' 만 빠진 인용은 같은 문장으로 본다 (모델이 줄여 옮김, G-3 2026.11.18)."""
    q = norm_text(quote)
    if not q:
        return False
    if q in norm_text(text):
        return True
    if norm_text(RX_DEF_PAREN.sub("", quote)) in norm_text(RX_DEF_PAREN.sub("", text)):
        return True
    # 정의된 말로 바꿔 쓴 인용: 원문 '도지사(이하 "시ㆍ도지사"라 한다) 또는' → 모델 '시ㆍ도지사 또는'
    term = lambda x: RX_DEF_PAREN.sub(lambda m: m.group(1), x)          # noqa: E731
    return norm_text(term(quote)) in norm_text(term(text))


# ════════════════════════════════════════════════════════════════
# 7. when 조건
# ════════════════════════════════════════════════════════════════
# 문법 (checklist.yaml 에 쓰인 것만):
#   식   := 항 (" and " 항)*
#   항   := QID == 답 | QID != 답 | QID in [답, 답] | QID has items | QID has 토큰 | QID includes 토큰
#   QID  := "A-2" 처럼 (하위 질문 아닌 기본 id)
#   has 토큰 / includes 토큰 : 그 질문 value.items 중 토큰과 같거나 "토큰-" 으로 시작하는 것이 있음
#                              (예: "D-1 has R-9" 는 R-9, R-9-1, R-9-2 … 중 하나)
#
# 평가 결과 (eval_when):
#   True  : 묻는다      False : 묻지 않는다 (not_applicable)      None : 앞 질문이 아직 안 끝나서 모름
# 앞 질문이 needs_info·needs_human·unknown 이면 "그럴 수도 있다"로 보고 True (모르면 묻는다 — 안전 방향).
# 앞 질문이 not_applicable 이면 answer="no", items=[] 로 본다.

class WhenSyntaxError(ValueError):
    pass


RX_QID = r"[A-Z]-\d+"
RX_TERM = re.compile(
    rf"^(?P<qid>{RX_QID})\s+(?:"
    rf"(?P<op>==|!=)\s*(?P<ans>\w+)"
    rf"|in\s*\[(?P<set>[^\]]*)\]"
    rf"|has\s+items(?P<has_items>)"
    rf"|(?P<has_op>has|includes)\s+(?P<tok>[\w-]+)"
    rf")$"
)


def parse_when(expr: str | None) -> list[dict]:
    """when 식 → 항 목록 [{"qid", "kind", "arg"}]. kind: eq / ne / in / has_items / has."""
    if not expr or not expr.strip():
        return []
    terms = []
    for part in re.split(r"\s+and\s+", expr.strip()):
        m = RX_TERM.match(part.strip())
        if not m:
            raise WhenSyntaxError(f"when 식을 읽을 수 없음: {part!r} (전체: {expr!r})")
        qid = m.group("qid")
        if m.group("op"):
            ans = m.group("ans")
            if ans not in ANSWERS:
                raise WhenSyntaxError(f"when 식의 답이 {ANSWERS} 가 아님: {ans!r}")
            terms.append({"qid": qid, "kind": "eq" if m.group("op") == "==" else "ne", "arg": ans})
        elif m.group("set") is not None:
            vals = [v.strip() for v in m.group("set").split(",") if v.strip()]
            bad = [v for v in vals if v not in ANSWERS]
            if bad or not vals:
                raise WhenSyntaxError(f"when 식 in [...] 값이 잘못됨: {vals!r}")
            terms.append({"qid": qid, "kind": "in", "arg": vals})
        elif m.group("has_items") is not None:
            terms.append({"qid": qid, "kind": "has_items", "arg": None})
        else:
            terms.append({"qid": qid, "kind": "has", "arg": m.group("tok")})
    return terms


def when_dependencies(expr: str | None) -> list[str]:
    """when 식이 기다리는 질문 id 목록."""
    return sorted({t["qid"] for t in parse_when(expr)})


def _term_true(term: dict, answer: str, items: list[str]) -> bool:
    kind, arg = term["kind"], term["arg"]
    if kind == "eq":
        return answer == arg
    if kind == "ne":
        return answer != arg
    if kind == "in":
        return answer in arg
    if kind == "has_items":
        return bool(items)
    return any(it == arg or it.startswith(arg + "-") for it in items)


def eval_when(expr: str | None, verdicts: dict[str, Verdict]) -> bool | None:
    """when 식 평가. True 묻는다 / False 안 묻는다 / None 앞 질문 대기."""
    terms = parse_when(expr)
    pending = False
    for t in terms:
        v = verdicts.get(t["qid"])
        if v is None or not v.final:
            pending = True
            continue
        if v.status == "not_applicable":
            ok = _term_true(t, "no", [])
        elif v.status != "agreed" or v.answer == "unknown":
            ok = True                                   # 모르면 묻는다
        else:
            ok = _term_true(t, v.answer, list((v.value or {}).get("items") or []))
        if not ok:
            return False                                # and 식: 하나라도 거짓이면 거짓
    return None if pending else True


# ════════════════════════════════════════════════════════════════
# 8. 사례 사실 (파트 1 case → needs_facts 이름)
# ════════════════════════════════════════════════════════════════
# checklist.yaml 의 needs_facts 이름과 파트 1 case(v1.0) 칸의 대응.
# 앞에 있는 경로부터 보고 처음으로 값이 있는 것을 쓴다. "components[].x" 는 성분별 목록.
# 여기 없는 이름은 extract_facts 가 missing 으로 둔다 → judge 는 unknown + missing_facts.

FACT_PATHS: dict[str, list[str]] = {
    "waste_code": ["classification.selected_code", "classification.reported_code"],
    "waste_name": ["name"],
    "process": ["process_description"],
    "material_form": ["material_form"],
    "components": ["components"],
    "analysis": ["analysis"],
    "analysis.pcb": ["analysis.pcb_ppm", "analysis.pcb"],
    "heating_value": ["analysis.heating_value_kcal"],
    "combustible": ["combustible"],
    "mixed_with_other": ["mixed_with_other"],
    "separable": ["components[].separable"],
    "foreign_matter": ["foreign_matter"],
    "item_specific": ["item_specific"],
    "storage_state": ["storage_state"],
    "contamination": ["contamination"],
    "hazardous_suspect": ["hazardous_suspect"],
    "banned_restricted_substance": ["banned_restricted_substance"],   # 금지·제한 물질 포함 여부 (파트 1)
    "waste_status": ["designated_waste_status"],
    "quantity": ["quantity"],
    "quantity.monthly_ton": ["quantity.monthly_ton"],
    "region": ["region"],
    "existing_declaration": ["existing_declaration"],
    "origin": ["origin"],                       # 파트 1 v1.0 에 없음 → missing
    "business_type": ["business_type"],         # 파트 1 v1.0 에 없음 → missing
}

# 사례가 아니라 앞 판단·경로에서 정해지는 값. missing 으로 보지 않는다 (composer 가 경로마다 채움).
DERIVED_FACTS: frozenset[str] = frozenset({"gate", "r_codes", "treatment_codes", "plan_check"})


def _get_path(case: dict, path: str) -> tuple[bool, Any]:
    """case 에서 경로 값. (찾음 여부, 값). components[].x 는 성분별 목록 (모두 None 이면 못 찾음)."""
    if "[]." in path:
        head, tail = path.split("[].", 1)
        ok, arr = _get_path(case, head)
        if not ok or not isinstance(arr, list):
            return False, None
        vals = [_get_path(x, tail)[1] if isinstance(x, dict) else None for x in arr]
        return (any(v is not None for v in vals), vals)
    cur: Any = case
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return (cur is not None and cur != "" and cur != []), cur


def extract_facts(case: dict) -> dict:
    """파트 1 case → {"values": {이름: 값}, "missing": [이름], "explicit_missing": [이름], "not_applicable": [이름]}.

    case.not_applicable 에 경로가 있으면 그 사실은 not_applicable (묻지 않음).
    case.missing 에 경로가 있으면 값이 있어도 missing 으로 본다 (파트 1이 확인 못 한 값).
    missing          : 값이 없는 모든 사실 (입력 항목에 아예 없는 것 포함)
    explicit_missing : 그중 파트 1이 case.missing 으로 '확인 못 함' 이라고 밝힌 것
                       (나머지는 '제공되지 않음' — judge 가 다른 사실로 해당 여부를 볼 수 있음)
    """
    na_paths = set(case.get("not_applicable") or [])
    missing_paths = set(case.get("missing") or [])
    values: dict[str, Any] = {}
    missing: list[str] = []
    explicit: list[str] = []
    na: list[str] = []
    for name, paths in FACT_PATHS.items():
        if any(p in na_paths for p in paths):
            values[name] = None
            na.append(name)
            continue
        found, val = False, None
        for p in paths:
            found, val = _get_path(case, p)
            if found:
                break
        if found and not any(p in missing_paths for p in paths):
            values[name] = val
        else:
            values[name] = None
            missing.append(name)
            if any(p in missing_paths for p in paths):
                explicit.append(name)
    for name, val in computed_facts(case).items():         # 계산할 수 있는 사실 (파트 1에 묻지 않게)
        values[name] = val
        if val is None:
            missing.append(name)
    return {"values": values, "missing": sorted(missing), "explicit_missing": sorted(explicit),
            "not_applicable": sorted(na)}


# 파트 1 값으로 계산하는 사실 (2026.11.17 데모: H-1 이 '1일 평균 배출량'을 파트 1에 물음 — 월 물량으로 계산 가능)
COMPUTED_FACTS: dict[str, str] = {
    "quantity.monthly_avg_ton": "계산 (파트 1 월별 물량 평균)",
    "quantity.daily_avg_kg": "계산 (파트 1 월별 물량 평균 × 1000 ÷ 30)",
    "quantity.yearly_ton": "계산 (파트 1 월별 물량 평균 × 12)",
}


def computed_facts(case: dict) -> dict[str, Any]:
    months = ((case.get("quantity") or {}).get("monthly_ton")) or []
    try:
        nums = [float(x) for x in months if x is not None]
    except (TypeError, ValueError):
        nums = []
    if not nums:
        return {k: None for k in COMPUTED_FACTS}
    avg = sum(nums) / len(nums)
    return {"quantity.monthly_avg_ton": round(avg, 3), "quantity.daily_avg_kg": round(avg * 1000 / 30, 1),
            "quantity.yearly_ton": round(avg * 12, 2)}


def facts_for(facts: dict, names: list[str]) -> dict:
    """질문 하나에 넘길 사실만 고르기 (judge 입력). "analysis.pcb" 처럼 점이 든 이름도 FACT_PATHS 이름 그대로."""
    values = facts.get("values", {})
    return {n: values.get(n) for n in names if n not in DERIVED_FACTS}


# ════════════════════════════════════════════════════════════════
# 9. 직렬화
# ════════════════════════════════════════════════════════════════

def state_to_json(state: AgentState, indent: int | None = None) -> str:
    """AgentState → JSON. routes 의 RouteDraft 는 to_dict() 로 (복원하면 dict)."""
    return json.dumps(to_jsonable(dict(state)), ensure_ascii=False, indent=indent, sort_keys=True)


def state_from_json(text: str) -> AgentState:
    """JSON → AgentState (자료형 복원). routes 는 dict 목록으로 남긴다."""
    d = json.loads(text)
    st: AgentState = AgentState()
    for k in ("case", "facts", "routes", "audit", "errors"):
        if k in d:
            st[k] = d[k]
    if "questions" in d:
        st["questions"] = [Question.from_dict(q) for q in d["questions"]]
    if "candidates" in d:
        st["candidates"] = {q: [Candidate.from_dict(c) for c in v] for q, v in d["candidates"].items()}
    if "packs" in d:
        st["packs"] = {q: EvidencePack.from_dict(p) for q, p in d["packs"].items()}
    if "answers" in d:
        st["answers"] = {q: [JudgeAnswer.from_dict(a) for a in v] for q, v in d["answers"].items()}
    if "verdicts" in d:
        st["verdicts"] = {q: Verdict.from_dict(v) for q, v in d["verdicts"].items()}
    if "retry_count" in d:
        st["retry_count"] = {q: int(n) for q, n in d["retry_count"].items()}
    return st
