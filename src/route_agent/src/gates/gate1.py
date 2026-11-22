"""관문 1 — 지정 순환자원 판매.

순환자원 지정 고시 품목이면, 고시 조건을 맞춰 폐기물이 아닌 원료로 판매하는 경로를 만든다.

판정표: designated_resources (순환자원 지정 고시 별표)
입력:   WasteCase(파트 1 case), PreliminaryResult(사전검증)
출력:   list[RouteDraft]
        - 지정 품목이 아니면 빈 목록
        - 조건을 어기면 REJECTED 경로 1개 (왜 안 되는지 보여 주기 위해 남김)

판정 순서
1. 분류번호가 지정 품목인지 (아니면 경로 없음)
2. 품목 한정 (IC 트레이, 알루미늄·구리, 도정 공장 쌀겨, 그 밖의 적용범위 단서)
3. 고시 조건: 다른 폐기물과 섞임, 이물질 제거, 보관 상태
"""
from __future__ import annotations

import re

from preliminary.models import PreliminaryResult, WasteCase

from .models import RouteDraft, Status, Step
from .tables import Tables, split

NONFERROUS = {"알루미늄": "알루미늄", "al": "알루미늄", "aluminum": "알루미늄",
              "구리": "구리", "cu": "구리", "copper": "구리"}
STORAGE_CODES = {"51-17-25", "51-19-00"}          # 커피찌꺼기, 왕겨·쌀겨: 보관 방법까지 고시 조건
GRAIN_CODE = "51-19-00"


def _item_rows(code: str, tables: Tables) -> list[dict]:
    return [r for r in tables.designated if r["분류번호"] == code]


def _scope_note(row: dict) -> str:
    """적용범위 원문에서 '…에 한하여' / '다만 …' 부분만 뽑아 질문에 쓴다."""
    text = re.sub(r"\s+", " ", row["적용범위_원문"]).replace("적용범위 ", "")
    m = re.search(r"다만,?\s*([^.]+)\.", text)
    return (m.group(1).strip() + " 제외") if m else text[:80]


METHOD_KEYWORDS = [
    ("이물질", "이물질을 고시 기준치 이하로 제거"),
    ("색상별로 선별", "색상별로 선별"),
    ("파쇄ㆍ분쇄", "파쇄·분쇄시설로 일정 규격으로 파쇄"),
    ("압축시설", "압축시설로 일정 규격으로 압축"),
    ("절단시설", "절단시설로 일정 규격으로 절단"),
    ("별표 5의3", "폐기물 재활용 기준(시행규칙 별표 5의3)에 적합"),
    ("용도로 사용하려는 자에게 공급", "고시 용도로 사용하려는 자에게 공급"),
]


def _method_summary(row: dict) -> str:
    """방법기준 원문에 나오는 작업만 순서대로 나열한다 (원문은 근거 칸으로 확인)."""
    text = re.sub(r"\s+", " ", row["방법기준_원문"])
    found = [label for key, label in METHOD_KEYWORDS if key in text]
    if "압축시설" in text and "절단시설" in text:
        found = [f for f in found if not f.startswith(("압축시설", "절단시설"))] + ["절단시설 또는 압축시설로 일정 규격으로 절단·압축"]
    return "고시 방법기준: " + (", ".join(dict.fromkeys(found)) or "원문 확인")


