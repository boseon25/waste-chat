"""서류 비서 — 거래방의 서류 묶음을 에이전트가 먼저 챙기고, 사람은 확인할 칸만 본다.

1) 진행판(board)      법령 판단이 요구한 서류와 거래방 서류를 합쳐 빠진 서류·진행률·확인할 칸·상대 업체에 받을 칸·기한을 보여준다.
2) 원본 나눠 넣기      입력서(docx)·사업자등록증·허가증(사진·PDF)을 한 번 올리면 내가 맡은 모든 서류의 칸에 값을 '제안'한다.
3) 확인할 칸           제안·바뀐 값·검토 오류·확신이 낮은 칸만 모아 수락/거절한다. 칸마다 출처와 확신도(high/check)를 남긴다.
4) 바뀐 내용 따라가기  칸 값이 나온 자리(업체 정보·거래 조건·법령 판단 사실·다른 서류)가 바뀌면 새 값을 제안한다.
   원본 대조            올린 원본에 적힌 값과 서류 값이 다르면 검토에서 알린다.

칸별 기록(store.desk): {"meta": {item: {key: {source, origin, basis, conf, ai}}},
                        "proposals": {item: {key: {value, source, why, conf, kind}}},
                        "rejected": {item: {key: [값…]}}, "originals": [...], "requests": {업체id: 시각}, "start_date": "YYYY-MM-DD"}
"""
import calendar
import json
import re
import time
import unicodedata
from datetime import date

from . import advisor, catalog, deal_docs, doc_agent, engine, intake_docx, store

HIGH, CHECK = "high", "check"
AI_SOURCES = re.compile(r"에이전트|올린 자료|원본|AI")

# 법령 판단(파트 2)이 낸 서류 이름 → 거래방 항목. flag: 그 항목을 보이게 하는 거래방 표시
ENGINE_DOCS = [
    (r"건설.*처리계획", ["cplan"], "construction"),
    (r"위탁\s*계약", ["contract"], None),
    (r"수탁\s*처리\s*능력|수탁\s*확인", ["form5_hauler", "form5_processor", "designated_acceptance"], None),
    (r"배출자\s*신고", ["form6"], "business"),
    (r"처리\s*계획", ["dplan"], "designated"),
    (r"분석\s*결과|성분\s*분석", ["analysis", "lab_result"], "designated"),
    (r"유해성", ["hazard"], "designated"),
    (r"올바로|전자\s*인계", ["allbaro_emitter", "allbaro_hauler", "allbaro_processor"], None),
]
FLAG_LABEL = {"business": "배출자 신고 대상", "designated": "지정폐기물", "construction": "건설폐기물", "classify": "분류 확인 필요"}

# 기한: (설명, 기준일 → 날짜 계산). 근거는 docs/거래서류안내.md. 기준일은 '폐기물 발생(첫 반출 예정)일'.
def _plus_month(d):
    y, m = (d.year + (d.month == 12), d.month % 12 + 1)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


DEADLINES = {
    "form6": ("발생일부터 1개월 이내 관할 시·군·구에 신고", _plus_month),
    "dplan": ("처리(첫 반출) 전에 확인", lambda d: d),
    "hazard": ("첫 인계 전에 수탁자에게 제공", lambda d: d),
    "contract": ("첫 반출 전에 서면 계약", lambda d: d),
    "form5_hauler": ("계약 전에 확인", lambda d: d),
    "form5_processor": ("계약 전에 확인", lambda d: d),
    "designated_acceptance": ("처리계획서 제출 전", lambda d: d),
    "cplan": ("착공일까지 신고", None),
    "allbaro_emitter": ("인계 전 입력, 예약은 인수 후 2일 이내 확정", None),
    "allbaro_hauler": ("인수한 날부터 2일 이내", None),
    "allbaro_processor": ("인수·처리한 날부터 각 2일 이내", None),
    "annual": ("다음 해 2월 말일까지 전산 제출", lambda d: date(d.year + 1, 2, calendar.monthrange(d.year + 1, 2)[1])),
    "ledger": ("발생·처리 때마다 기록, 3년 보존", None),
}
ROLE_OF_PREFIX = doc_agent.ROLE_OF_PREFIX
FACT_OF = {"waste_classification": "code", "designated_waste_code": "code", "waste_name": "name",
           "generation_process": "process", "waste_state": "form", "waste_state_code": "form",
           "contamination_detail": "contamination_detail", "contract_quantity": "monthly_ton", "quantity_period": "monthly_ton"}


