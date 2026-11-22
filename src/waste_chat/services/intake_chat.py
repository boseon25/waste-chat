"""입력서로 시작한 거래 찾기의 AI 대화 — 검색 조건은 바꾸지 않고, 모르는 것을 묻고 답한다.

근거: 입력서 사실, 법령 판단 결과(결론·할 일·서류·업체), 1차 후보, 법령 원문과 실무 안내(lawbase.tiered).
답에 붙이는 인용은 이번에 찾은 조각 안에서만 고른다(없는 조문을 지어내지 않음). 조건을 바꾸려면 '사실 고치기'로 안내한다.
"""
import json
import re

from . import advisor, directory, engine, envfile, intake_flow, judgment_view, lawbase, store, tables

FACT_PREFER = ("WMA_RULE:B4", "WMA_DEC:B1", "WMA_RULE:16의13", "WMA:13의2", "WMA_RULE:18")


def _context(search):
    view = engine.view(search["id"])
    facts = view.get("facts") or {}
    plain = judgment_view.build(view.get("result"), facts) if view.get("result") else None
    lines = ["[입력서 사실] " + "; ".join(f"{engine.FACT_LABELS.get(k, k)}: {v}" for k, v in facts.items() if v)]
    job = view.get("job") or {}
    if job.get("status") == "running":
        lines.append("[법령 판단] 진행 중(30~90초)")
    elif plain:
        lines.append(f"[법령 판단 결론] {plain['verdict']['title']} — {plain['verdict']['why']} ({plain['basis_line']})")
        if plain["todo"]:
            lines.append("[확인할 것] " + "; ".join(f"{t['label']}: {t['ask']}" for t in plain["todo"]))
        if plain["docs"]:
            lines.append("[필요한 서류] " + "; ".join(f"{d['title']}({d['status']}, {d.get('who', '')})" for d in plain["docs"]))
        for rt in plain["routes"][:1]:
            lines.append(f"[법령 기준 업체] {rt['title']}: " + ", ".join(f"{p['name']}({p['region']}, {p['km']}km)" for p in rt["partners"][:5]))
    else:
        lines.append("[법령 판단] 아직 없음(분류번호를 정하면 실행)")
    if search.get("results"):
        lines.append("[1차 후보(가입 업체)] " + ", ".join(c["name"] for c in search["results"][:6]))
    return "\n".join(lines), facts


ROLE_WORDS = [("hauler", r"운반|운송"), ("recycler", r"재활용|가공|재생"), ("disposer", r"소각|처분|매립")]


def find_companies(me, search, message):
    """채팅에서 요청한 업체 → 카드. 입력서 기준 추천과 따로, 대화에만 붙인다.
    - 업체 이름이 질문에 나오면 그 가입 업체
    - '운반업체 찾아줘'처럼 종류를 말하면 그 종류의 가입 업체와 공공자료 업체(이번 폐기물 기준, 배출 현장에서 가까운 순)"""
    wastes = set(search["need"].get("wastes") or [])
    here = intake_flow.at_site(me, search["need"])
    msg = tables.norm_name(message)
    asked = {r["to_id"]: r["status"] for r in store.requests_for(me["id"])
             if r["from_id"] == me["id"] and r["status"] != "declined" and r["need"].get("_search") == search["id"]}
    wants_find = bool(re.search(r"찾|알려|보여|추천|있(어|나)", message))
    roles = [r for r, pat in ROLE_WORDS if re.search(pat, message)] if wants_find else []
    cards, seen = [], set()

    def member(c, how):
        p = c["profile"]
        handles = set(directory.handled_wastes(p))
        fit = bool(wastes & handles)
        cards.append({"kind": "member", "id": c["id"], "name": p.get("name", ""), "roles": p.get("roles", []),
                      "match_role": next((r for r in roles if r in p.get("roles", [])), (p.get("roles") or [""])[0]),
                      "address": p.get("address", ""), "distance_km": directory.distance_km(here["profile"], p),
                      "handles": sorted(handles), "fit": fit, "demo": bool(c["demo"]), "request": asked.get(c["id"], ""),
                      "linked": bool(p.get("public_key")), "how": how,
                      "note": ("이번 폐기물(" + ", ".join(sorted(wastes)) + ")을 영업대상으로 등록함") if fit
                              else ("등록된 영업대상에 이번 폐기물(" + ", ".join(sorted(wastes)) + ")이 없음 · 허가증으로 확인 필요")})
        seen.add(c["id"])

    for c in store.companies():  # 이름으로
        n = tables.norm_name(c["profile"].get("name"))
        if c["id"] != me["id"] and len(n) >= 2 and n in msg:
            member(c, "이름")
    if cards:  # 이름으로 찾은 업체가 있으면 그 업체만(이름 속 '운반' 같은 말로 종류 검색까지 하지 않는다)
        return cards
    for role in roles:  # 종류로: 가입 업체(이번 폐기물 취급) → 공공자료 업체
        for c in directory.match({**search["need"], "want": [role]}, here, limit=12):
            if role in c["roles"] and c["id"] not in seen and len(cards) < 8:
                member(store.company(c["id"]), "종류")
        for x in tables.search_partners(list(wastes), [role], (here["profile"].get("lat"), here["profile"].get("lng")), limit=4):
            if len(cards) >= 10:
                break
            cards.append({"kind": "public", "id": x["id"], "name": re.sub(r"_+$", "", x["name"]), "roles": [role], "match_role": role,
                          "address": x["address"], "distance_km": x["distance_km"], "phone": x.get("phone", ""),
                          "license": x.get("license", ""), "handles": x["kinds"][:4], "fit": True, "how": "종류",
                          "note": "공공자료 업체 · 앱 미가입(전화 문의) · 허가 품목 기준"})
    return cards