# ----------------------------------------------------------------------
# 2. 품목 한정
# ----------------------------------------------------------------------
def _pick_rows(code: str, rows: list[dict], case: WasteCase) -> tuple[list[dict], str | None, dict | None]:
    """
    품목 한정을 판정한다.
    반환: (해당 행들, 판정 결과, 질문)
      판정 결과: None = 해당, "NOT_ITEM" = 고시 품목 아님(경로 없음),
                "REJECT:<이유>" = 발생자 조건 불충족, "ASK" = 모름
    """
    spec = case.item_specific or {}

    if code == "51-03-01":                                     # 폐합성수지 중 IC 트레이만
        v = spec.get("ic_tray")
        if v is True:
            return rows, None, None
        if v is False:
            return rows, "NOT_ITEM", None
        return rows, "ASK", {"field": "item_specific.ic_tray", "text": "반도체 IC 트레이(IC-Tray)인가요?"}

    if code == "51-29-02":                                     # 비철금속 중 알루미늄·구리만
        v = spec.get("nonferrous_material")
        if v is None:
            return rows, "ASK", {"field": "item_specific.nonferrous_material",
                                 "text": "비철금속 재질이 알루미늄인가요, 구리인가요, 다른 금속인가요?"}
        name = NONFERROUS.get(str(v).strip().lower())
        hit = [r for r in rows if name and name in r["품목"]]
        return (hit, None, None) if hit else (rows, "NOT_ITEM", None)

    if code == GRAIN_CODE:                                     # 쌀겨: 양곡가공업 신고 사업장 발생분
        v = spec.get("grain_processor_registered")
        if v is True:
            return rows, None, None
        if v is False:
            return rows, "REJECT:양곡가공업 신고 사업장(도정 공장)에서 나온 왕겨·쌀겨만 순환자원으로 인정", None
        return rows, "ASK", {"field": "item_specific.grain_processor_registered",
                             "text": "양곡가공업 신고를 한 도정 공장에서 나온 왕겨·쌀겨인가요?"}

    if any(r["품목_한정"] == "Y" for r in rows):              # 그 밖의 적용범위 단서 (종이팩 제외 등)
        v = spec.get("in_scope")
        if v is True:
            return rows, None, None
        if v is False:
            return rows, "NOT_ITEM", None
        return rows, "ASK", {"field": "item_specific.in_scope",
                             "text": f"고시 적용범위에 해당하나요? ({_scope_note(rows[0])})"}

    return rows, None, None


# ----------------------------------------------------------------------
# 3. 고시 조건
# ----------------------------------------------------------------------
def _check_conditions(route: RouteDraft, code: str, row: dict, case: WasteCase) -> None:
    # 다른 폐기물과 섞임
    if case.mixed_with_other is True:
        route.cap(Status.REJECTED, "다른 폐기물과 섞여 있어 순환자원 기준에 맞지 않음")
    elif case.mixed_with_other is None:
        route.ask("mixed_with_other", "다른 폐기물과 섞여 있나요?")
        route.cap(Status.NEEDS_INFO, "혼합 여부 미확인")

    # 이물질 (고시 방법기준에 이물질 제거가 있는 품목만)
    if "이물질" in row["방법기준_원문"]:
        fm = case.foreign_matter or {}
        items = fm.get("items")
        if items is None:
            route.ask("foreign_matter", "이물질(라벨, 코팅, 도료 등)이 붙어 있나요? 떼어 낼 수 있나요?")
            route.cap(Status.NEEDS_INFO, "이물질 여부 미확인")
        elif items:
            sep = fm.get("separable")
            if sep is False:
                route.cap(Status.REJECTED, f"이물질({', '.join(map(str, items))})을 분리할 수 없음")
            elif sep is True:
                route.cap(Status.CONDITIONAL, f"이물질({', '.join(map(str, items))})을 고시 기준치 이하로 제거한 뒤 판매")
            else:
                route.ask("foreign_matter.separable", f"이물질({', '.join(map(str, items))})을 떼어 낼 수 있나요?")
                route.cap(Status.NEEDS_INFO, "이물질 분리 가능 여부 미확인")

    # 보관 상태 (커피찌꺼기, 왕겨·쌀겨)
    if code in STORAGE_CODES:
        if not case.storage_state:
            route.ask("storage_state", "어떻게 보관하고 있나요? (밀폐 용기, 실내 등)")
            route.cap(Status.NEEDS_INFO, "보관 상태 미확인")
        else:
            storage_ok = (case.raw or {}).get("storage_ok")       # 파트 1이 보관 기준 충족 여부를 줄 때만
            if storage_ok is False:
                route.cap(Status.CONDITIONAL, f"보관 상태('{case.storage_state}')가 고시 기준에 맞지 않아 개선 필요")
            route.conditions.append({
                "text": f"고시 보관 방법 준수 (현재: {case.storage_state})",
                "basis": row["근거"], "basis_status": "확인됨",
            })


