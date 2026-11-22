"""매칭 전 서류 준비 점검(모델 없이): 판단 결과 → 미리 쓸 서류 목록, 상대 업체 칸 잠금, 작성 저장, 공유 막기, 거래방으로 이어받기.
python tools/check_prep.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["TIMELYGPT_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import directory, platform_api as api, store  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out["json"]


profile = {"name": "준비배출", "roles": ["emitter"], "address": "경기 화성시 향남읍", "manager": "담당", "manager_phone": "010",
           "wastes": ["폐합성수지"], "lat": 37.13, "lng": 126.9, "process": "사출 성형", "reg_no": "123-45-67890"}
a = api.handle("POST", "signup", {"login": "prepcheck", "password": "secret1", "profile": profile}, None)["cookie"]
facts = {"name": "사출 불량품과 런너", "process": "사출 성형 불량품", "form": "마른 고체 조각", "components": "PP 70%, ABS 30%",
         "contamination": "깨끗", "hazard": "아니오", "monthly_ton": "5", "address": "경기 화성시 향남읍", "code": "51-03-01"}
s = call("POST", "intake/start", {"facts": facts, "want": ["recycler"]}, a)
sid = s["id"]
call("GET", f"case/{sid}/docs/contract/prefill", None, a, 404)          # 판단 전에는 미리 쓸 목록이 없다(견적만)
for _ in range(120):
    v = call("GET", f"case/{sid}", None, a)
    if v["job"].get("status") != "running":
        break
    time.sleep(1)
assert v["job"]["status"] == "done" and v["prep"], v.get("prep")
keys = [x["key"] for x in v["prep"]]
assert "rfq" in keys and "contract" in keys, keys
assert any(d.get("prep_key") == "contract" for d in v["plain"]["docs"])

pre = call("GET", f"case/{sid}/docs/contract/prefill", None, a)
assert pre["prep"] and pre["locked"] and all(k.startswith(("hauler_", "receiver_")) or k in ("transport_price", "processing_price", "payer", "contract_period", "contract_destination", "treatment_method") for k in pre["locked"])
assert pre["locked_why"]["contract_period"] == "agreed" and pre["locked_why"]["treatment_method"] == "their"
assert not any(f["key"] in pre["locked"] for f in pre["review"]["findings"]), pre["review"]["findings"]  # 잠근 칸은 검토에서 빼고 센다
assert pre["values"].get("generator_name") == "준비배출" and pre["values"].get("waste_classification") == "51-03-01", pre["values"]
assert not any(k.startswith("receiver_") for k in pre["values"])
vals = {**pre["values"], "precautions": "이형제가 묻은 런너는 따로 담음"}
call("POST", f"case/{sid}/docs/contract", {"action": "editing", "values": vals}, a)
call("POST", f"case/{sid}/docs/contract/review", {"values": vals}, a)
call("POST", f"case/{sid}/docs/contract/share", {"pdf": "", "values": vals}, a, 409)
call("POST", f"case/{sid}/docs/contract", {"action": "done"}, a, 409)
v = call("GET", f"case/{sid}", None, a)
assert next(x for x in v["prep"] if x["key"] == "contract")["filled"] > 0

# 가입 업체에 거래 요청 → 승인 → 거래방에 준비한 작성본이 들어간다
target = [r for r in s["results"] if not r.get("public")][0]
call("POST", "requests", {"to": target["id"], "search_id": sid, "as_role": "processor"}, a)
c = api.handle("POST", "login", {"login": target["id"].removeprefix("demo_"), "password": directory.DEMO_PASSWORD}, None)["cookie"]
req = call("GET", "bootstrap", None, c)["requests"][0]
room = call("POST", f"requests/{req['id']}/decide", {"accept": True}, c)["room_id"]
assert store.draft(room, "contract", a and store.session_company(a)["id"]).get("precautions") == "이형제가 묻은 런너는 따로 담음"
assert any("준비한 서류" in m["body"] for m in store.messages(room))
for who in (a, c):
    call("POST", f"rooms/{room}/agree", {"agree": True}, who)
pre = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)
assert pre["values"].get("precautions") == "이형제가 묻은 런너는 따로 담음" and not pre["prep"]
print("확인 완료 — 판단 결과로 미리 쓸 서류, 상대 칸 잠금, 작성 저장·검토, 공유·완료 막기, 거래방으로 이어받기")
