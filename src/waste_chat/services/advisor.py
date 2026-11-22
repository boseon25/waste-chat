"""AI 안내. 거래 상대 찾기와 거래방 도우미. 키가 없거나 호출이 실패하면 규칙 기반 안내로 답한다."""
import json

from . import deal_docs, directory, envfile, timely

KNOWLEDGE = """[국내 사업장폐기물 위탁처리 요약]
- 순서: 배출자가 물질(발생공정·성상·물량) 확인 → 허용된 처리방법 확인 → 그 품목·방법을 허가 범위에서 받을 수 있는 시설 확인 → 운반 주체 확인.
- 계약 전: 운반자·처리자는 발급된 허가증(신고증명서) 사본을 제공. 규칙 제16조의12 적용 배출자는 별지 제5호 수탁처리능력 확인서와 허가·방치폐기물 처리이행보증 증빙 사본을 받는다. 제5호는 '앞으로 받을 수 있다'는 확인서다.
- 계약: 서면 계약에 종류·수량·성상·주의사항, 계약기간, 출발·도착지, 처리시설·방법, 운반단가와 처리단가를 구분해 적는다. 계약서는 체결일부터 3년 보관.
- 행정 신고: 일반 계속사업 신고 대상은 별지 제6호로 개시·발생일부터 1개월 이내. 지정폐기물 처리계획 확인 대상은 제12호 처리계획서, 분석전문기관의 제13호 분석결과서, 제5호 수탁확인서를 준비해 처리 전에 확인받는다. 건설폐기물법 적용 공사는 건설 제7호를 착공일까지.
- 반출: 배출자는 인계 전 올바로에 예약·확정 입력하고 인계번호를 전달. 운반자는 인수 후 2일 이내, 처리자는 인수 후 2일 이내 인수량, 처리 후 2일 이내 처리량 입력.
- 처리 후: 계근표·처리확인서·거래명세·세금계산서로 정산. 대상 배출자는 1개월마다 처리과정 확인. 관리대장은 마지막 기록일부터 3년 보존, 실적보고는 보통 다음 해 2월 말일까지.
- 분류 확인: 분류번호가 바뀔 수 있으면 ① 발생 공정 설명서(공정·원료·성상) ② 사업자등록증·원료 MSDS·성상 사진 ③ 분석 의뢰서와 시료 채취 기록부를 갖춰 폐기물분석전문기관(한국환경공단, 시·도 보건환경연구원, 지정 민간기관)에 성분 분석을 맡긴다. ④ 분석결과서를 받으면 분류 질의서에 요약해 관할 시·군·구(지정폐기물은 유역·지방환경청)에 공문이나 국민신문고로 묻는다. ⑤ 회신으로 확정된 분류번호를 계약서·신고서·올바로에 똑같이 쓴다. 질의서·의뢰서는 법정 서식이 없어 이 앱의 공통 양식으로 쓰고, 기관 고유 양식이 있으면 옮겨 적는다.
- 허가증·신고증명서·분석결과서는 기관이 발급한다. 빈 서식으로 대신 만들 수 없다. 허가 여부, 법령 적용 여부, 분류번호를 추정해 단정하지 않는다."""


PROBLEM = {"text": ""}  # 키가 거절되면 다시 시작할 때까지 기본 안내로만 답한다.


def ai_ready():
    return bool(envfile.api_key()) and not PROBLEM["text"]


def _call(messages, want_json=False, model=None):
    try:
        return timely.chat(messages, envfile.api_key(), model or envfile.model(), want_json)
    except ValueError as exc:
        if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
            PROBLEM["text"] = ".env의 API 키를 Timely가 받아들이지 않았습니다. 키를 새로 발급해 .env에 넣고 서버를 다시 시작하세요."
        raise


# ── 거래 상대 찾기 ─────────────────────────────────────────────────────
def _rule_reply(need, results, me):
    if not need["wastes"]:
        return ("어떤 폐기물을 거래하려는지 품목을 알려 주세요. 예: “PE 포장필름 스크랩이 월 5톤 정도 나옵니다. "
                "오염은 거의 없고, 재생 원료로 쓸 재활용업체를 찾고 있어요.”")
    want = " · ".join(directory.ROLES[w] for w in need["want"])
    head = f"{' · '.join(need['wastes'])}" + (f", {need['quantity']}" if need["quantity"] else "") + f" 조건으로 {want}를 찾았습니다."
    if not results:
        return head + " 지금 등록된 업체 중에는 조건에 맞는 곳이 없습니다. 품목을 넓히거나 찾는 상대(재활용·처분·운반)를 바꿔 다시 말씀해 주세요."
    top = results[0]
    body = (f" 후보 {len(results)}곳을 오른쪽 지도에 표시했습니다. 가장 가까운 조건의 후보는 {top['name']}"
            + (f"(약 {top['distance_km']:g} km)" if top.get("distance_km") is not None else "") + "입니다.")
    ask = []
    if not need["quantity"]:
        ask.append("물량과 배출 주기")
    if not need["quality"]:
        ask.append("오염·혼합 정도")
    tail = (" " + "와 ".join(ask) + "를 알려 주시면 요청서에 함께 담겠습니다.") if ask else ""
    return (head + body + tail + " 업체의 허가 범위와 실제 반입 가능 여부는 요청을 보낸 뒤 상대 업체에 직접 확인하세요.")


