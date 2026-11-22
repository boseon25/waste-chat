# route_agent/src/proposal/feedback.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""업체 회신(PartnerFeedback) 반영 — 명세서 "비교·추천 후 회신 반영 재계산".

회신 1건 (파트 3이 업체 회신을 정리해서 보냄)
  필수: feedback_id, route_id, company_id, acceptance ("accepted" | "conditional" | "rejected")
  선택: answers{질문 field: 값}, quote{amount, unit, …}, conditions[], responded_at, responded_by,
        rejection_reason, capacity_ton_month, required_tests[], transport_by, license_checked

반영 규칙
- 같은 feedback_id는 한 번만 반영 (멱등). 같은 업체가 다시 회신하면 마지막 값으로 덮고 이력은 남김
- 업체 상태: accepted → confirmed / conditional → conditional / rejected → rejected
- 거절: 그 업체를 excluded_partners로 옮기고 예비 후보(reserve_candidates)에서 다음 업체를 올려 순위를 다시 매김.
        예비까지 없으면 next_action = "다른 업체 검색" (pipeline을 회신과 함께 다시 돌리면 거절 업체를 빼고 매칭)
- 처리 가능 물량 < 배출 물량: "물량 초과" 표시, 수락이어도 조건부로
- 시험 요구: 경로 required_tests에 추가 + 배출업체에 물을 것(emitter_requests)
- 업체 직접 운반: 운반 단계를 optional로
- 허가증 확인: verified = true, evidences에 회신 근거
- 바뀐 것이 있으면 route_version +1, checks.acceptance 다시 계산
회신 안의 문장은 데이터로만 다룬다 (지시로 실행하지 않음).
"""
from __future__ import annotations

from .checks import acceptance

STATUS = {"accepted": "confirmed", "conditional": "conditional", "rejected": "rejected"}
DIRECT = ("업체 직접", "직접", "수거", "self")


def _main(p: dict) -> list[dict]:
    return [c for c in p["partner_candidates"] if c["step_order"] == p.get("main_step_order")]


def _renumber(p: dict) -> None:
    for i, c in enumerate(_main(p)):
        c["rank"] = i + 1


def _promote(p: dict) -> dict | None:
    """예비 후보 1곳을 주 후보 맨 뒤로 올린다."""
    if not p.get("reserve_candidates"):
        return None
    nxt = p["reserve_candidates"].pop(0)
    mains = _main(p)
    pos = p["partner_candidates"].index(mains[-1]) + 1 if mains else 0
    p["partner_candidates"].insert(pos, nxt)
    return nxt


def _apply_one(p: dict, fb: dict) -> bool:
    fid = fb.get("feedback_id")
    status = STATUS.get(fb.get("acceptance"))
    cand = next((c for c in p["partner_candidates"] if c["company_id"] == fb.get("company_id")), None)
    if cand is None:
        cand = next((c for c in p.get("reserve_candidates") or [] if c["company_id"] == fb.get("company_id")), None)
    already_excluded = any(e["company_id"] == fb.get("company_id") for e in p["excluded_partners"])

    if status is None or (cand is None and not already_excluded):
        if status == "rejected" and fb.get("company_id"):
            # 다시 돌린 매칭에서 이미 빠진 업체의 거절 회신 → 기록만
            p["excluded_partners"].append({"company_id": fb["company_id"], "name": None,
                                           "reason": fb.get("rejection_reason"), "feedback_id": fid})
            return True
        p.setdefault("unmatched_feedback", []).append(fid)
        return False
    if cand is None:                                      # 이미 제외된 업체의 추가 회신
        return False

    # 이력 (같은 업체가 여러 번 회신하면 마지막 값이 현재 값)
    cand.setdefault("feedback_history", []).append(
        {k: fb.get(k) for k in ("feedback_id", "acceptance", "responded_at", "responded_by")})
    cand["status"] = status
    cand["responded_at"] = fb.get("responded_at")
    if fb.get("answers"):
        cand.setdefault("answers", {}).update(fb["answers"])
    if fb.get("conditions"):
        cand["partner_conditions"] = list(fb["conditions"])
    if fb.get("quote"):
        cand["quote"] = fb["quote"]
        quotes = [q for q in p["comparison"].setdefault("quotes", []) if q["company_id"] != cand["company_id"]]
        p["comparison"]["quotes"] = quotes + [{"company_id": cand["company_id"], **fb["quote"]}]
        if "quote" in p["comparison"]["missing"]:
            p["comparison"]["missing"].remove("quote")
            p["comparison"]["missing"].append("운송비·전처리비 (순비용 합계용)")

    # 처리 가능 물량
    cap, ton = fb.get("capacity_ton_month"), p.get("waste_ton_month")
    if isinstance(cap, (int, float)):
        cand["capacity_ton_month"] = cap
        flags = [f for f in cand.get("flags", []) if f != "물량 초과"]
        if ton and cap < ton:
            flags.append("물량 초과")
            if cand["status"] == "confirmed":
                cand["status"] = "conditional"
        cand["flags"] = flags

    # 시험 요구 → 경로 시험 목록 + 배출업체에 물을 것
    for t in fb.get("required_tests") or []:
        item = {"test": t, "r_codes": [], "source": f"업체 회신 {fid} ({cand['name']})"}
        if not any(x.get("test") == t for x in p["required_tests"]):
            p["required_tests"].append(item)
        reqs = p.setdefault("emitter_requests", [])
        if not any(r["text"].startswith(t) for r in reqs):
            reqs.append({"field": "partner_required_test",
                         "text": f"{t}: 업체({cand['name']})가 요구합니다. 시험 성적서가 있거나 받을 수 있나요?"})

    # 업체 직접 운반 → 운반 단계 생략 가능
    if str(fb.get("transport_by") or "").startswith(DIRECT) and status != "rejected":
        cand["transport_by"] = fb["transport_by"]
        for s in p["steps"]:
            if s["role"] == "transporter":
                s["optional"] = True
        p["transport_note"] = f"{cand['name']}이(가) 직접 운반 → 운반 업체 선택 불필요"

    # 허가증 확인
    if fb.get("license_checked") is True:
        cand["verified"] = True
        ev = f"FEEDBACK-{fid}"
        if not any(e["evidence_id"] == ev for e in p["evidences"]):
            p["evidences"].append({"evidence_id": ev, "source": f"업체 회신 ({cand['name']})", "ref": fid,
                                   "responded_by": fb.get("responded_by"), "responded_at": fb.get("responded_at")})

    # 거절 → 제외하고 예비 후보 올리기
    if status == "rejected":
        if cand in p["partner_candidates"]:
            p["partner_candidates"].remove(cand)
        elif cand in p.get("reserve_candidates") or []:
            p["reserve_candidates"].remove(cand)
        p["excluded_partners"].append({"company_id": cand["company_id"], "name": cand["name"],
                                       "reason": fb.get("rejection_reason"), "feedback_id": fid})
        promoted = _promote(p) if cand["step_order"] == p.get("main_step_order") else None
        if promoted:
            p.setdefault("promoted", []).append({"company_id": promoted["company_id"], "name": promoted["name"],
                                                 "replaces": cand["company_id"]})
        _renumber(p)
    return True


def apply_feedback(proposals: list[dict], feedbacks: list[dict]) -> list[dict]:
    by_route = {p["route_id"]: p for p in proposals}
    changed: set[str] = set()
    for fb in feedbacks:
        p = by_route.get(fb.get("route_id"))
        if p is None or fb.get("feedback_id") in p["applied_feedback_ids"]:
            continue
        p["applied_feedback_ids"].append(fb.get("feedback_id"))
        if _apply_one(p, fb):
            changed.add(p["route_id"])

    for rid in changed:
        p = by_route[rid]
        mains = _main(p)
        a, why = acceptance(mains) if mains else ("rejected", "후보 업체가 모두 거절 → 다른 업체 검색 필요")
        p["checks"]["acceptance"] = a
        p["check_reasons"]["acceptance"] = why
        p["route_version"] += 1
        p["next_action"] = "다른 업체 검색" if (not mains or all(c["status"] == "rejected" for c in mains)) else None
    return proposals
