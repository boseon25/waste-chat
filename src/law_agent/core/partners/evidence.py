"""업체마다 '왜 통과했나(근거)'와 '무엇을 더 확인해야 하나(주의)'를 매칭 결과에 붙인다 (2026.11.20).

판단은 업체 CSV 값으로 이미 끝났다 (by_recycler · by_transport). 여기서는 그 판단에 쓴 CSV 값을 사람이 읽는 말로 남기고,
CSV 로는 거를 수 없지만 맡기기 전에 꼭 봐야 하는 것을 주의로 단다. 결과(PartnerCandidate.evidence · cautions)는
RouteProposal(파트 3) · 법률 카드 · 시연 화면 · waste-chat 이 같은 값을 쓴다.

  evidence: [{"item": "영업 상태", "value": "영업 중", "law": "폐기물관리법 제25조"}, …]
  cautions: ["비배출시설계 폐기물만 운반합니다 — 배출 사업장이 배출시설계면 맡길 수 없습니다 (배출시설계 여부 확인)", …]
"""
from __future__ import annotations

from datetime import date

from .models import Location, PartnerCandidate

LAW_ENTRUST = "폐기물관리법 제18조 제1항"
LAW_PERMIT = "폐기물관리법 제25조"
LAW_REPORTER = "폐기물관리법 제46조"
OLD_YEARS = 2                                      # 자료 기준연도가 올해보다 이만큼 이상 오래되면 주의


def _monthly_ton(quantity: dict | None) -> float | None:
    xs = [float(x) for x in (quantity or {}).get("monthly_ton") or [] if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 3) if xs else None


def _old(year: str | None) -> bool:
    try:
        return date.today().year - int(str(year)[:4]) >= OLD_YEARS
    except (TypeError, ValueError):
        return False


def recycler(c: PartnerCandidate, codes: list[str], designated: list[bool], quantity: dict | None) -> None:
    ev = [{"item": "위탁 처리", "value": f"다른 사업장 폐기물을 위탁받는 {c.permit_type or '허가·신고'} 업체", "law": LAW_ENTRUST},
          {"item": "허가 폐기물", "value": c.waste_group or "-", "law": LAW_PERMIT}]
    if any(designated):
        ev.append({"item": "지정폐기물", "value": "지정폐기물 처리 허가 있음", "law": LAW_PERMIT})
    if c.treatment_codes:
        names = next((r[len("처리방법 "):] for r in c.reasons if r.startswith("처리방법 ")), "")   # "2010 파쇄·분쇄…"
        rc = ", ".join(c.r_codes) if c.r_codes else "이번 재활용 유형"
        ev.append({"item": "처리방법 코드", "value": (names or ", ".join(c.treatment_codes))
                   + f" — 업체가 허가받은 처리방법이 이번 경로({rc})에 필요한 처리방법과 같음", "law": LAW_PERMIT})
    if c.r_codes:
        ev.append({"item": "재활용 유형", "value": ", ".join(c.r_codes) + " (법이 허용한 유형과 같음)",
                   "law": "시행규칙 별표 4의3"})
    if c.registration_type:
        ev.append({"item": "등록 형태", "value": c.registration_type, "law": "서비스 범위 (중간재활용업)"})
    c.evidence = ev
    cau = []
    if c.permit_type == "신고":
        cau.append(f"폐기물처리 신고자입니다 — 받을 수 있는 폐기물 종류·양에 제한이 있으니 이번 물량을 받을 수 있는지 확인 ({LAW_REPORTER})")
    m = _monthly_ton(quantity)
    cau.append(f"처리 가능한 양은 자료에 없습니다 — 월 {m}톤을 받을 수 있는지 회신으로 확인" if m is not None
               else "처리 가능한 양은 자료에 없습니다 — 받을 수 있는 물량을 회신으로 확인")
    if _old(c.data_date):
        cau.append(f"자료 기준 {c.data_date}년 — 지금도 같은 허가로 영업 중인지 허가증으로 확인")
    c.cautions = cau


LIQUID = ("액상", "액체", "폐액", "슬러지", "오니", "반고상")


def transporter(c: PartnerCandidate, codes: list[str], designated: list[bool], quantity: dict | None,
                emitter_category: str | None = None, material_form: str | None = None) -> None:
    scope = c.scope_text or ""
    ev = [{"item": "영업 상태", "value": "영업 중", "law": LAW_PERMIT},
          {"item": "운반 대상", "value": f"{c.waste_group or '-'} (건설폐기물 전용 아님)", "law": LAW_PERMIT}]
    if any(designated):
        ev.append({"item": "지정폐기물", "value": "지정폐기물 운반 허가 · 분류번호 " + ", ".join(codes), "law": LAW_PERMIT})
    if scope:
        ev.append({"item": "영업 대상 (자료 원문)", "value": scope[:160], "law": LAW_PERMIT})
    cap = {k: v for k, v in (c.capacity or {}).items() if str(v).strip() not in ("", "0", "0.0")}   # 0 = 자료 없음
    if cap:
        ev.append({"item": "운반 능력", "value": " · ".join(f"{k} {v}" for k, v in cap.items()), "law": "업체 자료"})
    c.evidence = ev
    cau = []
    last = scope.rsplit("; ", 1)[-1].strip()           # by_transport: "영업대상 원문; 배출시설계 구분"
    only_off, only_on = last == "비배출시설계", last == "배출시설계"
    if only_off and emitter_category != "비배출시설계":
        cau.append("비배출시설계 폐기물만 운반합니다 — 배출 사업장이 배출시설계면 맡길 수 없습니다 (배출시설계 여부 확인)")
    if only_on and emitter_category != "배출시설계":
        cau.append("배출시설계 폐기물만 운반합니다 — 배출 사업장이 비배출시설계면 맡길 수 없습니다 (배출시설계 여부 확인)")
    form = material_form or ""
    if any(w in form for w in LIQUID):
        cau.append(f"{form} 폐기물입니다 — 탱크로리 또는 밀폐 용기(드럼)로 운반할 차량이 있는지 확인 "
                   "(업체 자료에는 차량 대수만 있고 종류는 없음)")
    elif "보유차량" not in cap:
        cau.append("보유 차량 수·종류가 자료에 없습니다 — 운반 가능한 차량을 회신으로 확인")
    cau.append("영업 구역(어느 지역까지 운반하는지)은 자료에 없습니다 — 회신으로 확인")
    if _old(c.data_date):
        cau.append(f"자료 기준 {c.data_date}년 — 지금도 같은 허가로 영업 중인지 허가증으로 확인")
    c.cautions = cau


def distance_text(loc: Location | dict | None, origin_basis: str | None = None) -> str:
    """거리 표시: 도로 거리 / 직선 추정 / 같은 시군구(추정 불가)"""
    if loc is None:
        return "-"
    g = (lambda k: loc.get(k)) if isinstance(loc, dict) else (lambda k: getattr(loc, k, None))
    if g("road_km") is not None:
        return f"{g('road_km'):.1f} km (도로)"
    d, basis = g("distance_km"), g("basis") or ""
    if d is None:
        return "-"
    if d < 0.5 and "중심점" in basis:
        return "같은 시군구 (주소 좌표가 없어 거리 추정 불가)"
    est = f"약 {d * 1.3:.0f} km (직선 × 1.3 추정"
    return est + (", 시군구 중심 기준)" if "중심점" in basis or "중심점" in (origin_basis or "") else ")")
