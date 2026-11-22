# route_agent/src/recommend/__init__.py 에서 복사 (2026.11.17). 바꾼 점: import 경로. 역방향 추천(reverse.py)은 가져오지 않음
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""배출업체 → 가공업체 추천 카드 (화면용).

pipeline 결과(경로·단계별)를 3번 페이지 "배출업체 화면"에 바로 쓸 모양으로 묶는다.
- 처리 방법(경로)마다 카드 1개, 법 순서(순환자원 판매 → 재활용 위탁 → 소각·매립)
- 카드마다 추천 업체 순위 = 매칭 순위 그대로 (운송 탄소·거리 → 허가 → 확신도)
- 운송업체는 협력 확정 후 정하므로 카드의 추천 업체에 넣지 않고 후보 수만 적는다
- 불가 경로, 업체가 없는 경로는 "추천 없음"으로 따로 모은다

  rec = build_recommendation(pipeline_result, raw_case, case)
"""
from __future__ import annotations

from statistics import mean

PROCESSOR_ROLES = ("recycler", "preprocessor", "treatment", "end_user")
STATUS = {"confirmed": "가능", "conditional": "조건부", "needs_info": "확인 필요", "rejected": "불가"}
TIER = {"reuse": "재사용", "material_recycle": "재생이용", "energy_recovery": "에너지 회수"}
ROLE = {"recycler": "재활용 업체", "preprocessor": "중간 가공 업체", "treatment": "처분 업체",
        "end_user": "순환자원 구매처", "transporter": "운반 업체"}


def _kind(route: dict) -> str:
    if route["gate"] == 1:
        return "순환자원 판매"
    if route["gate"] == 4:
        return "소각·매립"
    return f'재활용 위탁 ({TIER.get(route.get("tier"), "중간 가공")})'


def _processor_step(steps: list[dict]) -> dict | None:
    """카드에 보여줄 단계: 가공·처분·구매 역할 중 필수 단계의 마지막 (매립 전처리 같은 선택 단계는 제외)"""
    cands = [s for s in steps if s["role"] in PROCESSOR_ROLES]
    required = [s for s in cands if not s.get("optional")]
    return (required or cands or [None])[-1]


def _card_partner(rank: int, c: dict) -> dict:
    loc = c.get("location") or {}
    carbon = c.get("carbon") or {}
    return {
        "순위": rank,
        "company_id": c["company_id"],
        "업체명": c["name"],
        "역할": ROLE.get(c["role"], c["role"]),
        "지역": f'{c["sido"]} {c["sigungu"]}'.strip(),
        "주소": c.get("address"),
        "연락처": c.get("phone"),
        "허가": " ".join(x for x in (c.get("permit_type"), c.get("registration_type")) if x) or None,
        "처리방법코드": c.get("treatment_codes") or [],
        "재활용유형": c.get("r_codes") or [],
        "거리_km": loc.get("road_km") if loc.get("road_km") is not None else carbon.get("road_km"),
        "거리_근거": loc.get("road_basis") or "직선 × 1.3 추정",
        "예상시간_분": loc.get("duration_min"),
        "통행료_원": loc.get("toll_won"),
        "직선거리_km": loc.get("distance_km"),
        "위치_근거": loc.get("basis"),
        "운송탄소_kgCO2eq_월": carbon.get("transport_kgco2e"),
        "추천이유": c.get("reasons") or [],
        "확신도": c.get("match_confidence"),
        "데이터_기준": f'{c.get("data_date") or ""} {c.get("source") or ""}'.strip(),
    }


def _waste(result: dict, raw: dict, case) -> dict:
    cls = (result["preliminary"].get("classification") or {})
    region = raw.get("region") or {}
    monthly = [v for v in ((case.quantity or {}).get("monthly_ton") or []) if isinstance(v, (int, float))]
    return {
        "case_id": result["case_id"],
        "폐기물": raw.get("name"),
        "분류번호": cls.get("normalized_waste_code") or (raw.get("classification") or {}).get("selected_code"),
        "지정폐기물": {"general": "아님", "designated": "해당 (의심 포함)"}.get(result["preliminary"].get("waste_status"),
                                                                           result["preliminary"].get("waste_status")),
        "월물량_톤": round(mean(monthly), 3) if monthly else None,
        "형태": raw.get("material_form"),
        "지역": f'{region.get("sido", "")} {region.get("sigungu", "")}'.strip(),
        "주소": region.get("address"),
        "배출업체": raw.get("emitter"),                     # 파트 1이 주면 그대로 (지금 계약에는 없음)
    }


def build_recommendation(result: dict, raw: dict, case) -> dict:
    options, none = [], []
    for r in result["routes"]:
        p = r.get("partners") or {}
        steps = p.get("steps") or []
        laws = [f'{l["law"]} {l["article"]}' for l in r.get("laws", []) if l.get("law")]
        head = {"처리": r["title"], "구분": _kind(r), "법적상태": STATUS.get(r["regulatory"], r["regulatory"])}

        if r["regulatory"] == "rejected":
            none.append({**head, "이유": r.get("reasons") or []})
            continue
        main = _processor_step(steps)
        if main is None or not main["candidates"]:
            why = (main or {}).get("rejected_summary") or {}
            none.append({**head, "이유": [f"맞는 업체 없음 {why}" if why else "업체를 찾지 않는 경로"]
                         + [q["text"] for q in (main or {}).get("questions", [])]})
            continue

        others = [{"역할": ROLE.get(s["role"], s["role"]), "후보_수": s["total_found"],
                   "메모": "협력 확정 후 선택" if s["role"] == "transporter" else
                           ("선택 단계" if s.get("optional") else None)}
                  for s in steps if s is not main]
        to_emitter = [q["text"] for q in r.get("questions") or []]
        to_partner = []
        for s in steps:
            for q in s.get("questions") or []:
                (to_partner if q.get("to") == "partner" and s is main else to_emitter).append(q["text"])

        options.append({
            **head,
            "업체확보": {True: "확보", False: "미확보", None: "-"}[p.get("secured")],
            "근거법령": laws,
            "조건": [c["text"] for c in r.get("conditions") or []],
            "추천업체": [_card_partner(i + 1, c) for i, c in enumerate(main["candidates"])],
            "조건맞는_업체_수": main["total_found"],
            "가공업체가_알려줘야_할_것": list(dict.fromkeys(to_partner)),
            "배출업체가_확인할_것": list(dict.fromkeys(t for t in to_emitter if "다음 단계" not in t)),
            "다른_단계": others,
            "처리탄소_참고": r.get("carbon_reference"),
        })
    return {"waste": _waste(result, raw, case), "options": options, "추천_없음": none}


__all__ = ["build_recommendation", "PROCESSOR_ROLES"]
