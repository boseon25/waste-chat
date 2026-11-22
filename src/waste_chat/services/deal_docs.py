"""거래방의 서류 목록. 누가 따로 준비하고 무엇을 함께 작성하는지, 지금 어느 단계인지 계산한다.

근거와 기한은 docs/거래서류안내.md를 따른다. 실제 신고·확인 대상 여부는 각 업체가 확인한다.
"""
import time

from . import store

PHASES = [
    ("classify", "분류 확인", "분류번호가 바뀔 수 있으면 분석기관에 성분 분석을 맡기고 관할 기관에 판단을 묻습니다."),
    ("contract", "계약 전 확인·계약", "수탁자의 허가 범위와 처리능력을 확인하고 서면 계약을 맺습니다."),
    ("filing", "행정 신고·확인", "배출자가 관할 기관에 신고하거나 처리계획 확인을 받습니다."),
    ("transfer", "반출·운반·인수", "한 건을 인계번호로 연결해 각자 올바로에 입력합니다."),
    ("after", "정산·사후관리", "계량·처리 증빙으로 정산하고 장부와 실적보고를 남깁니다."),
]
ROLE_LABEL = {"emitter": "배출업체", "hauler": "운반업체", "processor": "처리업체"}
EDITING_WINDOW = 50  # 초. 이 시간 안에 작성 화면의 신호가 있으면 ‘작성 중’으로 본다.


def item(key, title, phase, roles, kind, note, doc=None, shared=False, author=None, when=None):
    return dict(key=key, title=title, phase=phase, roles=roles, kind=kind, note=note, doc=doc, shared=shared,
                author=author, when=when)