def _cards_text(cards):
    return "\n".join(f"- {c['name']}({'가입' if c['kind'] == 'member' else '공공자료'}, {directory.ROLES.get(c['match_role'], '')}): {c['note']}"
                     + (f", 약 {c['distance_km']:g}km" if c.get("distance_km") is not None else "") for c in cards)


def answer(me, search, message):
    """(답, 인용 목록). 검색 조건(need·results)은 건드리지 않는다."""
    ctx, facts = _context(search)
    cards = find_companies(me, search, message)
    if cards:
        ctx += "\n[요청한 업체 조회 결과 — 화면에 카드로 함께 보여 줌]\n" + _cards_text(cards)
    query = f"{message} {facts.get('name', '')}"
    hits = lawbase.tiered(query, prefer=FACT_PREFER, k_law=5, k_practice=3)
    if not advisor.ai_ready():
        top = hits[0] if hits else None
        reply = ("AI 연결이 없어 관련 원문만 찾아 드립니다." + (f" 가장 가까운 근거는 ‘{top['label']}’입니다." if top else "")
                 + " 입력서 내용을 바꾸려면 ‘법령 판단’ 탭의 ‘사실 고치기’를 쓰세요.")
        return reply + (f" 요청하신 업체 {len(cards)}곳을 아래에 보여 드립니다." if cards else ""), [lawbase.public(h, 700) for h in hits[:2]], cards
    sources = "\n".join(f"[{i + 1}] ({h['kind']}) {h['label']}: {lawbase.brief(h, 600)}" for i, h in enumerate(hits))
    history = [{"role": "user" if t["role"] == "user" else "assistant", "content": t["text"][:800]}
               for t in search.get("turns", [])[-8:] if t["role"] in ("user", "assistant")]
    system = (
        "폐기물 배출업체 담당자가 기초 입력서로 처리 업체를 찾는 중에 묻는 질문에 답합니다.\n"
        "규칙: 1) [상황]의 입력서 사실·법령 판단 결과를 먼저 보고 이 건에 맞게 답합니다. 2) 법령 근거는 [자료]에서만 번호로 인용하고, "
        "자료에 없는 조문·숫자를 지어내지 않습니다. 실무 안내는 법적 구속력이 없다고 밝힙니다. 3) 모르면 모른다고 하고 누구에게(처리업체·관할 기관·분석기관) 확인할지 알려 줍니다. "
        "마크다운 굵게(**)·제목(#) 표시는 쓰지 않습니다. 4) 검색 조건이나 입력서 내용을 바꾸자는 요청이면 '법령 판단 탭의 사실 고치기'로 바꿀 수 있다고 안내합니다. 5) 짧고 쉬운 존댓말로 답합니다.\n"
        "JSON: {\"reply\": 답, \"cite\": [인용한 자료 번호]}\n"
        f"[상황]\n{ctx}\n[자료]\n{sources or '(찾은 자료 없음)'}")
    out = advisor._call([{"role": "system", "content": system}, *history, {"role": "user", "content": message}],
                        True, envfile.agent_model())
    reply = str(out.get("reply") or "").strip() or "답을 만들지 못했습니다. 질문을 조금 바꿔 다시 물어봐 주세요."
    reply = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), reply)  # 화면은 마크다운을 그리지 않는다
    order = {}  # 자료 번호 → 화면에 보이는 인용 번호(1부터)
    for n in [*out.get("cite", []), *re.findall(r"\[(\d+)\]", reply)]:
        try:
            i = int(n) - 1
        except (ValueError, TypeError):
            continue
        if 0 <= i < len(hits) and i not in order and len(order) < 4:
            order[i] = len(order) + 1
    reply = re.sub(r"\[(\d+)\]", lambda m: f"[{order[int(m.group(1)) - 1]}]" if int(m.group(1)) - 1 in order else "", reply)
    return reply, [lawbase.public(hits[i], 700) for i in sorted(order, key=order.get)], cards