def _norm(v):
    return re.sub(r"[\s\-·.,()㈜]|주식회사", "", unicodedata.normalize("NFKC", str(v or ""))).lower()


def _fields(doc_id):
    return {f["key"]: f["label"] for f in (catalog.DOCS.get(doc_id or "") or {}).get("fields", [])}


# ── 칸별 자동 값과 출처 ───────────────────────────────────────────────
def layers(room, members, companies, me_id, it):
    """이 서류의 자동 값: 업체 정보·거래 조건 → 같은 방의 다른 서류 → 법령 판단 사실(빈칸만).
    {칸: {value, source, origin}}"""
    keys = _fields(it["doc"])
    out = {k: x for k, x in deal_docs.prefill_layer(room, members, companies).items() if k in keys}
    titles = {i["key"]: i["title"] for i, _ in deal_docs.room_items(room, members)}
    taken = set()
    for other, owner, saved in store.room_drafts(room["id"], me_id):  # 최근 작성본부터
        if other == it["key"] or other not in titles:
            continue
        for k, v in saved.items():
            if k in keys and k not in taken and v:
                taken.add(k)
                out[k] = {"value": v, "source": f"‘{titles[other]}’에 적은 내용", "origin": f"doc:{other}:{owner}:{k}"}
    facts = store.casefile(room["id"]).get("facts") or {}
    for k, v in doc_agent.from_facts(it["doc"] or "", facts).items():
        if k not in out or out[k]["origin"].startswith("need:"):  # 판단 사실이 거래 요청 문구보다 구체적이다
            out[k] = {"value": v, "source": "법령 판단에 쓴 사실", "origin": f"facts:{it['doc']}:{k}"}
    return out


def _resolve(room, origin):
    """origin 자리의 지금 값. 알 수 없으면 None."""
    kind, _, rest = origin.partition(":")
    if kind == "profile":
        cid, _, name = rest.partition(":")
        c = store.company(cid)
        if not c:
            return None
        v = c["profile"].get(name, "")
        return ", ".join(v) if isinstance(v, list) else v
    if kind == "need":
        return deal_docs.need_value(room["need"], rest)
    if kind == "doc":
        item, owner, key = rest.split(":", 2)
        return store.draft(room["id"], item, owner).get(key)
    if kind == "facts":
        doc_id, _, key = rest.partition(":")
        return doc_agent.from_facts(doc_id, store.casefile(room["id"]).get("facts") or {}).get(key)
    return None


def note(room_id, item, cid, values, layer):
    """자동으로 들어간 값의 출처를 기록한다(값이 그 자리 값과 같을 때만)."""
    def change(data):
        meta = data.setdefault("meta", {}).setdefault(item, {})
        for k, x in layer.items():
            if values.get(k) == x["value"] and (k not in meta or meta[k].get("basis") != x["value"]):
                meta[k] = {"source": x["source"], "origin": x["origin"], "basis": x["value"], "conf": HIGH, "ai": False}
    store.update_desk(room_id, cid, change)


def touch(room_id, item, cid, changes):
    """작성 화면에서 바뀐 칸의 출처 기록. changes: {칸: 출처 문구}. 출처가 바뀌면(직접 고침 등) 자동 출처를 떼어 낸다."""
    def change(data):
        meta = data.setdefault("meta", {}).setdefault(item, {})
        for k, src in list(changes.items())[:200]:
            src = str(src or "")[:80]
            if meta.get(k, {}).get("source") == src:
                continue
            meta[k] = {"source": src, "conf": HIGH, "ai": bool(AI_SOURCES.search(src))}
    store.update_desk(room_id, cid, change)