ITEMS = [
    # 분류 확인 (거래방에서 ‘분류 확인 필요’를 켠 경우)
    item("process_note", "폐기물 발생 공정 설명서", "classify", ["emitter"], "작성",
         "공정·원료·성상을 한 번 적어 두면 분석 의뢰서와 분류 질의서에 그대로 이어집니다.", doc="process_note",
         when=lambda f: f.get("classify")),
    item("classify_files", "사업자등록증·원료 MSDS·성상 사진", "classify", ["emitter"], "발급본",
         "회사가 가진 자료를 거래방 채팅에 올려 둡니다. 분석 의뢰와 질의에 함께 첨부합니다.",
         when=lambda f: f.get("classify")),
    item("analysis_request", "폐기물 성분 분석 의뢰서(공통)", "classify", ["emitter"], "작성",
         "분석기관 고유 양식이 있으면 이 내용을 옮겨 적습니다.", doc="analysis_request",
         when=lambda f: f.get("classify")),
    item("sampling_record", "시료 채취 기록부", "classify", ["emitter"], "작성",
         "언제·어디서·어떻게 채취했는지 남기고 시료와 함께 제출합니다.", doc="sampling_record",
         when=lambda f: f.get("classify")),
    item("lab_result", "폐기물 분석결과서 수령", "classify", ["emitter"], "발급본",
         "분석기관이 발급한 결과서를 채팅에 올립니다. 지정폐기물 처리계획 확인에도 같은 결과서를 씁니다.",
         when=lambda f: f.get("classify")),
    item("classify_query", "폐기물 분류 질의서", "classify", ["emitter", "hauler", "processor"], "작성",
         "배출업체가 작성하고, 운반·처리업체는 처리 경로와 반입 기준을 확인해 보탭니다. 관할 기관에 공문·민원으로 냅니다.",
         doc="classify_query", shared=True, author="emitter", when=lambda f: f.get("classify")),
    item("classify_reply", "기관 회신 수령·분류번호 반영", "classify", ["emitter"], "확인",
         "회신을 채팅에 올리고, 확정된 분류번호를 계약서·신고서·올바로 입력에 똑같이 씁니다.",
         when=lambda f: f.get("classify")),
    # 계약 전 확인·계약
    item("intake", "폐기물 기초 정보 입력서", "contract", ["emitter"], "작성",
         "발생 공정·성상·예상 물량을 정리합니다. 내부 자료이며 다른 업체에는 완료 여부만 보입니다.", doc="intake"),
    item("rfq", "반입·운반 견적 문의서", "contract", ["emitter", "hauler", "processor"], "작성",
         "배출업체가 작성해 공유하고, 운반·처리업체가 반입 기준과 견적을 회신합니다.", doc="rfq", shared=True, author="emitter"),
    item("permit_hauler", "수집·운반업 허가증 사본", "contract", ["hauler"], "발급본",
         "관계기관이 발급한 허가증 사본을 채팅에 올려 배출업체에 제공합니다. 빈 서식으로 대신할 수 없습니다."),
    item("permit_processor", "처분·재활용업 허가증(신고증명서) 사본", "contract", ["processor"], "발급본",
         "영업대상 폐기물·처리 방법·시설이 이번 거래와 맞는지 확인할 수 있도록 발급본 사본을 제공합니다."),
    item("bond", "방치폐기물 처리이행보증 증빙 사본", "contract", ["processor"], "발급본",
         "규칙 제16조의12 적용 거래에서 배출업체가 함께 받는 자료입니다."),
    item("form5_hauler", "제5호 수탁처리능력 확인서 · 운반용", "contract", ["emitter", "hauler"], "작성",
         "배출업체가 ①~⑥을 적고 운반업체가 자기 칸과 서명을 채웁니다. 앞으로 받을 수 있다는 확인서입니다.",
         doc="form5_b", shared=True, author="emitter"),
    item("form5_processor", "제5호 수탁처리능력 확인서 · 처리용", "contract", ["emitter", "processor"], "작성",
         "배출업체가 ①~⑥을 적고 처리업체가 허가·시설·능력을 확인해 채웁니다.", doc="form5_c", shared=True, author="emitter"),
    item("verify", "수탁자 허가 범위·적법성 대조", "contract", ["emitter"], "확인",
         "받은 허가증·보증 증빙과 제5호를 이번 폐기물·시설과 대조합니다. 폐기물적정처리추진센터 공개자료도 확인하세요."),
    item("contract", "수집·운반 및 처리 위·수탁계약서", "contract", ["emitter", "hauler", "processor"], "작성",
         "종류·수량·성상, 계약기간, 출발·도착지, 처리 방법, 운반단가와 처리단가를 구분해 적습니다. 서명본은 3년 보관.",
         doc="contract", shared=True),
    # 행정 신고·확인
    item("form6", "제6호 사업장폐기물배출자 신고서", "filing", ["emitter"], "작성",
         "일반 계속사업 신고 대상이면 개시·발생일부터 1개월 이내 관할 시·군·구에 신고합니다.", doc="form6",
         when=lambda f: f.get("business") and not f.get("construction")),
    item("analysis", "제13호 폐기물분석결과서", "filing", ["emitter"], "발급본",
         "지정폐기물 처리계획 확인에는 폐기물분석전문기관이 작성한 결과서가 필요합니다.", doc="analysis",
         when=lambda f: f.get("designated") and not f.get("classify")),
    item("hazard", "제14호의4 폐기물 유해성 정보자료", "filing", ["emitter"], "작성",
         "대상 폐기물이면 첫 인계 전에 수탁자에게 제공합니다.", doc="hazard", when=lambda f: f.get("designated")),
    item("designated_acceptance", "제5호 폐기물 수탁확인서 · 지정 처리계획", "filing", ["emitter", "processor"], "작성",
         "처리업체가 수탁 예정 사실을 확인합니다. 처리계획서에 첨부합니다.", doc="designated_acceptance", shared=True,
         author="processor", when=lambda f: f.get("designated")),
    item("dplan", "제12호 지정폐기물 처리계획서", "filing", ["emitter"], "작성",
         "확인 대상 지정폐기물은 처리 전에 시·도 또는 지방환경관서의 확인을 받습니다.", doc="dplan",
         when=lambda f: f.get("designated")),
    item("cplan", "건설 제7호 건설폐기물 처리계획서", "filing", ["emitter"], "작성",
         "건설폐기물법 적용 공사는 착공일까지 관할청에 신고합니다.", doc="cplan", when=lambda f: f.get("construction")),
    # 반출·운반·인수
    item("allbaro_emitter", "올바로 인계 예약·확정 입력", "transfer", ["emitter"], "시스템 입력",
         "운반자에게 넘기기 전에 종류·양을 입력하고 인계번호를 전달합니다. 예약했다면 처리자 인수 후 2일 이내 확정."),
    item("allbaro_hauler", "올바로 인수·운반 입력", "transfer", ["hauler"], "시스템 입력",
         "배출자에게서 인수한 날부터 2일 이내에 전달받은 인계번호로 입력합니다."),
    item("allbaro_processor", "올바로 인수·처리 결과 입력", "transfer", ["processor"], "시스템 입력",
         "인수한 날부터 2일 이내 실제 인수량을, 처리 후 2일 이내 처리량·처리일자를 입력합니다."),
    item("weigh", "계량표·처리확인서 제출", "transfer", ["processor"], "발급본",
         "계근 결과와 처리 확인 자료를 배출업체에 제출합니다. 초안으로 발급본을 대신할 수 없습니다."),
    # 정산·사후관리
    item("settlement", "반출 1건 계량·정산 대조표", "after", ["emitter", "hauler", "processor"], "작성",
         "이번 차량의 실계량·인수량·처리 회신을 계약 조건과 대조합니다.", doc="settlement", shared=True),
    item("bills", "거래명세·세금계산서 발행", "after", ["hauler", "processor"], "발급본",
         "계약에서 정한 시점에 증빙과 함께 청구합니다."),
    item("ledger", "폐기물 관리대장 기록", "after", ["emitter"], "작성",
         "발생·배출·처리 상황을 기록합니다. 마지막 기록일부터 3년 보존.", doc="ledger"),
    item("annual", "배출·처리 실적보고", "after", ["emitter"], "시스템 입력",
         "신고·확인 대상 배출자는 보통 다음 해 2월 말일까지 전산 제출합니다."),
]
BY_KEY = {i["key"]: i for i in ITEMS}