def search_turn(me, search, message):
    """대화 한 차례를 처리해 (답변, 조건, 후보 목록, 사용한 방식)을 돌려준다."""
    roles = me["profile"].get("roles") or []
    need = directory.read_need(message, search["need"], roles)
    note = ""
    if ai_ready():
        try:
            everyone = directory.match({"want": [], "wastes": []}, me, limit=60)
            listing = [{"id": c["id"], "name": c["name"], "roles": [directory.ROLES[r] for r in c["roles"]],
                        "wastes": c["wastes"], "methods": c["methods"], "capacity": c["capacity"] or c["vehicles"],
                        "address": c["address"], "km": c.get("distance_km")} for c in everyone]
            system = (
                "당신은 폐기물 순환 거래 플랫폼의 매칭 담당자입니다. 사용자는 "
                f"‘{me['profile'].get('name')}’({', '.join(directory.ROLES[r] for r in roles)}, {me['profile'].get('address', '')}) 소속입니다.\n"
                "대화에서 거래 조건을 파악하고 아래 [업체 목록]에서 조건에 맞는 후보를 고르세요.\n"
                "JSON 객체 하나만 답합니다: {\"reply\": 문자열, \"need\": {\"wastes\": [], \"want\": [], \"quantity\": \"\", \"quality\": \"\", \"detail\": \"\"}, "
                "\"picks\": [{\"id\": \"\", \"why\": \"\"}]}\n"
                f"- need.wastes는 다음 중에서만 고릅니다: {', '.join(directory.WASTES)}\n"
                "- need.want는 emitter(배출업체), hauler(수집·운반), recycler(재활용·가공), disposer(처분) 중에서 고릅니다.\n"
                "- picks는 목록에 있는 id만, 적합한 순서로 최대 8곳. why는 품목·처리 방법·거리 등 고른 이유 한 줄.\n"
                "- reply는 한국어 존댓말 3~5문장, 목록 기호나 마크다운 없이 씁니다. 파악한 조건을 한 번 확인하고, 후보를 지도에 표시했다고 알리고, "
                "물량·주기·오염 정도처럼 빠진 정보가 있으면 한두 가지만 묻습니다.\n"
                "- 허가 범위나 수탁 가능 여부를 단정하지 않습니다. 후보일 뿐이며 요청 후 상대 업체에 직접 확인하도록 안내합니다.\n"
                "- 목록에 없는 업체나 사실을 지어내지 않습니다. 맞는 업체가 없으면 picks를 비우고 그렇게 말합니다.\n"
                f"[현재까지 파악한 조건] {json.dumps(search['need'], ensure_ascii=False)}\n"
                f"[업체 목록] {json.dumps(listing, ensure_ascii=False)}\n" + KNOWLEDGE)
            history = [{"role": "assistant" if t["role"] == "assistant" else "user", "content": t["text"]}
                       for t in search["turns"][-10:]]
            out = _call([{"role": "system", "content": system}, *history, {"role": "user", "content": message}], True)
            ai_need = directory.clean_need(out.get("need"))
            for name in ("wastes", "want", "quantity", "quality"):
                if ai_need[name]:
                    need[name] = ai_need[name]
            by_id = {c["id"]: c for c in everyone}
            picks = []
            for pick in out.get("picks") if isinstance(out.get("picks"), list) else []:
                found = by_id.get(pick.get("id")) if isinstance(pick, dict) else None
                if found and found["id"] not in {p["id"] for p in picks}:
                    wanted = [r for r in need["want"] if r in found["roles"]]
                    picks.append({**found, "why": directory.clean(pick.get("why"), 160) or found.get("why", ""),
                                  "match_role": wanted[0] if wanted else found["roles"][0]})
            results = picks[:8] or directory.match(need, me)
            reply = directory.clean(out.get("reply"), 1500)
            if reply:
                return reply, need, results, "ai"
        except ValueError as exc:
            note = f"\n\n(AI 연결 문제로 기본 검색으로 안내했습니다: {exc})"
    results = directory.match(need, me)
    return _rule_reply(need, results, me) + note, need, results, "rule"


