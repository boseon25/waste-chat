"""서류 비서 점검(모델 없이): 진행판·상대 업체 요청·기한, 입력서 원본 나눠 넣기(제안), 수락/거절, 바뀐 값 따라가기, 원본 대조.
python tools/check_desk.py"""
import base64
import os
import sys
import tempfile
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["TIMELYGPT_API_KEY"] = ""
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import directory, platform_api as api, store  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out["json"]


profile = {"name": "점검배출", "roles": ["emitter"], "address": "경기 수원시 권선구", "manager": "담당", "manager_phone": "010",
           "wastes": ["폐합성수지"], "lat": 37.27, "lng": 127.0, "process": "사출 성형", "reg_no": "123-45-67890"}
a = api.handle("POST", "signup", {"login": "deskcheck", "password": "secret1", "profile": profile}, None)["cookie"]
found = call("POST", "search", {"message": "PE 필름 스크랩이 월 5톤 나옵니다. 재활용업체를 찾아요"}, a)
target = [r for r in found["results"] if not r.get("public")][0]
call("POST", "requests", {"to": target["id"], "search_id": found["id"], "as_role": "processor"}, a)
c = api.handle("POST", "login", {"login": target["id"].removeprefix("demo_"), "password": directory.DEMO_PASSWORD}, None)["cookie"]
req = call("GET", "bootstrap", None, c)["requests"][0]
room = call("POST", f"requests/{req['id']}/decide", {"accept": True}, c)["room_id"]
call("POST", f"rooms/{room}/desk", None, a, 409)                       # 합의 전에는 진행판 없음
for who in (a, c):
    call("POST", f"rooms/{room}/agree", {"agree": True}, who)

# 1) 진행판: 서류 수, 상대 업체 몫, 기한
b = call("GET", f"rooms/{room}/desk", None, a)
assert b["docs"] and "챙길 서류" in b["headline"], b["headline"]
assert b["need_from"] and b["need_from"][0]["count"] > 0, b["need_from"]   # 처리업체 칸
assert "발생(첫 반출 예정)일" in b["headline"]
b = call("POST", f"rooms/{room}/desk/start", {"date": "2026-12-10"}, a)
contract = next(x for x in b["docs"] if x["key"] == "contract")
assert contract["deadline"]["date"] == "2026-12-10" and "가장 가까운 기한" in b["headline"]
call("POST", f"rooms/{room}/desk/start", {"date": "12월 10일"}, a, 400)
n = len(store.messages(room))
out = call("POST", f"rooms/{room}/desk/request", {}, a)
assert out["sent"] == 1 and len(store.messages(room)) == n + 1 and out["need_from"][0]["sent"]
# 법령 판단이 요구했는데 목록에 없는 서류 → 표시를 켜라는 안내
store.save_casefile(room, facts={"code": "51-03-01", "name": "PE 필름", "form": "필름 조각", "monthly_ton": "5"})

# 2) 입력서 원본 → 내 서류 모두에 제안(바로 넣지 않음)
from services.paths import PART1  # noqa: E402
blob = (PART1 / "fixtures/intake_records_example.docx").read_bytes()
out = call("POST", f"rooms/{room}/desk/original", {"file": {"name": "입력서.docx", "data": base64.b64encode(blob).decode()}}, a)
assert out["docs"] and out["found"], out
pre = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)
props = pre["proposals"]
assert "waste_name" in props and props["waste_name"]["source"].startswith("올린 원본"), props
assert pre["values"].get("waste_name") != props["waste_name"]["value"]       # 수락 전에는 칸이 그대로

# 3) 수락/거절
r = call("POST", f"rooms/{room}/docs/contract/decide", {"accept": ["waste_name"], "reject": [k for k in props if k != "waste_name"],
                                                       "values": pre["values"]}, a)
assert r["applied"]["waste_name"] == props["waste_name"]["value"] and not r["proposals"], r
pre = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)
assert pre["values"]["waste_name"] == props["waste_name"]["value"] and not pre["proposals"]   # 거절한 값은 다시 제안 안 함
assert pre["meta"]["waste_name"]["conf"] == "high"

# 4) 원본 대조: 원본과 다르게 고치면 검토 경고
vals = {**pre["values"], "waste_name": "다른 이름"}
rv = call("POST", f"rooms/{room}/docs/contract/review", {"values": vals}, a)
assert any(f["key"] == "waste_name" and "올린 원본" in f["message"] for f in rv["findings"]), rv["findings"]
# 바뀐 내용 따라가기: 업체 정보(주소)가 바뀌면 그 값을 쓴 칸에 새 값을 제안
call("POST", f"rooms/{room}/docs/contract", {"action": "editing", "values": pre["values"]}, a)
assert pre["values"].get("generator_address") == "경기 수원시 권선구"
call("POST", "profile", {"profile": {**profile, "address": "경기 화성시 팔탄면"}}, a)
pre2 = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)
ch = pre2["proposals"].get("generator_address")
assert ch and ch["kind"] == "changed" and ch["value"] == "경기 화성시 팔탄면" and pre2["values"]["generator_address"] == "경기 수원시 권선구", pre2["proposals"]
# 직접 고친 칸은 따라가지 않는다
call("POST", f"rooms/{room}/docs/contract", {"action": "editing", "values": {**pre2["values"], "contact_name": "직접 적음"},
                                             "sources": {"contact_name": "직접 입력"}}, a)
call("POST", "profile", {"profile": {**profile, "address": "경기 화성시 팔탄면", "manager": "새 담당"}}, a)
pre3 = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)
assert "contact_name" not in pre3["proposals"], pre3["proposals"]
# 고치기 결과는 바로 넣지 않고 제안으로
out = call("POST", f"rooms/{room}/docs/contract/fix", {"values": {k: v for k, v in pre3["values"].items() if k != "waste_classification"}}, a)
assert "waste_classification" in out["proposals"], out["proposals"].keys()
b = call("GET", f"rooms/{room}/desk", None, a)
assert next(x for x in b["docs"] if x["key"] == "contract")["proposals"] >= 1 and b["originals"]
# 법령 판단이 요구했는데 거래방 목록에 없는 서류 → 표시를 켜라는 안내
import time  # noqa: E402
call("POST", f"case/{room}/run", {"facts": {"code": "51-03-01", "name": "PE 필름", "form": "필름 조각", "monthly_ton": "5"}}, a)
for _ in range(120):
    if call("GET", f"case/{room}", None, a)["job"].get("status") != "running":
        break
    time.sleep(1)
b = call("GET", f"rooms/{room}/desk", None, a)
assert any(m["flag"] == "business" for m in b["missing"]), b["missing"]
call("POST", f"rooms/{room}/flags", {"business": True}, a)
assert not any(m["flag"] == "business" for m in call("GET", f"rooms/{room}/desk", None, a)["missing"])
print("확인 완료 — 진행판(빠진 서류·상대 업체 몫·기한·요청), 원본 나눠 넣기(제안), 수락/거절, 바뀐 값 따라가기, 원본 대조, 고치기 제안")