def room_items(room, members):
    """이 거래방에 해당하는 항목과 참여 업체."""
    by_role = {}
    for m in members:
        by_role.setdefault(m["role"], []).append(m["company_id"])
    flags = room["flags"]
    found = []
    for it in ITEMS:
        if it["when"] and not it["when"](flags):
            continue
        people = [cid for role in it["roles"] for cid in by_role.get(role, [])]
        if not people:
            continue
        if it["shared"] and len(set(people)) < 2:
            continue  # 상대가 아직 거래방에 없다.
        found.append((it, list(dict.fromkeys(people))))
    return found


def view(room, members, names, me_id):
    """화면에 보여줄 서류 상태."""
    states = {}
    for s in store.doc_states(room["id"]):
        states.setdefault(s["item"], {})[s["company_id"]] = s
    now = time.time()
    phases = {pid: {"id": pid, "label": label, "about": about, "items": []} for pid, label, about in PHASES}
    for it, people in room_items(room, members):
        st = states.get(it["key"], {})
        row = {"key": it["key"], "title": it["title"], "kind": it["kind"], "note": it["note"], "doc": it["doc"],
               "shared": it["shared"], "mine": me_id in people}
        if it["shared"]:
            file = st.get("", {})
            editing = [{"id": cid, "name": names.get(cid, "")} for cid, s in st.items()
                       if cid and s["status"] == "editing" and now - s["updated"] < EDITING_WINDOW]
            parties = [{"id": cid, "name": names.get(cid, ""),
                        "confirmed": st.get("ok:" + cid, {}).get("extra", {}).get("version") == file.get("extra", {}).get("version")
                        and bool(file)} for cid in people]
            author_role = it["author"]
            row.update(parties=parties, editing=editing,
                       author=ROLE_LABEL.get(author_role, "참여 업체 누구나"),
                       file=({**file["extra"], "at": file["updated"]} if file else None),
                       done=bool(file) and all(p["confirmed"] for p in parties))
        else:
            owners = [{"id": cid, "name": names.get(cid, ""), "done": st.get(cid, {}).get("status") == "done"}
                      for cid in people]
            row.update(owners=owners, done=all(o["done"] for o in owners),
                       my_done=next((o["done"] for o in owners if o["id"] == me_id), None))
        phases[it["phase"]]["items"].append(row)
    ordered = [p for p in phases.values() if p["items"]]
    for p in ordered:
        p["done"] = sum(1 for i in p["items"] if i["done"])
        p["total"] = len(p["items"])
    current = next((p["id"] for p in ordered if p["done"] < p["total"]), None)
    mine = [i for p in ordered for i in p["items"] if i["mine"]]
    my_open = [i for i in mine if not (i["done"] if i["shared"] else i["my_done"])]
    return {"phases": ordered, "current": current, "flags": room["flags"],
            "total": sum(p["total"] for p in ordered), "done": sum(p["done"] for p in ordered),
            "mine_total": len(mine), "mine_open": len(my_open)}


def remaining(phase, me_id):
    """아직 끝나지 않은 항목을 ‘누가 무엇을’ 형태로 적는다."""
    lines = []
    for i in phase["items"]:
        if i["done"]:
            continue
        if i["shared"]:
            if not i["file"]:
                who = "작성 시작 전 (" + i["author"] + ")"
            else:
                waiting = [p["name"] for p in i["parties"] if not p["confirmed"]]
                who = "확인 대기: " + ", ".join(waiting)
            if i["editing"]:
                who = ", ".join(e["name"] for e in i["editing"]) + " 작성 중"
        else:
            who = ", ".join(o["name"] for o in i["owners"] if not o["done"])
        lines.append({"title": i["title"], "who": who, "mine": i["mine"], "shared": i["shared"]})
    return lines


