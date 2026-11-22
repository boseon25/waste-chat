"""질문 차례 정하기 — retriever·evidence·judge·verifier·graph 가 모두 이 함수로 "이번에 처리할 질문"을 정한다.

차례 방식 (앞 답이 나온 질문부터):
  ready    : 아직 최종 판단이 없고 when 이 참이고 사실 의존(context.fact_dependencies)이 모두 끝난 질문
             + 재검색 대기(retry) 질문                                             → 이번 차례에 처리
  pending  : when 이 앞 질문을 기다리거나(eval_when = None), 필요한 사실을 확정할 질문이 안 끝남 → 다음 차례
  skip     : when 이 거짓 → verifier 가 not_applicable 로 기록
사실 의존(판단 누적): 분류번호는 A-1, 지정폐기물 여부는 A-2, R코드는 D-1, 처리방법 코드는 F-1 판단을 기다린다.
  → 1차 A-1·A-2·A-4 / 2차 B·C·F·G·H·J… / 3차 D-1·C-2… / 4차 D-2~D-5·I-1… / 5차 E-1·I-2 (보통 5차례).

약속 (각 노드가 지킬 것):
  - retriever·evidence·judge 는 ready_ids(state) 의 질문만 처리한다.
  - evidence 는 ready 질문마다 EvidencePack 을 반드시 만든다 (후보가 없으면 items 빈 묶음).
    → verifier 는 "이번 차례에 묶음이 새로 생긴 질문" 을 판단 대상으로 본다.
  - judge 는 ready 질문마다 answers[qid] 를 반드시 새로 넣는다 (모두 실패해도 빈 목록) — 재검색 때 지난 답이 남지 않게.
  - judge 는 호출 상한에 걸리면 errors 에 message 가 BUDGET_PREFIX 로 시작하는 기록을 남긴다.
"""
from __future__ import annotations

from law_agent.agents.context import fact_dependencies
from law_agent.state import AgentState, Question, eval_when

BUDGET_PREFIX = "budget_exceeded"
MAX_ROUNDS = 10                     # 안전장치 (보통 5차례 + 재검색): 차례(retriever 진입) 최대 횟수. 넘으면 남은 질문은 needs_human


def is_wide(q: Question, retries: int) -> bool:
    """근거를 넓게 모을 차례인가.
    좁게(1회차): 최소 조문 + 인용 1단계 + 검색 상위 몇 개, 글자 한도 작게 — 대부분 여기서 합의로 끝남
    넓게: 재검색(retries > 0) 이거나 검색이 핵심인 질문(J 단계, search=True)
    retriever·evidence 가 같은 함수로 정한다."""
    return bool(q.search) or retries > 0


def question_map(state: AgentState) -> dict[str, Question]:
    return {q.id: q for q in state.get("questions") or []}


def classify(state: AgentState) -> dict[str, list[str]]:
    """{"ready", "pending", "skip", "done"} — 질문 id 목록 (questions 순서 유지)."""
    verdicts = state.get("verdicts") or {}
    qids = {q.id for q in state.get("questions") or []}
    out: dict[str, list[str]] = {"ready": [], "pending": [], "skip": [], "done": []}
    for q in state.get("questions") or []:
        v = verdicts.get(q.id)
        if v is not None and v.status == "retry":
            out["ready"].append(q.id)
            continue
        if v is not None and v.final:
            out["done"].append(q.id)
            continue
        w = eval_when(q.when, verdicts)
        if w is True and any(d in qids and (verdicts.get(d) is None or not verdicts[d].final)
                             for d in fact_dependencies(q)):
            w = None                                  # 필요한 사실을 확정할 질문이 아직 안 끝남
        out["ready" if w is True else "skip" if w is False else "pending"].append(q.id)
    return out


def ready_ids(state: AgentState) -> list[str]:
    """이번 차례에 처리할 질문 id."""
    return classify(state)["ready"]


def ready_questions(state: AgentState) -> list[Question]:
    qm = question_map(state)
    return [qm[i] for i in ready_ids(state)]