# ── 제안 ──────────────────────────────────────────────────────────────
def propose(room_id, item, cid, items):
    """items: [{key, value, source, why, conf, kind}] → 거절한 값은 다시 제안하지 않는다. 새로 넣은 개수."""
    def change(data):
        props = data.setdefault("proposals", {}).setdefault(item, {})
        rejected = data.get("rejected", {}).get(item, {})
        n = 0
        for x in items:
            if _norm(x["value"]) in {_norm(v) for v in rejected.get(x["key"], [])}:
                continue
            props[x["key"]] = {k: x.get(k, "") for k in ("value", "source", "why", "conf", "kind", "origin", "basis")}
            n += 1
        return n
    return store.update_desk(room_id, cid, change)


def decide(room, members, me_id, item_key, accept=(), reject=(), edits=None):
    """제안 수락·거절. 수락한 값은 작성본에 넣고 출처를 남긴다."""
    it = deal_docs.BY_KEY[item_key]
    labels = _fields(it["doc"])
    edits = edits or {}
    applied = {}

    def change(data):
        props = data.setdefault("proposals", {}).setdefault(item_key, {})
        meta = data.setdefault("meta", {}).setdefault(item_key, {})
        for k in accept:
            p = props.pop(k, None)
            if p and k in labels:
                v = edits.get(k) if isinstance(edits.get(k), str) and edits[k].strip() else p["value"]
                applied[k] = v
                meta[k] = {"source": p["source"] + (" (고쳐서 수락)" if v != p["value"] else " (확인함)"),
                           "conf": HIGH, "ai": bool(AI_SOURCES.search(p["source"])),
                           **({"origin": p["origin"], "basis": v} if p.get("origin") and v == p["value"] else {})}
        for k in reject:
            p = props.pop(k, None)
            if p:
                data.setdefault("rejected", {}).setdefault(item_key, {}).setdefault(k, []).append(p["value"])
                if p.get("kind") == "changed" and k in meta:  # 바뀐 값을 거절하면 지금 값을 유지하고 더 묻지 않는다
                    meta[k].pop("origin", None)
    store.update_desk(room["id"], me_id, change)
    if applied:
        values = store.draft(room["id"], item_key, me_id)
        if not values and it["shared"]:
            values = dict(store.draft(room["id"], item_key, ""))
        values.update(applied)
        store.save_draft(room["id"], item_key, me_id, values)
    return applied


def stale(room, item_key, me_id, values):
    """출처 자리의 값이 바뀌었는데 칸은 예전 값 그대로인 것 → 새 값 제안."""
    meta = store.desk(room["id"], me_id).get("meta", {}).get(item_key, {})
    out = []
    for k, m in meta.items():
        if not m.get("origin") or values.get(k) != m.get("basis"):
            continue
        now = _resolve(room, m["origin"])
        if now is None or _norm(now) == _norm(m["basis"]) or not str(now).strip():
            continue
        out.append({"key": k, "value": str(now).strip(), "source": m["source"], "kind": "changed", "conf": HIGH,
                    "why": f"{m['source']}이(가) ‘{m['basis']}’에서 바뀌었습니다.", "origin": m["origin"]})
    return out


def doc_state(room, item_key, me_id, values):
    """작성 화면용: 칸별 출처·확신도, 대기 중인 제안(바뀐 값 포함)."""
    d = store.desk(room["id"], me_id)
    props = dict(d.get("proposals", {}).get(item_key, {}))
    changed = stale(room, item_key, me_id, values)
    if changed:
        propose(room["id"], item_key, me_id, changed)
        props = store.desk(room["id"], me_id).get("proposals", {}).get(item_key, {})
    props = {k: p for k, p in props.items() if _norm(p["value"]) != _norm(values.get(k))}
    return {"meta": d.get("meta", {}).get(item_key, {}), "proposals": props}


# ── 2) 원본 한 번 올리기 → 모든 서류에 나눠 넣기 ───────────────────────
def my_docs(room, members, me_id):
    return [(it, people) for it, people in deal_docs.room_items(room, members)
            if me_id in people and it["doc"] and _fields(it["doc"])]


