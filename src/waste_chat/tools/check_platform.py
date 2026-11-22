"""가입 → 거래 찾기 → 요청·승인 → 거래방 → 서류 공유·확인 흐름을 키 없이 점검한다. python tools/check_platform.py"""
import base64
import os
import sys
import tempfile
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["TIMELYGPT_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import catalog, deal_docs, directory, pdf_writer, platform_api as api, store  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out


def login(name):
    return call("POST", "login", {"login": name, "password": directory.DEMO_PASSWORD})["cookie"]


profile = {"name": "점검배출", "roles": ["emitter"], "address": "경기 수원시", "manager": "담당", "manager_phone": "010",
           "wastes": ["폐합성수지"], "lat": 37.27, "lng": 127.0, "process": "사출 성형"}
call("POST", "signup", {"login": "x", "password": "1", "profile": {}}, expect=400)
a = call("POST", "signup", {"login": "checker", "password": "secret1", "profile": profile})["cookie"]
found = call("POST", "search", {"message": "PE 필름 스크랩이 월 5톤 나옵니다. 재활용업체를 찾아요"}, a)["json"]
assert found["results"] and found["results"][0]["match_role"] == "recycler"
target = found["results"][0]
call("POST", "requests", {"to": target["id"], "search_id": found["id"], "as_role": "processor"}, a)
call("POST", "requests", {"to": target["id"], "search_id": found["id"]}, a, expect=409)
c = login(target["id"].removeprefix("demo_"))
inbox = call("GET", "bootstrap", None, c)["json"]["requests"]
room = call("POST", f"requests/{inbox[0]['id']}/decide", {"accept": True}, c)["json"]["room_id"]
haulers = call("POST", f"rooms/{room}/find", {"want": "hauler"}, a)["json"]["results"]
call("POST", "requests", {"to": haulers[0]["id"], "room_id": room, "as_role": "hauler"}, a)
b = login(haulers[0]["id"].removeprefix("demo_"))
invite = call("GET", "bootstrap", None, b)["json"]["requests"][0]
assert invite["invite"]
call("POST", f"requests/{invite['id']}/decide", {"accept": True}, b)
for who in (a, b, c):
    call("POST", f"rooms/{room}/agree", {"agree": True}, who)
view = call("GET", f"rooms/{room}", {}, a)["json"]
assert view["room"]["docs_open"] and view["docs"]["total"] == 17
call("POST", f"rooms/{room}/docs/intake", {"action": "done"}, c, expect=403)
for who, keys in ((a, ["intake", "verify"]), (b, ["permit_hauler"]), (c, ["permit_processor", "bond"])):
    for key in keys:
        call("POST", f"rooms/{room}/docs/{key}", {"action": "done"}, who)
for key in ("rfq", "form5_hauler", "form5_processor", "contract"):
    doc = catalog.DOCS[deal_docs.BY_KEY[key]["doc"]]
    values = call("GET", f"rooms/{room}/docs/{key}/prefill", None, a)["json"]["values"]
    assert values.get("generator_name") == "점검배출"
    call("POST", f"rooms/{room}/docs/{key}", {"action": "editing", "values": values}, a)
    pdf = base64.b64encode(pdf_writer.render_document(doc, values)).decode()
    call("POST", f"rooms/{room}/docs/{key}/share", {"pdf": pdf, "values": values}, a)
    for who in (b, c):
        api.handle("POST", f"rooms/{room}/docs/{key}", {"action": "confirm"}, who)
view = call("GET", f"rooms/{room}", {}, c)["json"]
assert view["docs"]["current"] == "transfer", view["steps"]["headline"]
assert "다음 단계" in view["messages"][-1]["body"]
call("GET", f"files/{[m for m in view['messages'] if m.get('file')][0]['file']['id']}", None, login("namdo"), expect=404)
print("확인 완료 — 가입, 거래 찾기, 요청·초대, 3자 거래방, 서류 공유·확인, 다음 절차 안내")

# 분류 확인: 표시를 켜면 맨 앞 단계가 생기고, 공정 설명서에 적은 값이 의뢰서·질의서로 이어진다.
call("POST", f"rooms/{room}/flags", {"classify": True}, a)
view = call("GET", f"rooms/{room}", {}, a)["json"]
assert view["docs"]["phases"][0]["id"] == "classify" and view["docs"]["current"] == "classify"
assert [i["key"] for i in view["docs"]["phases"][0]["items"]][-1] == "classify_reply"
note = {"process_steps": "원료 투입 → 사출 → 불량품 분쇄", "raw_materials": "PP 펠릿, 착색제(MSDS 있음)",
        "generation_process": "사출 성형 불량품 분쇄"}
call("POST", f"rooms/{room}/docs/process_note", {"action": "editing", "values": note}, a)
for key in ("analysis_request", "classify_query"):
    out = call("GET", f"rooms/{room}/docs/{key}/prefill", None, a)["json"]
    assert out["values"]["generation_process"] == "사출 성형 불량품 분쇄", out["values"]
    assert "공정 설명서" in out["sources"]["generation_process"]
assert call("GET", f"rooms/{room}/docs/analysis_request/prefill", None, a)["json"]["values"]["raw_materials"].startswith("PP")
call("GET", f"rooms/{room}/docs/classify_query/prefill", None, c)  # 처리업체도 함께 작성
call("GET", f"rooms/{room}/docs/process_note/prefill", None, c, expect=403)
print("확인 완료 — 분류 확인 단계, 서식 간 값 이어받기")