# ── 거래방 도우미 ──────────────────────────────────────────────────────
def _room_context(room, members, names, me_id, doc_view):
    lines = ["[거래방] " + room["title"],
             "[거래 조건] " + "; ".join(f"{s['label']}: {s['value']}" for s in directory.need_summary(room["need"])),
             "[참여 업체] " + ", ".join(f"{names[m['company_id']]}({deal_docs.ROLE_LABEL[m['role']]}"
                                     f"{', 거래 합의' if m['agreed'] else ''})" for m in members),
             f"[질문자] {names[me_id]}"]
    if doc_view:
        for p in doc_view["phases"]:
            lines.append(f"[{p['label']}] {p['done']}/{p['total']} 완료")
            for r in deal_docs.remaining(p, me_id):
                lines.append(f"  - 남음: {r['title']} / {r['who']}" + (" / 질문자 담당" if r["mine"] else ""))
    else:
        lines.append("[서류] 아직 거래 합의 전이라 서류 목록을 열지 않았습니다.")
    return "\n".join(lines)


def _rule_room(message, room, members, names, me_id, doc_view):
    if not doc_view:
        hauler = any(m["role"] == "hauler" for m in members)
        return ("지금은 협의 단계입니다. 채팅에서 품목·물량·반입 기준·단가를 맞춘 뒤 위의 ‘거래 진행에 합의’를 모든 업체가 누르면 "
                "업체별·공통 서류 목록을 열어 드립니다."
                + ("" if hauler else " 운반업체가 따로 필요하면 ‘업체 초대’에서 운반업체를 찾아 요청을 보낼 수 있습니다."))
    step = deal_docs.steps(doc_view, me_id)
    if any(w in message for w in ("다음", "절차", "남은", "남았", "단계", "이후", "순서")):
        lines = [step["headline"]]
        for p in step["phases"]:
            mark = {"done": "완료", "current": "진행 중", "upcoming": "예정"}[p["state"]]
            lines.append(f"\n{p['label']} — {mark} ({p['done']}/{p['total']})\n{p['about']}")
            lines += [f"  · {r['title']} — {r['who']}" for r in p["remaining"]]
        return "\n".join(lines)
    mine = [(p["label"], i) for p in doc_view["phases"] for i in p["items"] if i["mine"]]
    own = [f"  · {i['title']} ({i['kind']}) — {'완료' if i['my_done'] else '준비 필요'}" for _, i in mine if not i["shared"]]
    shared = [f"  · {i['title']} — " + ("완료" if i["done"] else
              (", ".join(e["name"] for e in i["editing"]) + " 작성 중") if i["editing"] else
              "공유됨, 확인 대기" if i["file"] else "작성 전") for _, i in mine if i["shared"]]
    return (f"{names[me_id]}에서 챙길 서류입니다.\n\n따로 준비하는 서류\n" + ("\n".join(own) or "  · 없음")
            + "\n\n함께 작성하는 서류\n" + ("\n".join(shared) or "  · 없음")
            + "\n\n오른쪽 ‘서류’ 탭에서 작성 화면을 열 수 있습니다. 남은 절차가 궁금하면 “다음 절차 알려줘”라고 물어보세요.")


def room_turn(message, history, room, members, names, me_id, doc_view):
    if ai_ready():
        try:
            system = ("당신은 폐기물 위탁처리 거래를 돕는 실무 안내자입니다. 질문한 업체의 입장에서 답합니다. "
                      "아래 [현재 상황]의 서류 상태와 [요약]의 절차만 근거로 삼고, 모르는 것은 확인이 필요하다고 말합니다. "
                      "허가 여부, 법령 적용 대상 여부, 분류번호, 단가를 추정해 단정하지 않습니다. "
                      "한국어 존댓말로 짧고 구체적으로, 누가 무엇을 언제까지 해야 하는지 중심으로 답합니다. 마크다운 표와 굵은 글씨는 쓰지 않습니다.\n"
                      "[현재 상황]\n" + _room_context(room, members, names, me_id, doc_view) + "\n" + KNOWLEDGE)
            turns = [{"role": "assistant" if t["role"] == "assistant" else "user", "content": t["body"]} for t in history[-12:]]
            reply = _call([{"role": "system", "content": system}, *turns, {"role": "user", "content": message}])
            if reply:
                return reply[:4000], "ai"
        except ValueError as exc:
            return (_rule_room(message, room, members, names, me_id, doc_view)
                    + f"\n\n(AI 연결 문제로 기본 안내로 답했습니다: {exc})"), "rule"
    return _rule_room(message, room, members, names, me_id, doc_view), "rule"