def _current(room_id, it, me_id):
    v = store.draft(room_id, it["key"], me_id)
    if not v and it["shared"]:
        v = store.draft(room_id, it["key"], "")
    return v


def distribute(room, members, me, name, kind, payload, file_id=None):
    """kind: docx(기초 입력서 bytes) | image(data URL) | text(PDF 글). 서류별 제안 수를 돌려준다."""
    rid, me_id = room["id"], me["id"]
    docs = my_docs(room, members, me_id)
    union = {}
    for it, _ in docs:
        union.update(_fields(it["doc"]))
    found, doc_kind, issuer, note_ = {}, "", "", ""
    if kind == "docx":
        read = intake_docx.read(payload)
        item = read["items"][0]
        facts = item["facts"]
        doc_kind, issuer = "폐기물 기초 입력서", read["company"].get("company_name", "")
        for it, _ in docs:
            for k, v in doc_agent.from_facts(it["doc"], facts).items():
                found.setdefault(k, (v, True))
        comp = read["company"]
        mine = me["profile"].get("name", "")
        if comp.get("company_name") and mine and doc_agent._norm_name(comp["company_name"]) != doc_agent._norm_name(mine):
            note_ = (f"입력서의 회사(‘{comp['company_name']}’)가 우리 업체(‘{mine}’)와 달라 회사 정보 칸은 넣지 않았습니다. "
                     "폐기물 정보만 제안했습니다.")
            comp = {}
        for k, v in (("generator_name", comp.get("company_name")), ("generator_registration", comp.get("business_number")),
                     ("contact_name", comp.get("contact_name")), ("contact_phone", comp.get("contact_phone")),
                     ("generator_address", comp.get("site_address"))):
            if v and k in union:
                found[k] = (v, True)
        if len(read["items"]) > 1:
            note_ += (" " if note_ else "") + f"입력서의 폐기물 {len(read['items'])}개 중 첫 번째(‘{facts.get('name', '')}’)로 채웠습니다."
    else:
        if not advisor.ai_ready():
            raise ValueError("사진·PDF 읽기는 AI 연결이 필요합니다. .env의 키를 확인하세요.")
        my_role = next(m["role"] for m in members if m["company_id"] == me_id)
        fields = [{"key": k, "label": l} for k, l in union.items()]
        system = ("업로드한 자료(사업자등록증·허가증·이전 서류 등)에서 서식 칸에 들어갈 값을 찾습니다. 자료에 실제로 적힌 글자만 옮기고 "
                  "추측·계산·보정하지 않습니다. 칸 접두어 generator=배출업체, hauler=운반업체, receiver=처리업체입니다. "
                  f"올린 업체는 {deal_docs.ROLE_LABEL.get(my_role, '')}이며, 자료의 발급 대상 업체에 맞는 접두어 칸에만 넣습니다. "
                  "exact는 자료에 글자 그대로 있으면 true, 읽기 어렵거나 여러 값 중 골랐으면 false입니다. "
                  "JSON: {\"doc_kind\": 문서 종류, \"issuer\": 발급 기관, \"values\": {칸key: {\"value\": 값, \"exact\": true|false}}, \"note\": 확인할 점}\n"
                  f"[칸] {json.dumps(fields, ensure_ascii=False)}\n[거래방 업체] "
                  + json.dumps({deal_docs.ROLE_LABEL[m['role']]: store.company(m['company_id'])['profile'].get('name') for m in members},
                               ensure_ascii=False))
        user = ([{"type": "text", "text": "이 자료에서 칸 값을 찾아 주세요."}, {"type": "image_url", "image_url": {"url": payload}}]
                if kind == "image" else "자료 내용:\n" + payload[:14000])
        out = advisor._call([{"role": "system", "content": system}, {"role": "user", "content": user}], True)
        doc_kind, issuer, note_ = str(out.get("doc_kind") or "")[:60], str(out.get("issuer") or "")[:60], str(out.get("note") or "")[:300]
        for k, x in (out.get("values") or {}).items():
            if k not in union:
                continue
            v, exact = (x.get("value"), x.get("exact")) if isinstance(x, dict) else (x, False)
            if isinstance(v, str) and v.strip():
                found[k] = (doc_agent._tidy(k, unicodedata.normalize("NFC", v.strip())), exact is True)
    label = doc_kind or name
    origin = {"id": store.new_id("o_"), "name": name, "kind": doc_kind, "issuer": issuer, "file_id": file_id,
              "values": {k: v for k, (v, _) in found.items()}, "at": time.time()}
    store.update_desk(rid, me_id, lambda d: d.setdefault("originals", []).append(origin))
    made = []
    for it, _ in docs:
        doc = catalog.DOCS[it["doc"]]
        keys = _fields(it["doc"])
        cur = _current(rid, it, me_id)
        items = []
        for k, (v, exact) in found.items():
            if k not in keys or _norm(cur.get(k)) == _norm(v):
                continue
            ok, why = doc_agent.validate(doc, k, v)
            if not ok:
                continue
            items.append({"key": k, "value": v, "source": f"올린 원본 ‘{label}’", "kind": "original",
                          "conf": HIGH if exact else CHECK,
                          "why": ("지금 값 ‘" + cur[k] + "’과 다릅니다. " if cur.get(k) else "") + ("" if exact else "자료에서 읽기 어려웠거나 추정이 섞였을 수 있습니다.")})
        n = propose(rid, it["key"], me_id, items) if items else 0
        if n:
            made.append({"key": it["key"], "title": it["title"], "proposed": n})
    return {"original": {k: origin[k] for k in ("id", "name", "kind", "issuer")}, "found": len(found), "docs": made, "note": note_}


