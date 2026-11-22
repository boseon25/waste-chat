"""입력 형식 검사 — 파트 1 case 를 judge 에 보내기 전에. 법 판단이 아니라 형식·존재만 본다.

결과 status (나쁜 순서):
  needs_review : 사람이 봐야 함 (분류번호 형식이 깨짐 — 고쳐서 다시 받아야 함)
  needs_info   : 파트 1에 물어야 함 (분류번호 없음, 상위 분류만, 하이픈 없는 번호, 분류표에 없는 번호)
  ok           : 진행. warnings 는 진행하되 기록 (신고·선택 번호 다름, 성분 비율 합이 100이 아님)
needs_info·needs_review 면 judge 를 부르지 않는다 (호출 낭비, 엉뚱한 근거로 판단하는 것을 막음).

분류번호가 분류표에 있는지는 사실 데이터(waste_codes_4.csv, fact_tools)로 본다. 표를 못 읽으면 그 검사만 건너뛴다.
"""
from __future__ import annotations

import re

RX_FULL = re.compile(r"^\d{2}-\d{2}-\d{2}$")
RX_MID = re.compile(r"^\d{2}-\d{2}$")
RX_DIGITS = re.compile(r"^\d{4,6}$")
STATUS_ORDER = {"ok": 0, "needs_info": 1, "needs_review": 2}


def _codes(case: dict) -> tuple[str | None, str | None, list[str]]:
    cls = case.get("classification") or {}
    comps = [c.get("waste_code") for c in case.get("components") or [] if isinstance(c, dict) and c.get("waste_code")]
    return cls.get("selected_code") or None, cls.get("reported_code") or None, comps


def _exists(code: str) -> bool | None:
    try:
        from law_agent.tools.fact_tools import waste_code_exists
        return waste_code_exists(code)
    except Exception:                                # noqa: BLE001 — 표가 없으면 이 검사만 건너뜀
        return None


def check_code(code: str) -> tuple[str, str | None]:
    """분류번호 하나 → (status, 문제 설명)."""
    c = code.strip()
    if RX_FULL.match(c):
        if _exists(c) is False:
            return "needs_info", f"분류번호 {c} 가 분류표(시행규칙 별표 4)에 없음 — 번호 확인 필요"
        return "ok", None
    if RX_MID.match(c):
        return "needs_info", f"분류번호 {c} 는 중분류까지만 — 세부 분류번호(00-00-00) 필요"
    if RX_DIGITS.match(c):
        return "needs_info", f"분류번호 {c} 에 하이픈이 없음 — 00-00-00 형식으로 확인 필요"
    return "needs_review", f"분류번호 {c} 의 형식이 잘못됨"


def check_input(case: dict) -> dict:
    """{"status", "issues": [{"field", "status", "text"}], "warnings": [문장]}"""
    issues: list[dict] = []
    warnings: list[str] = []
    if str(case.get("schema_version", "")) not in ("1.0",):
        warnings.append(f"schema_version {case.get('schema_version')!r} — 1.0 기준으로 읽음")
    selected, reported, comp_codes = _codes(case)
    main = selected or reported
    if not main and not comp_codes:
        issues.append({"field": "classification.selected_code", "status": "needs_info",
                       "text": "분류번호가 없음 — 파트 1에 분류번호 확인 필요"})
    for field, code in [("classification.selected_code", main)] + \
            [(f"components[{i}].waste_code", c) for i, c in enumerate(comp_codes)]:
        if not code:
            continue
        st, text = check_code(code)
        if st != "ok":
            issues.append({"field": field, "status": st, "text": text})
    if selected and reported and selected != reported:
        warnings.append(f"신고 분류번호 {reported} 와 선택 분류번호 {selected} 가 다름 — 선택 번호로 판단")
    shares = [c.get("share_pct") for c in case.get("components") or [] if isinstance(c, dict)]
    if shares and all(isinstance(x, (int, float)) for x in shares) and abs(sum(shares) - 100) > 0.5:
        warnings.append(f"성분 비율 합이 {sum(shares)}% — 100% 가 아님")
    status = max((i["status"] for i in issues), key=lambda s: STATUS_ORDER[s], default="ok")
    return {"status": status, "issues": issues, "warnings": warnings}
