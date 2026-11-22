"""매칭 전 서류 준비 — 법령 판단(거래 찾기 기록)마다 '우리 업체만 있는 준비 공간'을 두고 서류를 미리 쓴다.

- 서류 목록: 법령 판단이 낸 '필요한 서류'를 거래방 서류 항목(deal_docs)으로 바꾸고, 견적 문의서는 늘 넣는다.
- 저장: 거래방과 같은 저장소(docdraft)에 방 번호 대신 거래 찾기 번호(s_…)로 둔다. 작성 화면·에이전트·검토를 그대로 쓴다.
- 함께 쓰는 서류는 상대 업체 칸(hauler_·receiver_)과 협의 값(단가)을 잠근다. 추측해서 채우지 않는다.
- 거래방이 열리면(copy_to_room) 준비한 작성본과 칸별 출처를 그 방으로 옮긴다.
"""
import re

from . import catalog, deal_docs, engine, store

# 법령 판단 서류 이름 → 배출업체가 미리 쓸 수 있는 거래방 항목(작성 서식이 있는 것만)
FROM_ENGINE = [
    (r"위탁\s*계약", ["contract"]),
    (r"수탁\s*처리\s*능력", ["form5_processor"]),
    (r"배출자\s*신고", ["form6"]),
    (r"처리\s*계획", ["dplan"]),
    (r"유해성", ["hazard"]),
    (r"분석\s*결과|성분\s*분석", ["analysis_request"]),
]
ALWAYS = ["rfq"]  # 견적 문의서: 후보 업체 여러 곳에 같은 내용으로 보낼 수 있다
THEIR_PREFIX = ("hauler_", "receiver_")
NEGOTIATED = {"transport_price", "processing_price", "payer", "contract_period"}


def items(result):
    keys = list(ALWAYS)
    for d in (result or {}).get("docs") or []:
        for pat, ks in FROM_ENGINE:
            if re.search(pat, d.get("title", "")):
                keys += [k for k in ks if k not in keys]
                break
    out = []
    for k in keys:
        it = deal_docs.BY_KEY.get(k)
        if it and it["doc"] and catalog.DOCS.get(it["doc"], {}).get("fields"):
            out.append(it)
    return out


THEIR_KEYS = {"treatment_method", "contract_destination"}  # 함께 쓰는 서류에서 처리업체가 적는 값(처리 방법·처리시설 주소)
RECIPIENT = {"rfq_recipient", "rfq_destination"}  # 견적 문의서의 받는 업체·도착지: 업체를 고른 뒤 정한다


def locked_why(it):
    """매칭 전에는 비워 두는 칸 → 이유. their: 상대 업체가 채움, agreed: 업체와 협의해 정함."""
    if not it["shared"] and it["key"] != "rfq":
        return {}
    out = {}
    for f in catalog.DOCS[it["doc"]]["fields"]:
        k = f["key"]
        if k.startswith(THEIR_PREFIX) or (it["shared"] and k in THEIR_KEYS):
            out[k] = "their"
        elif k in NEGOTIATED:
            out[k] = "agreed"
        elif k in RECIPIENT:
            out[k] = "their"
    return out


def locked(it):
    return list(locked_why(it))


def filter_review(rv, lock):
    """매칭 전 검토: 잠근 칸(상대 업체·협의 값)이 비었다는 지적은 빼고, 업체가 정해져야 받을 수 있는 첨부(제5호 등)는
    오류가 아니라 '나중에 할 일' 안내로 바꿔 다시 센다."""
    if not rv:
        return rv
    lock = set(lock)
    findings = []
    for f in rv["findings"]:
        if f.get("key") in lock:
            continue
        if not f.get("key") and "거래방 서류 탭" in (f.get("message") or ""):
            msg = re.sub(r"\s*\(거래방 서류 탭[^)]*\)", "", f["message"])
            f = {**f, "severity": "info", "message": msg + " — 업체가 정해지면 거래방에서 받습니다. 지금 고칠 칸은 아닙니다."}
        findings.append(f)
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    counts = {s: sum(f["severity"] == s for f in findings) for s in ("error", "warning", "info")}
    return {**rv, "findings": findings, "counts": counts,
            "verdict": "fix" if counts["error"] else "check" if counts["warning"] else "pass"}


def fake_room(search, me, result):
    """거래방 로직을 그대로 쓰기 위한 '우리 업체만 있는 방'."""
    titles = " ".join(d.get("title", "") for d in (result or {}).get("docs") or [])
    code_row = None
    if result and result.get("code"):
        from . import tables
        code_row = tables.code(result["code"])
    flags = {"business": bool(re.search(r"배출자\s*신고", titles)),
             "designated": (result or {}).get("status") == "designated" or bool(code_row and code_row.get("지정폐기물") == "Y")
                           or bool(re.search(r"처리\s*계획|유해성", titles)),
             "construction": False, "classify": False}
    room = {"id": search["id"], "title": search["title"], "need": search["need"], "flags": flags,
            "announced": ["agreed"], "created_by": me["id"], "prep": True}
    return room, [{"company_id": me["id"], "role": "emitter", "agreed": 1}]


def status(sid, me_id, its):
    out = []
    for it in its:
        keys = [f["key"] for f in catalog.DOCS[it["doc"]]["fields"]]
        lock = set(locked(it))
        mine = [k for k in keys if k not in lock]
        values = store.draft(sid, it["key"], me_id)
        out.append({"key": it["key"], "title": it["title"], "shared": it["shared"],
                    "filled": sum(1 for k in mine if (values.get(k) or "").strip()), "total": len(mine),
                    "locked": len(lock), "started": bool(values)})
    return out


def copy_to_room(sid, cid, room_id):
    """준비 공간의 작성본·칸별 출처를 새 거래방으로 옮긴다. 옮긴 서류 수."""
    n = 0
    for item, owner, values in store.room_drafts(sid, cid):
        if owner != cid or not values:
            continue
        if not store.draft(room_id, item, cid):
            store.save_draft(room_id, item, cid, values)
            n += 1
    data = store.desk(sid, cid)
    if data.get("meta") or data.get("originals"):
        def merge(d):
            d["meta"] = {**data.get("meta", {}), **d.get("meta", {})}
            d["originals"] = d.get("originals", []) + data.get("originals", [])
        store.update_desk(room_id, cid, merge)
    return n


def view(search, me):
    res = engine.result(store.casefile(search["id"]).get("job"))
    its = items(res)
    return status(search["id"], me["id"], its)