# ----------------------------------------------------------------------
# 경로
# ----------------------------------------------------------------------
def _route(code: str, rows: list[dict], case: WasteCase, pre: PreliminaryResult) -> RouteDraft:
    rcs = sorted({rc for r in rows for rc in split(r["허용_R코드"])})
    item = " / ".join(dict.fromkeys(r["품목"] for r in rows))
    row = rows[0]
    route = RouteDraft(
        gate=1,
        tier=None,
        route_type="direct_use",
        title=f"지정 순환자원 판매: {item}",
        waste_codes=[code],
        waste_status="general",
        steps=[
            Step(1, "수집·운반 (필요 시)", "transporter", waste_codes=[code]),
            Step(2, "순환자원 용도로 사용", "end_user", r_codes=rcs, waste_codes=[code]),
        ],
        regulatory=Status.CONFIRMED,
        reasons=[f"순환자원 지정 고시 품목: {item} ({code})"],
        r_code_detail=[{"r_code": rc, "description": "", "basis": row["근거"]} for rc in rcs],
        evidence_ids=sorted({r["근거"] for r in rows}),
    )
    route.conditions.append({
        "text": f"고시 용도({', '.join(rcs)})로 사용하려는 자에게만 공급",
        "basis": row["근거"], "basis_status": "확인됨",
    })
    route.conditions.append({"text": _method_summary(row), "basis": row["근거"], "basis_status": "확인됨"})
    route.conditions.append({
        "text": "폐기물이 아니게 되므로 인계서·수탁처리능력 확인 대상 아님 (고시 조건을 지키는 동안만)",
        "basis": row["근거"], "basis_status": "확인됨",
    })
    return route


def run(case: WasteCase, pre: PreliminaryResult, tables: Tables) -> list[RouteDraft]:
    if pre.waste_status != "general":          # 지정폐기물(의심 포함)은 대상 아님 (사전검증에서 보통 SKIP)
        return []

    main = pre.classification.normalized_waste_code
    comp_codes = [c["waste_code"] for c in pre.component_results]
    codes = [main] + [c for c in comp_codes if c != main]

    routes: list[RouteDraft] = []
    for code in codes:
        rows = _item_rows(code, tables)
        if not rows:
            continue
        rows, verdict, question = _pick_rows(code, rows, case)
        if verdict == "NOT_ITEM":
            continue

        route = _route(code, rows, case, pre)
        if verdict == "ASK":
            route.ask(question["field"], question["text"])
            route.cap(Status.NEEDS_INFO, "고시 품목 해당 여부 미확인")
        elif verdict and verdict.startswith("REJECT:"):
            route.cap(Status.REJECTED, verdict[len("REJECT:"):])

        _check_conditions(route, code, rows[0], case)

        # 분류번호가 여러 개인 혼합물: 나눌 수 있으면 분리 후 판매, 아니면 불가
        if len(codes) > 1:
            if case.separable is True:
                route.cap(Status.CONDITIONAL, "다른 성분과 분리한 뒤 이 성분만 판매")
            elif case.separable is False:
                route.cap(Status.REJECTED, "다른 폐기물 성분과 분리할 수 없음")
            else:
                route.ask("separable", "다른 성분과 나눌 수 있나요?")
                route.cap(Status.NEEDS_INFO, "분리 가능 여부 미확인")

        if any((c.status or "").lower() == "estimated" for c in case.components):
            route.cap(Status.CONDITIONAL, "구성 정보가 추정 값")
        routes.append(route)
    return routes