def originals(room_id, me_id):
    return store.desk(room_id, me_id).get("originals", [])


# ── 1) 진행판 ─────────────────────────────────────────────────────────
def _deadline(key, start):
    rule = DEADLINES.get(key)
    if not rule:
        return None
    text, calc = rule
    when = calc(start).isoformat() if (calc and start) else None
    if key == "annual" and not start:
        when = calc(date.today()).isoformat()
    return {"text": text, "date": when}


def board(room, members, names, me, ctx_for):
    """ctx_for(item_key) → 검토 맥락(doc_agent.review 용)."""
    rid, me_id = room["id"], me["id"]
    d = store.desk(rid, me_id)
    start = None
    if d.get("start_date"):
        try:
            start = date.fromisoformat(d["start_date"])
        except ValueError:
            start = None
    view = deal_docs.view(room, members, names, me_id)
    rows = {i["key"]: i for p in view["phases"] for i in p["items"]}
    my_role = next(m["role"] for m in members if m["company_id"] == me_id)
    roles_here = {m["role"]: m["company_id"] for m in members}
    docs, asks = [], {}
    check_total = prop_total = 0
    for it, people in deal_docs.room_items(room, members):
        if me_id not in people:
            continue
        row = rows.get(it["key"], {})
        done = row.get("done") if it["shared"] else row.get("my_done")
        entry = {"key": it["key"], "title": it["title"], "shared": it["shared"], "kind": it["kind"], "done": bool(done),
                 "doc": it["doc"], "deadline": _deadline(it["key"], start), "phase": it["phase"]}
        labels = _fields(it["doc"])
        if labels and not done:
            values = _current(rid, it, me_id)
            state = doc_state(room, it["key"], me_id, values)
            rv = doc_agent.review(it["doc"], values, ctx_for(it["key"]))
            low = [k for k, m in state["meta"].items() if m.get("conf") == CHECK and values.get(k)]
            errs = {f["key"] for f in rv["findings"] if f["key"] and f["severity"] in ("error", "warning")}
            entry.update(filled=sum(1 for k in labels if (values.get(k) or "").strip()), total=len(labels),
                         proposals=len(state["proposals"]),
                         changed=sum(1 for p in state["proposals"].values() if p.get("kind") == "changed"),
                         check=len(set(low) | errs | set(state["proposals"])), errors=rv["counts"]["error"])
            check_total += entry["check"]
            prop_total += entry["proposals"]
            if it["shared"]:  # 상대 몫 빈칸
                for k, lab in labels.items():
                    role = ROLE_OF_PREFIX.get(k.split("_", 1)[0])
                    if role and role != my_role and role in roles_here and not (values.get(k) or "").strip():
                        asks.setdefault(roles_here[role], {}).setdefault(it["title"], []).append(lab)
        docs.append(entry)
    # 법령 판단이 요구했는데 거래방 목록에 없는 서류
    judged = engine.result(store.casefile(rid).get("job"))
    present = {it["key"] for it, _ in deal_docs.room_items(room, members)}
    missing = []
    for x in (judged or {}).get("docs", []):
        for pat, keys, flag in ENGINE_DOCS:
            if re.search(pat, x.get("title", "")):
                if not present & set(keys):
                    missing.append({"title": x["title"], "why": x.get("why", ""), "basis": x.get("basis", ""),
                                    "flag": flag, "flag_label": FLAG_LABEL.get(flag, "")})
                break
    sent = d.get("requests", {})
    need_from = [{"company_id": cid, "name": names.get(cid, ""), "docs": [{"title": t, "fields": f} for t, f in docs_.items()],
                  "count": sum(len(f) for f in docs_.values()), "sent": sent.get(cid)} for cid, docs_ in asks.items()]
    todo = [x for x in docs if not x["done"]]
    drafted = [x for x in todo if x.get("filled")]
    upcoming = sorted([x for x in todo if x["deadline"] and x["deadline"]["date"]], key=lambda x: x["deadline"]["date"])
    parts = [f"이 거래에서 우리 업체가 챙길 서류는 {len(docs)}종이고 {len(docs) - len(todo)}종을 마쳤습니다."]
    if drafted:
        parts.append(f"{len(drafted)}종은 초안이 있고 확인할 칸이 {check_total}개입니다.")
    if prop_total:
        parts.append(f"에이전트가 제안한 값 {prop_total}개가 수락을 기다립니다.")
    if missing:
        parts.append(f"법령 판단에서 필요하다고 나온 서류 {len(missing)}종이 목록에 없습니다.")
    for n in need_from:
        parts.append(f"{n['name']}에 받을 칸이 {n['count']}개" + (" (요청 보냄)." if n["sent"] else "입니다."))
    if upcoming:
        parts.append(f"가장 가까운 기한은 ‘{upcoming[0]['title']}’ {upcoming[0]['deadline']['date']}입니다.")
    if not start and any(x["deadline"] and DEADLINES[x["key"]][1] and x["key"] != "annual" for x in todo):
        parts.append("폐기물 발생(첫 반출 예정)일을 알려 주시면 나머지 기한도 날짜로 계산합니다.")
    return {"headline": " ".join(parts), "docs": docs, "missing": missing, "need_from": need_from,
            "start_date": d.get("start_date", ""), "originals": [{k: o.get(k) for k in ("id", "name", "kind", "issuer", "at")}
                                                                 for o in d.get("originals", [])][-8:],
            "judged": bool(judged)}


def send_requests(room, members, names, me, board_view, only=None):
    """상대 업체 몫 빈칸을 거래방 채팅에 한 번에 요청한다."""
    sent = []
    for n in board_view["need_from"]:
        if only and n["company_id"] != only:
            continue
        lines = [f"{n['name']}께 요청드립니다. 함께 작성하는 서류에서 귀사가 채워 주셔야 할 칸입니다."]
        for x in n["docs"]:
            lines.append(f"· {x['title']}: " + ", ".join(x["fields"][:12]) + (" 외" if len(x["fields"]) > 12 else ""))
        lines.append("서류 탭에서 해당 서류를 열어 작성하거나, 채팅으로 값을 알려 주세요.")
        store.add_message(room["id"], me["id"], "text", "\n".join(lines))
        sent.append(n["company_id"])
    now = time.time()
    store.update_desk(room["id"], me["id"], lambda d: d.setdefault("requests", {}).update({c: now for c in sent}))
    return sent


def set_start(room_id, me_id, value):
    value = (value or "").strip()
    if value:
        date.fromisoformat(value)
    store.update_desk(room_id, me_id, lambda d: d.update(start_date=value))
