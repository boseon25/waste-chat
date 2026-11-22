"""플랫폼 API. app.py가 /api/p/ 아래 요청을 이 모듈의 handle()로 넘긴다."""
import base64
import json
import re
import unicodedata
from urllib.parse import quote

import threading

from . import advisor, catalog, deal_docs, desk, intake_chat, prep, doc_agent, engine, judgment_view, intake_docx, intake_flow, intake_handoff, part1_flow, tables, directory, envfile, geocode, pdf_writer, store, timely

PDF_LOCK = threading.Lock()  # app.py가 서식 출력에 쓰는 잠금으로 바꿔 끼운다.

TEXT_LIMITS = {"name": 80, "reg_no": 20, "ceo": 40, "address": 160, "phone": 30, "manager": 40, "manager_phone": 30,
               "industry": 80, "process": 300, "permit_no": 60, "permit_authority": 60, "methods": 200, "capacity": 60,
               "vehicles": 120, "intro": 400}
FILE_TYPES = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_FILE = 5 * 1024 * 1024
SECRET = re.compile(r"tgpt_sk_[A-Za-z0-9_-]{16,}", re.I)


class Fail(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def text(value, limit, required=None):
    value = unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else ""
    value = re.sub(r"[\u0000-\u0008\u000b-\u001f]", "", value)[:limit]
    if required and not value:
        raise Fail(400, required)
    if SECRET.search(value):
        raise Fail(400, "API 키는 서버의 .env 파일에만 넣어 주세요.")
    return value


def clean_profile(raw):
    if not isinstance(raw, dict):
        raise Fail(400, "업체 정보를 확인하세요.")
    p = {name: text(raw.get(name), limit) for name, limit in TEXT_LIMITS.items()}
    for name, label in (("name", "상호를"), ("address", "사업장 주소를"), ("manager", "담당자 이름을"), ("manager_phone", "담당자 연락처를")):
        if not p[name]:
            raise Fail(400, f"{label} 입력하세요.")
    roles = [r for r in directory.ROLES if isinstance(raw.get("roles"), list) and r in raw["roles"]]
    if not roles:
        raise Fail(400, "업체 유형을 하나 이상 고르세요.")
    p["roles"] = roles
    for name in ("wastes", "permit_wastes"):
        chosen = raw.get(name) if isinstance(raw.get(name), list) else []
        p[name] = [w for w in directory.WASTES if w in chosen]
    if "emitter" not in roles:
        p["wastes"] = []
    handler = any(r != "emitter" for r in roles)
    if not handler:
        p["permit_wastes"] = []
    if "emitter" in roles and not p["wastes"]:
        raise Fail(400, "주로 발생하는 폐기물 품목을 하나 이상 고르세요.")
    if handler and not p["permit_wastes"]:
        raise Fail(400, "영업대상(취급) 폐기물 품목을 하나 이상 고르세요.")
    if handler and not p["permit_no"]:
        raise Fail(400, "허가·신고 번호를 입력하세요.")
    try:
        p["lat"], p["lng"] = round(float(raw.get("lat")), 5), round(float(raw.get("lng")), 5)
    except (TypeError, ValueError):
        raise Fail(400, "사업장 위치가 지도에 표시되지 않았습니다. 주소를 다시 입력하거나 지도를 눌러 표시하세요.") from None
    if not (32.5 <= p["lat"] <= 39.5 and 124 <= p["lng"] <= 132):
        raise Fail(400, "사업장 위치는 국내 지도 범위 안에서 선택하세요.")
    key = raw.get("public_key") if isinstance(raw.get("public_key"), str) else ""
    p["public_key"] = key if handler and key in tables.public_index() else ""  # 업체가 직접 고른 공공자료 업체(허가 정보 연결)
    return p


def fit_values(doc_id, values):
    """서식 칸에 들어가지 않는 값은 미리 채우지 않는다. 한 칸 때문에 미리보기 전체가 실패하지 않게 한다."""
    doc = catalog.DOCS.get(doc_id)
    if not doc or not doc["fields"]:
        return {}
    labels = {f["key"] for f in doc["fields"]}
    usable = {k: v for k, v in values.items() if k in labels}
    with PDF_LOCK:
        try:
            pdf_writer.render_document(doc, usable)
            return usable
        except Exception:
            kept = {}
            for k, v in usable.items():
                try:
                    pdf_writer.render_document(doc, {k: v})
                    kept[k] = v
                except Exception:
                    continue
            return kept


def me_view(me):
    return {"id": me["id"], "login": me["login"], "demo": me["demo"], "profile": me["profile"]}


def member_room(room_id, me):
    room = store.room(room_id)
    members = store.members(room_id) if room else []
    if not room or me["id"] not in {m["company_id"] for m in members}:
        raise Fail(404, "참여 중인 거래방이 아닙니다.")
    return room, members


def room_people(members):
    companies = {m["company_id"]: store.company(m["company_id"]) for m in members}
    return companies, {cid: c["profile"].get("name", "") for cid, c in companies.items()}


def announce(room_id):
    """합의·서류 완료처럼 상태가 바뀌는 시점에 AI 안내를 채팅에 올린다."""
    room, members = store.room(room_id), store.members(room_id)
    _, names = room_people(members)
    done = list(room["announced"])
    if "agreed" not in done and len(members) >= 2 and all(m["agreed"] for m in members):
        done.append("agreed")
        store.add_message(room_id, "ai", "guide",
                          "모든 업체가 거래 진행에 합의했습니다. 지금부터 준비할 서류를 안내합니다.\n\n"
                          + deal_docs.guide_message(room, members, names)
                          + "\n\n오른쪽 ‘서류’ 탭에서 각자 작성 화면을 열 수 있습니다. 따로 준비하는 서류는 준비되면 체크하고, "
                            "함께 작성하는 서류는 작성 화면에서 ‘채팅방에 공유’를 누르면 이 대화에 올라옵니다.")
    if "agreed" in done:
        view = deal_docs.view(room, members, names, "")
        steps = deal_docs.steps(view, "")
        for index, phase in enumerate(steps["phases"]):
            tag = "phase:" + phase["id"]
            if phase["state"] != "done" or tag in done:
                continue
            done.append(tag)
            later = [p for p in steps["phases"][index + 1:] if p["state"] != "done"]
            if later:
                nxt = later[0]
                todo = "\n".join(f"  · {r['title']} — {r['who']}" for r in nxt["remaining"])
                rest = " → ".join(p["label"] for p in later[1:])
                body = (f"‘{phase['label']}’ 단계의 서류가 모두 준비됐습니다.\n\n다음 단계: {nxt['label']}\n{nxt['about']}\n{todo}"
                        + (f"\n\n그 뒤 남은 절차: {rest}" if rest else ""))
            else:
                body = f"‘{phase['label']}’ 단계까지 모든 절차를 마쳤습니다. 서명본과 증빙은 각 업체가 법정 기간 동안 보관하세요."
            store.add_message(room_id, "ai", "guide", body)
    if done != room["announced"]:
        store.update_room(room_id, announced=done)


def room_view(room, members, me, after=0, full=False):
    companies, names = room_people(members)
    docs_open = "agreed" in room["announced"]
    doc_view = deal_docs.view(room, members, names, me["id"]) if docs_open else None
    messages = []
    for m in store.messages(room["id"], after):
        row = {"id": m["id"], "kind": m["kind"], "body": m["body"], "created": m["created"], "sender": m["sender"],
               "name": names.get(m["sender"]) or {"ai": "AI 안내", "system": ""}.get(m["sender"], "나간 업체"),
               "mine": m["sender"] == me["id"]}
        if m["file_id"]:
            f = store.file(m["file_id"])
            if f:
                row["file"] = {"id": f["id"], "name": f["name"], "mime": f["mime"]}
        messages.append(row)
    invites = [{"id": r["id"], "name": store.company(r["to_id"])["profile"]["name"], "role": r["as_role"]}
               for r in store.room_requests(room["id"]) if r["status"] == "pending"]
    out = {"room": {"id": room["id"], "title": room["title"], "need": directory.need_summary(room["need"]),
                    "agreed": len(members) >= 2 and all(m["agreed"] for m in members), "docs_open": docs_open,
                    "can_delete": room["created_by"] == me["id"]},
           "members": [{**directory.public(companies[m["company_id"]], partner=True), "role": m["role"],
                        "role_label": deal_docs.ROLE_LABEL[m["role"]], "agreed": bool(m["agreed"]),
                        "me": m["company_id"] == me["id"]} for m in members],
           "messages": messages, "invites": invites, "docs": doc_view,
           "steps": deal_docs.steps(doc_view, me["id"]) if doc_view else None,
           "last_id": store.last_message_id(room["id"])}
    if full:
        out["ai_turns"] = store.ai_turns(room["id"], me["id"])
    return out


def request_view(r, me):
    incoming = r["to_id"] == me["id"]
    other = store.company(r["from_id"] if incoming else r["to_id"])
    room = store.room(r["room_id"]) if r["room_id"] else None
    return {"id": r["id"], "incoming": incoming, "status": r["status"], "created": r["created"], "note": r["note"],
            "other": directory.public(other, origin=me["profile"]), "need": directory.need_summary(r["need"]),
            "title": directory.need_title(r["need"]), "as_role": deal_docs.ROLE_LABEL.get(r["as_role"], ""),
            "invite": bool(r["need"].get("_invite")), "search_id": r["need"].get("_search"),
            "room_id": r["room_id"], "room_title": room["title"] if room else ""}


def asked_for(me, search_id):
    """이 거래 찾기 기록에서 보낸 요청의 상태(업체별). 다른 기록에서 맺은 예전 거래는 섞지 않는다."""
    out = {}
    for r in store.requests_for(me["id"]):
        if r["from_id"] == me["id"] and r["status"] != "declined" and r["need"].get("_search") == search_id:
            out[r["to_id"]] = {"status": r["status"], "room_id": r["room_id"]}
    return out


def bootstrap(me):
    rooms = []
    for room in store.rooms_for(me["id"]):
        members = store.members(room["id"])
        _, names = room_people(members)
        rooms.append({"id": room["id"], "title": room["title"], "last_id": store.last_message_id(room["id"]),
                      "members": [names[m["company_id"]] for m in members if m["company_id"] != me["id"]],
                      "docs_open": "agreed" in room["announced"]})
    return {"me": me_view(me), "searches": store.searches(me["id"]), "rooms": rooms,
            "requests": [request_view(r, me) for r in store.requests_for(me["id"])],
            "ai": {"ready": advisor.ai_ready(), "model": envfile.model() or timely.MODEL, "problem": advisor.PROBLEM["text"]}}


def search_view(s, me):
    asked = asked_for(me, s["id"])
    results = [{**c, "request": (asked.get(c["id"]) or {}).get("status", ""), "room_id": (asked.get(c["id"]) or {}).get("room_id")}
               for c in s["results"]]
    here = intake_flow.at_site(me, s["need"])
    return {"id": s["id"], "title": s["title"], "need": directory.need_summary(s["need"]), "turns": s["turns"],
            "results": results, "public": public_partners(s["need"], here, [c["name"] for c in results]),
            "intake": bool(s["need"].get("_intake")), "origin": (s["need"].get("_intake") or {}).get("origin")}


def public_partners(need, me, skip_names):
    """공공자료(환경부·한국환경공단 업체현황)에서 찾은 미가입 업체. 지도 위치는 시·군·구 중심점이다."""
    wastes = need.get("wastes") or []
    roles = [r for r in (need.get("want") or []) if r in ("recycler", "hauler", "disposer")]
    if not wastes or not roles:
        return []
    p = me["profile"]
    origin = (p.get("lat"), p.get("lng")) if p.get("lat") is not None else None
    out = []
    for c in tables.search_partners(wastes, roles, origin, limit=5, exclude_names=skip_names):
        line = []
        if c.get("rcodes"):
            line.append("재활용 유형 " + ", ".join(c["rcodes"][:3]))
        if c.get("methods"):
            line.append(", ".join(c["methods"][:2]))
        if c.get("products"):
            line.append("재생제품 " + ", ".join(c["products"][:2]))
        out.append({"id": c["id"], "name": c["name"], "roles": [c["role"]], "match_role": c["role"], "public": True,
                    "address": c["address"], "lat": c["lat"], "lng": c["lng"], "distance_km": c["distance_km"],
                    "wastes": [k[:24] for k in c["kinds"] if k][:4], "capacity": "", "vehicles": (c.get("vehicles") or "") and f"차량 {c['vehicles']}대",
                    "why": " · ".join(line), "phone": c.get("phone", ""), "license": c.get("license", ""),
                    "source": f"{c.get('source', '')} ({c.get('year', '')})"})
    return out


def decode_file(data, allowed=FILE_TYPES):
    mime = data.get("mime")
    if mime not in allowed or not isinstance(data.get("data"), str):
        raise Fail(400, "PDF, PNG, JPG, WebP 파일만 올릴 수 있습니다.")
    try:
        raw = base64.b64decode(data["data"], validate=True)
    except ValueError:
        raise Fail(400, "파일을 읽지 못했습니다.") from None
    if not 0 < len(raw) <= MAX_FILE:
        raise Fail(400, "5MB 이하 파일을 올려 주세요.")
    if mime == "application/pdf" and not raw.startswith(b"%PDF-"):
        raise Fail(400, "PDF 파일이 아닙니다.")
    name = re.sub(r'[<>:"/\\|?*\u0000-\u001f]', "_", text(data.get("name"), 100)) or "첨부" + allowed[mime]
    return name, mime, raw


# ── 요청 처리 ─────────────────────────────────────────────────────────
def handle(method, path, data, token):
    """(상태 코드, 본문 dict 또는 bytes 응답 dict)를 돌려준다."""
    try:
        return _route(method, [p for p in path.split("/") if p], data or {}, token)
    except Fail as exc:
        return {"status": exc.status, "json": {"error": str(exc)}}


def ok(body=None, **extra):
    return {"status": 200, "json": body if body is not None else {"ok": True}, **extra}


def _route(method, parts, data, token):
    me = store.session_company(token)
    head = parts[0] if parts else ""
    get, post = method == "GET", method == "POST"

    if head == "session" and get:
        if me:
            return ok({"signed_in": True, **bootstrap(me)})
        return ok({"signed_in": False, "demo": directory.demo_accounts(), "demo_password": directory.DEMO_PASSWORD,
                   "wastes": list(directory.WASTES), "roles": directory.ROLES})
    if head == "options" and get:
        return ok({"wastes": list(directory.WASTES), "roles": directory.ROLES, "demo": directory.demo_accounts(),
                   "demo_password": directory.DEMO_PASSWORD})
    if head == "geocode" and post:
        try:
            found = geocode.lookup(text(data.get("address"), 160))
        except ValueError as exc:
            raise Fail(502, str(exc)) from None
        if not found:
            raise Fail(404, "주소로 위치를 찾지 못했습니다. 시·군·구와 도로명을 확인하거나 지도를 눌러 표시하세요.")
        return ok(found)
    if head == "signup" and post:
        login = text(data.get("login"), 40).lower()
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        if not re.fullmatch(r"[a-z0-9_.-]{3,40}", login):
            raise Fail(400, "아이디는 영문 소문자·숫자 3~40자로 정하세요.")
        if not 6 <= len(password) <= 128:
            raise Fail(400, "비밀번호는 6자 이상으로 정하세요.")
        profile = clean_profile(data.get("profile"))
        try:
            cid = store.create_company(login, password, profile)
        except ValueError as exc:
            raise Fail(409, str(exc)) from None
        return ok(cookie=store.new_session(cid))
    if head == "login" and post:
        found = store.authenticate(text(data.get("login"), 40).lower(),
                                   data.get("password") if isinstance(data.get("password"), str) else "")
        if not found:
            raise Fail(401, "아이디 또는 비밀번호를 확인하세요.")
        if token:
            store.drop_session(token)
        return ok(cookie=store.new_session(found["id"]))
    if head == "logout" and post:
        if token:
            store.drop_session(token)
        return ok(cookie="")

    if not me:
        raise Fail(401, "로그인이 필요합니다.")

    if head == "bootstrap" and get:
        return ok(bootstrap(me))
    if head == "profile" and post:
        prof = clean_profile(data.get("profile"))
        old_p = me["profile"]
        if (prof["lat"], prof["lng"]) == (old_p.get("lat"), old_p.get("lng")):
            # 주소 글자만 바꾸고 지도 위치를 다시 찍지 않았으면 예전 좌표가 남아 거리 계산이 틀어진다.
            # 주소가 바뀌었거나, 좌표가 주소의 시·군·구에서 20km 넘게 떨어져 있으면 그 시·군·구 위치로 맞춘다.
            pt = intake_flow.site_point(prof["address"])
            if pt and (prof["address"] != old_p.get("address")
                       or (directory.distance_km(pt, prof) or 0) > 20):
                prof["lat"], prof["lng"] = round(pt["lat"], 5), round(pt["lng"], 5)
        store.update_profile(me["id"], prof)
        return ok({"me": me_view(store.company(me["id"]))})

    if head == "public-match" and post:
        found = tables.find_public(text(data.get("name"), 80), text(data.get("address"), 160), text(data.get("phone"), 30))
        taken = {c["profile"].get("public_key"): c["id"] for c in store.companies() if c["profile"].get("public_key")}
        return ok({"candidates": [{**x, "taken": bool(taken.get(x["key"]) and taken[x["key"]] != me["id"])} for x in found]})
    if head == "intake-template" and get:
        return {"status": 200, "bytes": intake_docx.TEMPLATE.read_bytes(),
                "type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "headers": {"Content-Disposition": "attachment; filename*=UTF-8''" + quote("폐기물_기초입력서.docx")}}
    if head == "case" and len(parts) >= 2:
        return _case(method, parts[1], parts[2:], data, me)
    if head == "intake" and post and len(parts) == 2:
        return _intake(parts[1], data, me)
    if head == "search":
        if post and len(parts) == 1:
            message = text(data.get("message"), 2000, "찾고 싶은 거래를 입력하세요.")
            s = store.search(data.get("id"), me["id"]) if isinstance(data.get("id"), str) else None
            if not s:
                s = {"id": store.new_id("s_"), "company_id": me["id"], "title": "", "need": directory.empty_need(),
                     "turns": [], "results": []}
            if s["need"].get("_intake"):  # 입력서로 시작한 기록: 조건은 그대로 두고 묻고 답한다
                try:
                    reply, cites, cards = intake_chat.answer(me, s, message)
                except ValueError as exc:
                    raise Fail(502, f"AI 답을 받지 못했습니다: {exc}") from None
                s["turns"] += [{"role": "user", "text": message},
                               {"role": "assistant", "text": reply, "mode": "ask", "cites": cites, "companies": cards}]
                s["turns"] = s["turns"][-80:]
                store.save_search(s)
                return ok(search_view(s, me))
            reply, need, results, mode = advisor.search_turn(me, s, message)
            s["turns"] += [{"role": "user", "text": message}, {"role": "assistant", "text": reply, "mode": mode}]
            s["turns"] = s["turns"][-80:]
            s["need"], s["results"] = need, results
            s["title"] = directory.need_title(need) if need["wastes"] else (s["title"] or directory.clean(message, 30))
            store.save_search(s)
            return ok(search_view(s, me))
        s = store.search(parts[1], me["id"]) if len(parts) > 1 else None
        if not s:
            raise Fail(404, "대화를 찾지 못했습니다.")
        if get:
            return ok(search_view(s, me))
        if post and parts[2:] == ["delete"]:
            store.delete_search(s["id"], me["id"])
            return ok()

    if head == "requests":
        if post and len(parts) == 1:
            target = store.company(data.get("to")) if isinstance(data.get("to"), str) else None
            if not target or target["id"] == me["id"]:
                raise Fail(400, "요청을 보낼 업체를 다시 선택하세요.")
            note = text(data.get("note"), 600)
            room_id = data.get("room_id") if isinstance(data.get("room_id"), str) else None
            if room_id:
                room, members = member_room(room_id, me)
                if target["id"] in {m["company_id"] for m in members}:
                    raise Fail(409, "이미 거래방에 참여 중인 업체입니다.")
                need = {**room["need"], "_invite": True}
            else:
                s = store.search(data.get("search_id"), me["id"]) if isinstance(data.get("search_id"), str) else None
                need = s["need"] if s else directory.clean_need(data.get("need"))
                if s:
                    need = {**need, "_search": s["id"]}  # 거래방을 열 때 그 대화의 법령 판단을 이어받는다
                if not need["wastes"]:
                    raise Fail(400, "거래할 폐기물 품목이 정해진 뒤에 요청을 보낼 수 있습니다.")
            if store.pending_between(me["id"], target["id"], room_id):
                raise Fail(409, "이미 보낸 요청이 답변을 기다리고 있습니다.")
            role = data.get("as_role") if data.get("as_role") in directory.DEAL_ROLES else directory.deal_role(target)
            store.create_request(me["id"], target["id"], room_id, role, need, note)
            if room_id:
                store.add_message(room_id, "system", "system",
                                  f"{me['profile']['name']}에서 {target['profile']['name']}에 "
                                  f"{deal_docs.ROLE_LABEL[role]} 참여를 요청했습니다.")
            return ok()
        r = store.request(parts[1]) if len(parts) > 1 else None
        if post and r and parts[2:] == ["cancel"]:  # 보낸 쪽이 답을 기다리는 요청을 거둬들인다
            if r["from_id"] != me["id"] or r["status"] != "pending":
                raise Fail(409, "답을 기다리는 내 요청만 취소할 수 있습니다.")
            store.delete_request(r["id"])
            if r["room_id"]:
                store.add_message(r["room_id"], "system", "system", f"{me['profile']['name']}에서 보낸 참여 요청을 취소했습니다.")
            return ok()
        if post and r and parts[2:] == ["decide"]:
            if r["to_id"] != me["id"] or r["status"] != "pending":
                raise Fail(409, "이미 처리했거나 답할 수 없는 요청입니다.")
            sender = store.company(r["from_id"])
            if not data.get("accept"):
                store.decide_request(r["id"], "declined")
                if r["room_id"]:
                    store.add_message(r["room_id"], "system", "system", f"{me['profile']['name']}에서 참여 요청을 거절했습니다.")
                return ok({"room_id": None})
            if r["room_id"]:
                room_id = r["room_id"]
                room = store.room(room_id)
                store.add_member(room_id, me["id"], r["as_role"])
                store.add_message(room_id, "system", "system",
                                  f"{me['profile']['name']}에서 {deal_docs.ROLE_LABEL[r['as_role']]}로 참여했습니다.")
                if "agreed" in room["announced"]:
                    members = store.members(room_id)
                    _, names = room_people(members)
                    extra = deal_docs.guide_message(store.room(room_id), members, names, only=me["id"])
                    store.add_message(room_id, "ai", "guide",
                                      f"{me['profile']['name']}에서 참여해 서류 목록을 갱신했습니다. 거래 조건을 확인한 뒤 "
                                      f"‘거래 진행에 합의’를 눌러 주세요.\n\n{extra}")
            else:
                title = f"{directory.need_title(r['need'])} · {sender['profile']['name']} ↔ {me['profile']['name']}"
                room_id = store.create_room(title, r["need"], r["from_id"])
                origin = store.casefile(r["need"].get("_search") or "")
                if origin["facts"] or origin["job"]:
                    store.save_casefile(room_id, facts=origin["facts"], job=origin["job"], version=origin["version"])
                carried = prep.copy_to_room(r["need"]["_search"], r["from_id"], room_id) if r["need"].get("_search") else 0
                mine = r["as_role"]
                theirs = directory.deal_role(sender)
                if theirs == mine:
                    theirs = "emitter" if mine != "emitter" else "processor"
                store.add_member(room_id, r["from_id"], theirs)
                store.add_member(room_id, me["id"], mine)
                summary = "\n".join(f"  · {s['label']}: {s['value']}" for s in directory.need_summary(r["need"]))
                store.add_message(room_id, "system", "system", f"{me['profile']['name']}에서 요청을 승인해 거래방이 열렸습니다.")
                if carried:
                    store.add_message(room_id, "system", "system",
                                      f"{sender['profile']['name']}에서 매칭 전에 준비한 서류 {carried}종을 이어받았습니다. 거래 합의 후 서류 탭에서 이어서 작성합니다.")
                store.add_message(room_id, "ai", "guide",
                                  f"{sender['profile']['name']}의 요청 내용입니다.\n{summary}"
                                  + (f"\n  · 전달 메모: {r['note']}" if r["note"] else "")
                                  + "\n\n품목의 성상·반입 기준·물량·단가와 운반 방법을 이 대화에서 맞춰 보세요. 조건이 정리되면 위의 "
                                    "‘거래 진행에 합의’를 각자 눌러 주세요. 운반업체가 따로 필요하면 ‘업체 초대’로 요청을 보낼 수 있습니다.")
            store.decide_request(r["id"], "accepted", room_id)
            announce(room_id)
            return ok({"room_id": room_id})

    if head == "files" and get and len(parts) == 2:
        f = store.file(parts[1])
        if not f:
            raise Fail(404, "파일을 찾지 못했습니다.")
        member_room(f["room_id"], me)
        return {"status": 200, "bytes": f["data"], "type": f["mime"],
                "headers": {"Content-Disposition": "inline; filename*=UTF-8''" + quote(f["name"])}}

    if head == "rooms" and len(parts) >= 2:
        room, members = member_room(parts[1], me)
        rid, rest = room["id"], parts[2:]
        if post and rest == ["delete"]:  # 거래방을 만든 업체만. 대화·서류·첨부·판단 기록까지 모두 지운다
            if room["created_by"] != me["id"]:
                raise Fail(403, "거래방을 만든 업체만 삭제할 수 있습니다.")
            store.delete_room(rid)
            return ok()
        if get and not rest:
            try:
                after = max(0, int(data.get("after", 0)))
            except (TypeError, ValueError):
                after = 0
            return ok(room_view(room, members, me, after, full=bool(data.get("full"))))
        if post and rest == ["messages"]:
            store.add_message(rid, me["id"], "text", text(data.get("text"), 4000, "메시지를 입력하세요."))
            return ok()
        if post and rest == ["files"]:
            name, mime, raw = decode_file(data)
            fid = store.add_file(rid, name, mime, raw, me["id"])
            store.add_message(rid, me["id"], "file", text(data.get("text"), 600), fid)
            return ok()
        if post and rest == ["agree"]:
            store.set_agreed(rid, me["id"], bool(data.get("agree")))
            store.add_message(rid, "system", "system", me["profile"]["name"] +
                              ("에서 거래 진행에 합의했습니다." if data.get("agree") else "에서 합의를 취소했습니다."))
            announce(rid)
            return ok()
        if post and rest == ["flags"]:
            flags = {name: bool(data.get(name)) for name in ("business", "designated", "construction", "classify")}
            store.update_room(rid, flags=flags)
            announce(rid)
            return ok()
        if post and rest == ["ai"]:
            message = text(data.get("message"), 2000, "질문을 입력하세요.")
            _, names = room_people(members)
            doc_view = deal_docs.view(room, members, names, me["id"]) if "agreed" in room["announced"] else None
            reply, mode = advisor.room_turn(message, store.ai_turns(rid, me["id"]), room, members, names, me["id"], doc_view)
            store.add_ai_turn(rid, me["id"], "user", message)
            store.add_ai_turn(rid, me["id"], "assistant", reply)
            return ok({"reply": reply, "mode": mode})
        if post and rest == ["find"]:
            want = data.get("want") if data.get("want") in directory.ROLES else "hauler"
            companies, _ = room_people(members)
            origin = next((companies[m["company_id"]] for m in members if m["role"] == "emitter"), me)
            found = directory.match({**room["need"], "want": [want]}, origin, exclude={m["company_id"] for m in members})
            pending = {r["to_id"] for r in store.room_requests(rid) if r["status"] == "pending"}
            return ok({"results": [{**c, "request": "pending" if c["id"] in pending else ""} for c in found],
                       "origin": directory.public(origin)})
        if post and rest == ["drafts"]:
            return ok(draft_all(room, members, me))
        if rest and rest[0] == "desk":
            return _desk(method, room, members, me, rest[1:], data)
        if len(rest) >= 2 and rest[0] == "docs":
            return _doc(method, room, members, me, rest[1], rest[2:], data)
    raise Fail(404, "지원하지 않는 주소입니다.")


# ── 법령 판단(파트 2 엔진) ─────────────────────────────────────────────
def _case_defaults(owner, me):
    """판단할 사실의 초기값: 가입 정보, 거래 조건, 작성 중인 서류."""
    if owner.startswith("r_"):
        room, members = member_room(owner, me)
        emitter = next((m["company_id"] for m in members if m["role"] == "emitter"), me["id"])
        profile = store.company(emitter)["profile"]
        docs = [vals for _i, _o, vals in reversed(store.room_drafts(owner, emitter))]
        return engine.facts_from(profile, room["need"], docs)
    s = store.search(owner, me["id"])
    if not s:
        raise Fail(404, "대화를 찾지 못했습니다.")
    return engine.facts_from(me["profile"], s["need"])


def case_access(owner, me):
    if owner.startswith("r_"):
        member_room(owner, me)
    elif not store.search(owner, me["id"]):
        raise Fail(404, "대화를 찾지 못했습니다.")


def clean_facts(raw):
    if not isinstance(raw, dict):
        raise Fail(400, "판단할 내용을 확인하세요.")
    out = {}
    for k in engine.FACT_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            if SECRET.search(v):
                raise Fail(400, "키나 비밀번호로 보이는 값은 넣지 마세요.")
            out[k] = v.strip()[:400]
    if out.get("code"):
        ok_code, msg = tables.check_code(out["code"])
        if not ok_code:
            raise Fail(400, msg)
    return out


def _point(region, i):
    """'경기 화성시' → 시·군·구 중심 좌표. 같은 곳의 업체 핀이 겹치지 않게 조금씩 비켜 놓는다(표시용)."""
    import math
    parts = (region or "").split()
    if not parts:
        return None, None
    lat, lng = tables._place(parts[0][:2], parts[1] if len(parts) > 1 else "")
    if lat is None:
        return None, None
    if i:
        a = i * 2.4
        lat, lng = lat + 0.006 * math.sqrt(i) * math.cos(a), lng + 0.008 * math.sqrt(i) * math.sin(a)
    return lat, lng


def _recommend(s_, me, plain):
    """법령 기준 추천: 판단된 경로의 공공자료 업체에 지도 좌표를 붙이고, 같은 경로에 맞는 앱 가입 업체(거래 요청 가능)를 더한다."""
    if not plain or plain.get("mismatch") or not plain["routes"]:
        return
    rt = plain["routes"][0]
    seen = {}
    for p in rt["partners"]:
        key = p.get("region") or ""
        p["lat"], p["lng"] = _point(key, seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1
    role = "disposer" if re.search(r"소각|처분|매립", rt["title"]) and "재활용" not in rt["title"] else "recycler"
    need = s_["need"]
    here = intake_flow.at_site(me, need)
    asked_full = asked_for(me, s_["id"])
    asked = {k: v["status"] for k, v in asked_full.items()}
    room_of = {k: v["room_id"] for k, v in asked_full.items()}
    linked = {c["profile"]["public_key"]: c for c in store.companies() if c["profile"].get("public_key") and c["id"] != me["id"]}
    # ① 추천 목록의 공공자료 업체 중 앱에 가입해 연결된 곳 → 거래 요청 가능
    for p in rt["partners"]:
        c = linked.get(p.get("key"))
        if c:
            p["member"] = {"id": c["id"], "name": c["profile"]["name"], "roles": c["profile"]["roles"], "request": asked.get(c["id"], ""),
                           "room_id": room_of.get(c["id"])}
    # ② 목록 밖이지만 연결된 가입 업체가 같은 법령 기준(분류번호 중분류·재활용 유형)을 통과하면 맨 위에
    rcodes = sorted({rc for p in rt["partners"] for rc in p.get("does", [])}) or re.findall(r"R-\d+(?:-\d+)?", rt["title"])
    shown = {p.get("key") for p in rt["partners"]}
    code = plain.get("code") or ""
    verified = []
    for key, c in linked.items():
        if key in shown or role not in c["profile"].get("roles", []):
            continue
        why = tables.public_passes(key, code, rcodes) if role == "recycler" else ""
        if why:
            pub = tables.public_index()[key]
            verified.append({**directory.public(c, origin=here["profile"]), "request": asked.get(c["id"], ""), "room_id": room_of.get(c["id"]), "basis": why,
                             "region": f"{pub['sido']} {pub['sigungu']}"})
    verified.sort(key=lambda c: c.get("distance_km") or 9999)
    plain["verified"] = verified[:5]
    # ③ 공공자료와 연결되지 않은 가입 업체: 등록한 품목만 맞음(허가 미확인)
    members = [c for c in directory.match({**need, "want": [role]}, here, limit=12)
               if role in c["roles"] and not store.company(c["id"])["profile"].get("public_key")][:4]
    plain["members"] = [{**c, "request": asked.get(c["id"], ""), "room_id": room_of.get(c["id"]),
                         "note": "공공자료와 연결되지 않아 허가 미확인 · 가입 때 등록한 취급 품목만 맞음"} for c in members]
    plain["origin"] = (need.get("_intake") or {}).get("origin")


def case_view(owner, me=None):
    view = engine.view(owner)
    view["plain"] = judgment_view.build(view.get("result"), view.get("facts") or {})
    if me and owner.startswith("s_") and view["plain"]:
        s_ = store.search(owner, me["id"])
        if s_ and s_["need"].get("_intake"):
            _recommend(s_, me, view["plain"])
            view["search_need"] = s_["need"]
    if me and owner.startswith("s_") and view["plain"] and "emitter" in (me["profile"].get("roles") or ["emitter"]):
        its = prep.items(view.get("result"))
        view["prep"] = prep.status(owner, me["id"], its)
        for d in view["plain"]["docs"]:
            for pat, ks in prep.FROM_ENGINE:
                if re.search(pat, d["title"]):
                    d["prep_key"] = next((k for k in ks if k in {i["key"] for i in its}), None)
                    break
    return view


def _case(method, owner, rest, data, me):
    case_access(owner, me)
    if len(rest) >= 2 and rest[0] == "docs":
        s_ = store.search(owner, me["id"]) if owner.startswith("s_") else None
        if not s_:
            raise Fail(404, "거래 찾기 기록에서만 미리 작성할 수 있습니다.")
        res = engine.result(store.casefile(owner).get("job"))
        room, members = prep.fake_room(s_, me, res)
        return _doc(method, room, members, me, rest[1], rest[2:], data, prep_items=prep.items(res))
    if method == "GET" and not rest:
        view = case_view(owner, me)
        if not view["facts"]:
            view["facts"] = _case_defaults(owner, me)
            view["prefilled"] = True
        view["codes"] = intake_flow.extras(view["facts"])["codes"]  # 입력서 화면과 같은 순서(이름·공정 먼저, 주성분만)
        if owner.startswith("s_"):
            s_ = store.search(owner, me["id"])
            if s_ and s_["need"].get("_intake"):
                view["source"] = "intake"
        if view["facts"].get("code"):
            view["code_check"] = tables.check_code(view["facts"]["code"])[1]
        ho = engine.handoff(owner) if owner.startswith("s_") else None
        if ho:  # 분류 에이전트가 정한 번호: 화면에서 고르지 않는다
            cl = ho["case"].get("classification") or {}
            view["code_fixed"] = {"code": ho["base"].get("code", ""), "label": cl.get("code_label", ""),
                                  "basis": cl.get("basis", ""), "conditions": cl.get("conditions", "")}
        return ok(view)
    if method == "POST" and rest == ["intake"]:
        return ok(_docx(data))
    if method == "POST" and rest == ["facts"]:
        store.save_casefile(owner, facts=_fixed_code(owner, clean_facts(data.get("facts"))))
        return ok(case_view(owner, me))
    if method == "POST" and rest == ["run"]:
        facts = _fixed_code(owner, clean_facts(data.get("facts")))
        if not facts.get("code"):
            raise Fail(400, "분류번호(00-00-00)를 먼저 정해 주세요. 후보 중에서 고르거나, 모르면 ‘분류 확인’ 절차로 기관에 확인합니다.")
        if not facts.get("name"):
            raise Fail(400, "폐기물 이름을 적어 주세요.")
        engine.start(owner, facts)
        return ok(case_view(owner, me))
    raise Fail(404, "지원하지 않는 주소입니다.")


def _docx(data):
    f = data.get("file") if isinstance(data.get("file"), dict) else {}
    try:
        raw = base64.b64decode(f.get("data") or "", validate=True)
    except ValueError:
        raise Fail(400, "파일을 읽지 못했습니다.") from None
    if not raw or len(raw) > MAX_FILE or not raw.startswith(b"PK"):
        raise Fail(400, "Word(.docx) 기초 입력서를 5MB 이하로 올려 주세요.")
    try:
        return intake_docx.read(raw)
    except ValueError as exc:
        raise Fail(400, f"기초 입력서를 읽지 못했습니다: {exc}") from None


def _fixed_code(owner, facts):
    """전달 파일로 시작한 건은 분류번호를 전달 파일 값으로 고정한다."""
    ho = engine.handoff(owner) if owner.startswith("s_") else None
    if ho and ho["base"].get("code"):
        facts["code"] = ho["base"]["code"]
    return facts


def _upload(data, what="파일"):
    f = data.get("file") if isinstance(data.get("file"), dict) else {}
    try:
        raw = base64.b64decode(f.get("data") or "", validate=True)
    except ValueError:
        raise Fail(400, "파일을 읽지 못했습니다.") from None
    if not raw or len(raw) > MAX_FILE:
        raise Fail(400, f"{what}을 5MB 이하로 올려 주세요.")
    return raw, str(f.get("name") or "")


def _intake_file(data, me):
    """올린 파일 → 입력서 읽기.
    - 분류 에이전트 전달 파일(.json): 분류번호를 그 파일 값으로 정한다.
    - Word 기초 입력서(.docx): waste_agent 파트 1 절차(접수 → 확인 질문 → 분류번호 → 내보내기)로 읽는다."""
    raw, name = _upload(data)
    if raw.lstrip()[:1] in (b"{", b"\xef") or name.lower().endswith(".json"):
        try:
            return {**intake_handoff.read(raw), "kind": "handoff"}
        except ValueError as exc:
            raise Fail(400, f"전달 파일을 읽지 못했습니다: {exc}") from None
    if not raw.startswith(b"PK"):
        raise Fail(400, "Word(.docx) 기초 입력서나 전달 파일(.json)을 올려 주세요.")
    try:
        return {**part1_flow.read(me, raw, name), "kind": "part1"}
    except part1_flow.Part1Error as exc:
        raise Fail(400, str(exc)) from None


def _p1(fn, *args):
    try:
        return ok(fn(*args))
    except part1_flow.Part1Error as exc:
        raise Fail(400, str(exc)) from None


def _handoff_case(raw):
    """화면이 돌려준 전달 건: 다시 읽어 검사한다(이름·크기). 화면 사실의 기준값(base)도 서버에서 다시 만든다."""
    if not isinstance(raw, dict) or not raw.get("name"):
        raise Fail(400, "전달 파일의 폐기물 건을 확인하세요.")
    if len(json.dumps(raw, ensure_ascii=False).encode()) > intake_handoff.MAX_CASE_BYTES:
        raise Fail(400, "전달 파일의 건이 너무 큽니다.")
    base = intake_handoff.facts_of(raw)
    if base.get("code") and not tables.check_code(base["code"])[0]:
        base.pop("code")
    return raw, base


def _intake_facts(raw, strict=True):
    """보충 질문의 '모름'은 그대로 받고(다시 묻지 않게), 나머지는 판단 사실 검사와 같게 본다."""
    raw = raw if isinstance(raw, dict) else {}
    unknown = {k for k in engine.FACT_KEYS if raw.get(k) == intake_flow.UNKNOWN}
    code = str(raw.get("code") or "").strip()
    facts = clean_facts({k: v for k, v in raw.items() if k not in unknown and k != "code"})
    if code:
        good, msg = tables.check_code(code)
        if not good and strict:
            raise Fail(400, msg)
        facts["code"] = code
    return facts, unknown


def _intake(action, data, me):
    """기초 입력서로 시작하기: read(입력서 읽기) → check(고친 값으로 질문·후보 다시 보기) → start(업체 찾기 + 법령 판단)."""
    if action == "read":
        out = _intake_file(data, me)
        if out["kind"] == "part1":
            return ok({"company": out["company"], "items": out["items"], "kind": "part1", "sid": out["sid"]})
        items = []
        for it in out["items"]:
            v = intake_flow.item_view(it, me["profile"])
            # 분류번호는 전달 파일 값으로 정해져 있다(사람이 고르지 않음)
            v.update({"handoff": it["handoff"], "code_fixed": True, "code_label": it["code_label"], "code_basis": it["code_basis"],
                      "code_conditions": it["code_conditions"], "synthetic": it["synthetic"]})
            items.append(v)
        return ok({"company": out["company"], "items": items, "kind": "handoff", "schema": out.get("schema", ""), "notice": out.get("notice", "")})
    p1 = data.get("part1") if isinstance(data.get("part1"), dict) else {}
    if action == "p1-answer":
        return _p1(part1_flow.answer, me, p1.get("sid"), p1.get("case"), data.get("request"), data.get("value"))
    if action == "p1-code":
        return _p1(part1_flow.set_code, me, p1.get("sid"), p1.get("case"), str(data.get("code") or "").strip(), data.get("reference"))
    if action == "p1-attach":
        raw, name = _upload(data, "문서")
        return _p1(part1_flow.attach, me, p1.get("sid"), p1.get("case"), data.get("kind"), data.get("issuer"), raw, name)
    if action == "check":
        facts, unknown = _intake_facts(data.get("facts"), strict=False)
        return ok(intake_flow.extras({**facts, **{k: intake_flow.UNKNOWN for k in unknown}}))
    if action == "start":
        facts, unknown = _intake_facts(data.get("facts"))
        if p1:  # Word 입력서: 파트 1 답·분류번호·첨부를 넣어 다시 내보낸 최신본으로 판단한다
            try:
                ho = _handoff_case(part1_flow.engine_case(me["id"], p1.get("sid"), p1.get("case")))
            except part1_flow.Part1Error as exc:
                raise Fail(400, str(exc)) from None
            facts["code"] = ho[1].get("code", "")  # 분류번호는 파트 1에 기록된 값만 쓴다(없으면 업체만 찾고 판단은 보류)
        else:
            ho = _handoff_case(data["handoff"]) if data.get("handoff") is not None else None
            if ho:
                facts["code"] = ho[1].get("code", "")  # 분류번호는 전달 파일 값만 쓴다
                if not facts["code"]:
                    raise Fail(400, "전달 파일에 쓸 수 있는 분류번호(selected_code)가 없습니다. 분류 에이전트에서 번호를 정한 뒤 다시 올려 주세요.")
        if not facts.get("name"):
            raise Fail(400, "폐기물 이름을 적어 주세요.")
        wastes = intake_flow.clean_wastes(data.get("wastes")) or intake_flow.wastes_for(facts)
        if not wastes:
            raise Fail(400, "업체를 찾을 품목을 하나 이상 골라 주세요.")
        need = intake_flow.need_from(facts, wastes, intake_flow.clean_want(data.get("want")))
        sources = [text(x, 300) for x in (data.get("sources") or [])[:40] if isinstance(x, str)]
        origin = intake_flow.site_point(facts.get("address"))  # 거리는 입력서의 배출 현장에서 잰다(가입 주소가 아니라)
        s = {"id": store.new_id("s_"), "company_id": me["id"], "title": directory.need_title(need) + " · 입력서",
             "need": {**need, "_intake": {"sources": sources, "unknown": sorted(unknown), "origin": origin}}, "turns": [], "results": []}
        here = intake_flow.at_site(me, s["need"])
        s["results"] = directory.match(need, here)
        public = public_partners(need, here, [c["name"] for c in s["results"]])
        running = bool(facts.get("code"))
        if ho:
            engine.set_handoff(s["id"], ho[0], ho[1])
            s["need"]["_intake"]["handoff"] = True
        if running:
            engine.start(s["id"], facts)
        else:
            store.save_casefile(s["id"], facts=facts)
        shown = {**facts, **{k: intake_flow.UNKNOWN for k in unknown}}
        fname = text(data.get("file"), 80)
        what = "기초 입력서" if p1 or not ho else "분류 에이전트 전달 파일"
        s["turns"] = [{"role": "user", "text": f"{what}{f'({fname})' if fname else ''}의 ‘{facts['name']}’로 업체를 찾아 주세요."},
                      {"role": "assistant", "mode": "intake",
                       "text": intake_flow.summary(shown, need, s["results"], public, running, not running)
                               .replace("기초 입력서의", "분류 에이전트 전달 파일의" if ho and not p1 else "기초 입력서의", 1)}]
        store.save_search(s)
        return ok(search_view(s, me))
    raise Fail(404, "지원하지 않는 주소입니다.")


def _engine_done(owner, job):
    """거래방 판단이 끝나면 채팅에 알린다."""
    if not owner.startswith("r_"):
        return
    res = engine.result(job)
    if job["status"] != "done" or not res:
        store.add_message(owner, "system", "system", "법령 판단이 끝나지 못했습니다. ‘법령 판단’ 탭에서 다시 실행해 주세요.")
        return
    route = (res["routes"] or [{}])[0]
    text_ = (f"법령 판단을 마쳤습니다. 분류번호 {res.get('code') or '-'}, "
             f"{'지정폐기물' if res.get('status') == 'designated' else '일반폐기물'} 기준으로 "
             + (f"‘{route.get('kind') or route.get('title')}’ 경로가 {route.get('legal')} 판단입니다. " if route else "가능한 경로를 찾지 못했습니다. ")
             + (f"필요한 서류 {len(res['docs'])}종, 추가로 확인할 질문 {len(res['questions'])}개가 있습니다. " if res["docs"] or res["questions"] else "")
             + "자세한 근거는 ‘법령 판단’ 탭에서 볼 수 있습니다.")
    store.add_message(owner, "system", "system", text_)


engine.on_done(_engine_done)


def draft_all(room, members, me):
    """내가 맡은 서류마다 업체 정보·거래 조건·법령 판단 사실·다른 서류 값으로 초안을 만든다. 이미 적은 값은 그대로 둔다."""
    if "agreed" not in room["announced"]:
        raise Fail(409, "거래 합의 후 서류 목록이 열린 뒤에 만들 수 있습니다.")
    rid = room["id"]
    companies, _ = room_people(members)
    made = []
    for it, people in deal_docs.room_items(room, members):
        doc = catalog.DOCS.get(it["doc"] or "")
        if me["id"] not in people or not doc or not doc.get("fields"):
            continue
        keys = {f["key"] for f in doc["fields"]}
        layer = desk.layers(room, members, companies, me["id"], it)
        values = {k: x["value"] for k, x in layer.items()}
        own = store.draft(rid, it["key"], me["id"])
        values.update(own)
        values = fit_values(it["doc"], {k: v for k, v in values.items() if k in keys})
        if values != own:
            store.save_draft(rid, it["key"], me["id"], values)
        desk.note(rid, it["key"], me["id"], values, layer)
        made.append({"key": it["key"], "title": it["title"], "filled": len(values), "total": len(keys),
                     "added": len(set(values) - set(own))})
    return {"docs": made}


def doc_ctx(room, members, me, it, data=None):
    """서류 작성 에이전트·검토에 주는 거래방 맥락."""
    rid = room["id"]
    _, names = room_people(members)
    view = deal_docs.view(room, members, names, me["id"])
    states = {i["key"]: i for p in view["phases"] for i in p["items"]}
    done = lambda k: bool(states.get(k) and states[k]["done"])
    by_role = {}
    for m in members:
        by_role.setdefault(m["role"], []).append(names[m["company_id"]])
    my_role = next(m["role"] for m in members if m["company_id"] == me["id"])
    titles = {i["key"]: i["title"] for i, _ in deal_docs.room_items(room, members)}
    others = [(titles[o], saved) for o, _owner, saved in store.room_drafts(rid, me["id"]) if o != it["key"] and o in titles]
    prof = me["profile"]
    profile = {k: prof.get(k) for k in ("name", "reg_no", "ceo", "address", "phone", "industry", "permit_no",
                                        "permit_authority", "process", "manager", "manager_phone") if prof.get(k)}
    case = store.casefile(rid)
    judged = engine.result(case.get("job"))
    return {"me_role": my_role, "me_name": prof.get("name", ""), "members": by_role, "others": others,
            "engine": judged, "facts": case.get("facts") or {}, "interview": bool((data or {}).get("interview")),
            "author_role": it["author"] or my_role, "profile": profile, "flags": room["flags"],
            "originals": desk.originals(rid, me["id"]),
            "form5_ready": done("form5_hauler") or done("form5_processor"), "permit_hauler": done("permit_hauler"),
            "permit_processor": done("permit_processor"), "bond": done("bond"),
            "analysis": done("analysis") or done("lab_result")}


def _desk(method, room, members, me, rest, data):
    """서류 비서: 진행판, 기준일, 상대 업체 요청, 원본 올리기."""
    if "agreed" not in room["announced"]:
        raise Fail(409, "거래 합의 후 서류 목록이 열린 뒤에 쓸 수 있습니다.")
    _, names = room_people(members)
    ctx_for = lambda key: doc_ctx(room, members, me, deal_docs.BY_KEY[key])
    if method == "GET" and not rest:
        return ok(desk.board(room, members, names, me, ctx_for))
    if method != "POST":
        raise Fail(404, "지원하지 않는 주소입니다.")
    if rest == ["start"]:
        try:
            desk.set_start(room["id"], me["id"], text(data.get("date"), 10))
        except ValueError:
            raise Fail(400, "날짜를 YYYY-MM-DD로 적어 주세요.") from None
        return ok(desk.board(room, members, names, me, ctx_for))
    if rest == ["request"]:
        view = desk.board(room, members, names, me, ctx_for)
        only = data.get("company") if isinstance(data.get("company"), str) else None
        sent = desk.send_requests(room, members, names, me, view, only)
        if not sent:
            raise Fail(400, "상대 업체에 요청할 빈칸이 없습니다.")
        announce(room["id"])
        return ok({"sent": len(sent), **desk.board(room, members, names, me, ctx_for)})
    if rest == ["original"]:
        f = data.get("file") if isinstance(data.get("file"), dict) else {}
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        try:
            if f.get("mime") == docx_mime or str(f.get("name", "")).lower().endswith(".docx"):
                raw = base64.b64decode(f.get("data") or "", validate=True)
                if not raw.startswith(b"PK") or len(raw) > MAX_FILE:
                    raise Fail(400, "Word(.docx) 기초 입력서를 5MB 이하로 올려 주세요.")
                out = desk.distribute(room, members, me, text(f.get("name"), 100) or "기초 입력서.docx", "docx", raw)
            else:
                name, mime, raw = decode_file(f)
                fid = store.add_file(room["id"], name, mime, raw, me["id"])  # 채팅에는 올리지 않는다(우리 업체 자료)
                if mime == "application/pdf":
                    import io
                    from pypdf import PdfReader
                    body = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(raw)).pages[:12])
                    if len(body.strip()) < 30:
                        raise Fail(400, "이 PDF에서 글자를 읽지 못했습니다. 스캔본이면 사진(JPG·PNG)으로 올려 주세요.")
                    out = desk.distribute(room, members, me, name, "text", body, fid)
                else:
                    out = desk.distribute(room, members, me, name, "image", f"data:{mime};base64," + base64.b64encode(raw).decode(), fid)
        except ValueError as exc:
            raise Fail(400, str(exc)) from None
        return ok({**out, "board": desk.board(room, members, names, me, ctx_for)})
    raise Fail(404, "지원하지 않는 주소입니다.")


def _doc(method, room, members, me, key, rest, data, prep_items=None):
    """거래방 서류 작성. prep_items가 있으면 매칭 전 준비 공간(우리 업체만, 공유·완료 표시 없음)."""
    rid = room["id"]
    if prep_items is not None:
        found = next(((it, [me["id"]]) for it in prep_items if it["key"] == key), None)
        if not found:
            raise Fail(404, "이 판단에서 미리 작성할 수 있는 서류가 아닙니다.")
        if method == "POST" and (rest in (["share"], ["ask"]) or data.get("action") in ("done", "undone", "confirm")):
            raise Fail(409, "업체에 거래를 요청해 거래방이 열린 뒤에 할 수 있습니다. 지금은 우리 쪽 내용을 미리 작성해 둡니다.")
    else:
        found = next(((it, people) for it, people in deal_docs.room_items(room, members) if it["key"] == key), None)
    if not found or "agreed" not in room["announced"]:
        raise Fail(404, "이 거래방의 서류 목록에 없는 항목입니다.")
    it, people = found
    if me["id"] not in people:
        raise Fail(403, "이 서류의 담당 업체가 아닙니다.")
    companies, names = room_people(members)

    def editing_others():
        if prep_items is not None:
            return []
        view = deal_docs.view(room, members, names, me["id"])
        row = next(i for p in view["phases"] for i in p["items"] if i["key"] == key)
        return [e["name"] for e in row.get("editing", []) if e["id"] != me["id"]]

    def clean_values():
        values = data.get("values")
        if not isinstance(values, dict) or len(values) > 250:
            return None
        lock = set(prep.locked(it)) if prep_items is not None else set()  # 매칭 전에는 상대 업체 칸·협의 값을 저장하지 않는다
        return {k: v[:5000] for k, v in values.items() if isinstance(k, str) and isinstance(v, str) and v.strip()
                and not SECRET.search(v) and k not in lock}

    def agent_ctx():
        return {**doc_ctx(room, members, me, it, data), "locked": prep.locked(it) if prep_items is not None else []}

    lock_now = prep.locked(it) if prep_items is not None else []
    labels_ = {f["key"]: f["label"] for f in catalog.DOCS.get(it["doc"] or "", {}).get("fields", [])}

    def review_of(values):
        rv = doc_agent.review(it["doc"], values, agent_ctx())
        if prep_items is not None:
            rv = prep.filter_review(rv, lock_now)
            rv["empty"] = [e for e in rv["empty"] if e not in {labels_.get(k) for k in lock_now}]
        return rv

    if method == "POST" and rest == ["agent"]:
        message = text(data.get("message"), 3000, "내용을 입력하세요.")
        if SECRET.search(message):
            raise Fail(400, "키나 비밀번호로 보이는 값은 대화에 넣지 마세요.")
        values = clean_values() or {}
        turns = data.get("turns") if isinstance(data.get("turns"), list) else []
        return ok(doc_agent.turn(it["doc"], message, values, data.get("focus"), turns[-10:], agent_ctx()))
    if method == "POST" and rest == ["fix"]:
        values = clean_values() or {}
        out = doc_agent.fix(it["doc"], values, agent_ctx())
        srcs = {f["key"]: f["source"] for r in out["rounds"] for f in r["fixed"]}
        props = [{"key": k, "value": v, "source": srcs.get(k, "작성 에이전트"), "kind": "fix",
                  "conf": desk.CHECK if srcs.get(k, "").startswith("작성 에이전트") else desk.HIGH,
                  "why": ("지금 값 ‘" + values[k] + "’을 바꿉니다. " if values.get(k) else "") + "검토에서 나온 문제를 고치는 값입니다."}
                 for k, v in out["updates"].items()]
        if props:
            desk.propose(rid, key, me["id"], props)
        st = desk.doc_state(room, key, me["id"], values)
        return ok({**out, "proposals": st["proposals"], "review": review_of(values)})
    if method == "POST" and rest == ["decide"]:
        acc = [k for k in (data.get("accept") or []) if isinstance(k, str)][:200]
        rej = [k for k in (data.get("reject") or []) if isinstance(k, str)][:200]
        edits = {k: v[:5000] for k, v in (data.get("edits") or {}).items() if isinstance(k, str) and isinstance(v, str)}
        applied = desk.decide(room, members, me["id"], key, acc, rej, edits)
        values = {**(clean_values() or {}), **applied}
        st = desk.doc_state(room, key, me["id"], values)
        return ok({"applied": applied, **st, "review": review_of(values)})
    if method == "POST" and rest == ["extract"]:
        f = data.get("file") if isinstance(data.get("file"), dict) else {}
        name, mime, raw = decode_file(f)
        try:
            if mime == "application/pdf":
                import io
                from pypdf import PdfReader
                body = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(raw)).pages[:12])
                if len(body.strip()) < 30:
                    raise Fail(400, "이 PDF에서 글자를 읽지 못했습니다. 스캔본이면 사진(JPG·PNG)으로 올려 주세요.")
                out = doc_agent.extract(it["doc"], "text", body, clean_values() or {}, agent_ctx())
            else:
                out = doc_agent.extract(it["doc"], "image", f"data:{mime};base64," + base64.b64encode(raw).decode(),
                                        clean_values() or {}, agent_ctx())
        except ValueError as exc:
            raise Fail(400, str(exc)) from None
        return ok({**out, "file": name})
    if method == "POST" and rest == ["review"]:
        return ok(review_of(clean_values() or {}))
    if method == "POST" and rest == ["ask"]:
        body = text(data.get("message"), 1500, "보낼 내용을 입력하세요.")
        store.add_message(rid, me["id"], "text", body)
        return ok()
    if method == "GET" and rest == ["prefill"]:
        layer = desk.layers(room, members, companies, me["id"], it)
        values = {k: x["value"] for k, x in layer.items()}
        sources = {k: x["source"] for k, x in layer.items()}
        if it["shared"]:
            shared = store.draft(rid, key, "")
            state = next((s for s in store.doc_states(rid) if s["item"] == key and s["company_id"] == ""), None)
            for k, v in shared.items():
                values[k] = v
                sources[k] = (state["extra"].get("by_name", "") + " 공유본") if state else "공유본"
        for k, v in store.draft(rid, key, me["id"]).items():
            values[k] = v
            sources[k] = "이전에 작성하던 내용"
        values = fit_values(it["doc"], values)
        desk.note(rid, key, me["id"], values, layer)
        st = desk.doc_state(room, key, me["id"], values)
        for k, m in st["meta"].items():
            if values.get(k) and m.get("source") and sources.get(k) in ("이전에 작성하던 내용", None):
                sources[k] = m["source"]
        basis = {k: [doc_agent._cite(b) for b in v] for k, v in doc_agent.field_basis(it["doc"] or "").items()}
        return ok({"room_title": room["title"], "title": it["title"], "shared": it["shared"], "doc": it["doc"], "basis": basis,
                   "review": review_of(values) if it["doc"] in catalog.DOCS else None,
                   "values": values, "sources": sources, "others": editing_others() if it["shared"] else [],
                   "meta": st["meta"], "proposals": st["proposals"],
                   "prep": prep_items is not None, "locked": lock_now,
                   "locked_why": prep.locked_why(it) if prep_items is not None else {},
                   "me": me["profile"]["name"]})
    if method != "POST":
        raise Fail(404, "지원하지 않는 주소입니다.")
    if rest == ["share"]:
        if not it["shared"]:
            raise Fail(400, "함께 작성하는 서류만 채팅방에 공유합니다.")
        name, mime, raw = decode_file({"mime": "application/pdf", "data": data.get("pdf"),
                                       "name": it["title"] + ".pdf"}, {"application/pdf": ".pdf"})
        previous = next((s for s in store.doc_states(rid) if s["item"] == key and s["company_id"] == ""), None)
        version = (previous["extra"].get("version", 0) if previous else 0) + 1
        name = f"{it['title']}_v{version}.pdf"
        fid = store.add_file(rid, name, mime, raw, me["id"])
        store.set_doc(rid, key, "", "shared", {"file_id": fid, "name": name, "by": me["id"],
                                               "by_name": me["profile"]["name"], "version": version})
        store.set_doc(rid, key, "ok:" + me["id"], "ok", {"version": version})
        store.clear_doc(rid, key, me["id"])
        values = clean_values()
        if values is not None:
            store.save_draft(rid, key, "", values)
            store.save_draft(rid, key, me["id"], values)
        store.add_message(rid, me["id"], "file",
                          f"‘{it['title']}’ 작성본(v{version})을 공유했습니다. 내용을 확인한 뒤 서류 탭에서 ‘확인’을 눌러 주세요.", fid)
        announce(rid)
        return ok({"version": version})
    action = data.get("action")
    if action in ("done", "undone") and not it["shared"]:
        if action == "done":
            store.set_doc(rid, key, me["id"], "done")
            store.add_message(rid, "system", "system", f"{me['profile']['name']} · ‘{it['title']}’ 준비 완료")
        else:
            store.clear_doc(rid, key, me["id"])
        announce(rid)
        return ok()
    if action == "editing":
        values = clean_values()
        if values is not None:
            store.save_draft(rid, key, me["id"], values)
        if isinstance(data.get("sources"), dict):
            desk.touch(rid, key, me["id"], {k: v for k, v in data["sources"].items() if isinstance(k, str) and isinstance(v, str)})
        if it["shared"]:
            store.set_doc(rid, key, me["id"], "editing")
        return ok({"others": editing_others() if it["shared"] else []})
    if action == "stop":
        if it["shared"]:
            store.clear_doc(rid, key, me["id"])
        return ok()
    if action == "confirm" and it["shared"]:
        state = next((s for s in store.doc_states(rid) if s["item"] == key and s["company_id"] == ""), None)
        if not state:
            raise Fail(409, "아직 공유된 작성본이 없습니다.")
        store.set_doc(rid, key, "ok:" + me["id"], "ok", {"version": state["extra"]["version"]})
        store.add_message(rid, "system", "system",
                          f"{me['profile']['name']} · ‘{it['title']}’ v{state['extra']['version']} 확인")
        announce(rid)
        return ok()
    raise Fail(400, "처리할 수 없는 요청입니다.")