def steps(doc_view, me_id):
    """남은 절차 안내. 현재 단계와 이후 단계를 구분한다."""
    out = []
    current = doc_view["current"]
    seen_current = False
    for p in doc_view["phases"]:
        state = "done" if p["done"] == p["total"] else "current" if p["id"] == current else "upcoming"
        seen_current = seen_current or state == "current"
        out.append({"id": p["id"], "label": p["label"], "about": p["about"], "state": state,
                    "done": p["done"], "total": p["total"], "remaining": remaining(p, me_id)})
    if current is None:
        headline = "등록된 서류와 절차를 모두 마쳤습니다. 서명본·증빙은 각 업체가 법정 기간 동안 보관하세요."
    else:
        now = next(s for s in out if s["id"] == current)
        after = [s["label"] for s in out if s["state"] == "upcoming"]
        headline = (f"지금은 ‘{now['label']}’ 단계입니다. 남은 항목 {now['total'] - now['done']}개를 마치면 "
                    + (f"‘{after[0]}’ 단계로 넘어갑니다." if after else "모든 절차가 끝납니다."))
    return {"headline": headline, "phases": out}


def guide_message(room, members, names, only=None):
    """거래 합의 직후 채팅에 올리는 서류 안내문. only가 있으면 그 업체 몫만 적는다."""
    items = room_items(room, members)
    lines = []
    for m in members:
        if only and m["company_id"] != only:
            continue
        own = [it["title"] + f" ({it['kind']})" for it, people in items if not it["shared"] and m["company_id"] in people]
        if own:
            lines.append(f"■ {names[m['company_id']]} ({ROLE_LABEL[m['role']]}) — 따로 준비\n" + "\n".join("  · " + t for t in own))
    shared = [it["title"] + " — " + " · ".join(names[c] for c in people) for it, people in items
              if it["shared"] and (not only or only in people)]
    if shared:
        lines.append("■ 함께 작성하는 서류\n" + "\n".join("  · " + t for t in shared))
    return "\n\n".join(lines)


def prefill(room, members, companies, item_key):
    """가입 때 입력한 업체 정보와 거래 조건에서 서식 칸에 넣을 값을 만든다."""
    found = prefill_layer(room, members, companies)
    return {k: x["value"] for k, x in found.items()}, {k: x["source"] for k, x in found.items()}


def prefill_layer(room, members, companies):
    """{칸: {value, source, origin}}. origin은 값이 나온 자리(바뀌었는지 다시 볼 때 쓴다).
    profile:<업체id>:<항목> / need:<항목>"""
    out = {}

    def put(key, value, source, origin):
        if isinstance(value, str) and value.strip() and key not in out:
            out[key] = {"value": value.strip(), "source": source, "origin": origin}

    prefix = {"emitter": "generator", "hauler": "hauler", "processor": "receiver"}
    for m in members:
        cid = m["company_id"]
        p = companies[cid]["profile"]
        pre, src = prefix[m["role"]], p.get("name", "") + " 업체 정보"
        for field, name in PROFILE_FIELDS:
            put(f"{pre}_{field}", p.get(name, ""), src, f"profile:{cid}:{name}")
        if p.get("permit_wastes"):
            put(f"{pre}_permit_waste", ", ".join(p["permit_wastes"]), src, f"profile:{cid}:permit_wastes")
        if m["role"] == "emitter":
            for key, name in (("contact_name", "manager"), ("contact_phone", "manager_phone"),
                              ("generation_process", "process"), ("pickup_location", "address")):
                put(key, p.get(name, ""), src, f"profile:{cid}:{name}")
        if m["role"] == "processor":
            for key, name in (("rfq_recipient", "name"), ("rfq_destination", "address"), ("contract_destination", "address")):
                put(key, p.get(name, ""), src, f"profile:{cid}:{name}")
    for key, name in (("waste_name", "wastes"), ("quantity_period", "quantity"), ("contract_quantity", "quantity"),
                      ("contamination_detail", "quality")):
        put(key, need_value(room["need"], name), "거래 요청 내용", f"need:{name}")
    return out


PROFILE_FIELDS = (("name", "name"), ("registration", "reg_no"), ("representative", "ceo"), ("address", "address"),
                  ("phone", "phone"), ("industry", "industry"), ("permit_number", "permit_no"),
                  ("permit_authority", "permit_authority"), ("method", "methods"), ("facility_capacity", "capacity"))


def need_value(need, name):
    v = need.get(name)
    return " · ".join(v) if isinstance(v, list) else (v or "")
