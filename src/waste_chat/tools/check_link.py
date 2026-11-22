"""가입 업체 ↔ 공공자료 업체 연결 점검(모델 없이): 후보 찾기, 연결 저장(엉뚱한 키는 버림), 법령 기준 추천에 '거래 요청 가능'으로 나오기.
python tools/check_link.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["TIMELYGPT_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import directory, platform_api as api, store, tables  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out["json"]


# 재활용업체 가입 → 공공자료에서 우리 업체 찾기 → 연결
rec = {"name": "금철상사", "roles": ["recycler"], "address": "경기 화성시 정남면 보통내길17번길 46", "manager": "담당", "manager_phone": "031-354-8580",
       "permit_wastes": ["폐합성수지"], "permit_no": "제2020-1호", "lat": 37.17, "lng": 126.95}
r = api.handle("POST", "signup", {"login": "linkrec", "password": "secret1", "profile": rec}, None)["cookie"]
cands = call("POST", "public-match", {"name": rec["name"], "address": rec["address"], "phone": rec["manager_phone"]}, r)["candidates"]
assert cands and cands[0]["key"] == "금철상사_|화성시" and not cands[0]["taken"], cands[:2]
call("POST", "profile", {"profile": {**rec, "public_key": "없는업체|어딘가"}}, r)
assert store.session_company(r)["profile"]["public_key"] == ""            # 공공자료에 없는 키는 저장하지 않는다
call("POST", "profile", {"profile": {**rec, "public_key": cands[0]["key"]}}, r)
assert store.session_company(r)["profile"]["public_key"] == "금철상사_|화성시"
assert tables.public_passes("금철상사_|화성시", "51-03-01", ["R-10"]) and not tables.public_passes("금철상사_|화성시", "06-01-03", ["R-10"])

# 배출업체가 입력서로 찾고 판단 → 연결된 업체는 '거래 요청 가능'(목록 안이면 member, 밖이면 verified)
emi = {"name": "연결배출", "roles": ["emitter"], "address": "경기 화성시 향남읍", "manager": "담당", "manager_phone": "010",
       "wastes": ["폐합성수지"], "lat": 37.13, "lng": 126.9, "process": "사출"}
a = api.handle("POST", "signup", {"login": "linkemi", "password": "secret1", "profile": emi}, None)["cookie"]
s = call("POST", "intake/start", {"facts": {"name": "사출 불량품 플라스틱", "code": "51-03-01", "form": "고체 조각", "monthly_ton": "5",
                                            "address": "경기 화성시 향남읍", "components": "PP 70%, ABS 30%"}}, a)
for _ in range(120):
    v = call("GET", f"case/{s['id']}", None, a)
    if v["job"].get("status") != "running":
        break
    time.sleep(1)
plain = v["plain"]
rec_id = store.session_company(r)["id"]
in_list = [p for p in plain["routes"][0]["partners"] if (p.get("member") or {}).get("id") == rec_id] if plain["routes"] else []
in_verified = [m for m in plain.get("verified", []) if m["id"] == rec_id]
assert in_list or in_verified, (plain.get("verified"), [p.get("key") for p in plain["routes"][0]["partners"]])
assert all(m["id"] != rec_id for m in plain.get("members", []))        # 연결된 업체는 '허가 미확인'에 나오지 않는다
assert all(m["note"].startswith("공공자료와 연결되지 않아") for m in plain.get("members", []))
print("확인 완료 — 공공자료 후보 찾기, 연결 저장·검증, 법령 기준 추천에 거래 요청 가능으로 표시, 미연결 업체는 허가 미확인")

# 채팅에서 업체를 찾으면: 입력서 기준 추천은 그대로 두고, 대화에 요청한 업체 카드만 붙인다
from services import intake_chat  # noqa: E402
hau = {"name": "시연운반", "roles": ["hauler"], "address": "경기 화성시 향남읍", "manager": "담당", "manager_phone": "010",
       "permit_wastes": ["폐목재"], "permit_no": "운-1", "lat": 37.13, "lng": 126.9}
h_ = api.handle("POST", "signup", {"login": "linkhaul", "password": "secret1", "profile": hau}, None)["cookie"]
before = call("GET", f"case/{s['id']}", None, a)["plain"]
out = call("POST", "search", {"id": s["id"], "message": "시연운반 업체 찾아줘"}, a)
cards = out["turns"][-1]["companies"]
assert cards and cards[0]["name"] == "시연운반" and not cards[0]["fit"], cards          # 이름으로 찾음 · 영업대상 아님 표시
after = call("GET", f"case/{s['id']}", None, a)["plain"]
assert [p["name"] for p in before["routes"][0]["partners"]] == [p["name"] for p in after["routes"][0]["partners"]]
assert "haulers" not in after                                                          # 오른쪽 추천에는 운반업체 칸을 더하지 않는다
out = call("POST", "search", {"id": s["id"], "message": "운반업체 찾아줘"}, a)
kinds = {c["kind"] for c in out["turns"][-1]["companies"]}
assert "public" in kinds and all(c["match_role"] == "hauler" for c in out["turns"][-1]["companies"]), out["turns"][-1]["companies"][:3]
print("확인 완료 — 채팅에서 업체 찾기: 이름·종류로 카드(가입·공공자료), 영업대상 맞는지 표시, 입력서 기준 추천은 그대로")

# 예전 거래방이 있는 업체에도 새 기록에서는 거래 요청을 다시 보낼 수 있다(요청 상태는 기록마다)
old_req = [r for r in store.requests_for(store.session_company(a)["id"]) if r["to_id"] == store.session_company(h_)["id"]]
s_new = call("POST", "intake/start", {"facts": {"name": "사출 불량품 플라스틱", "code": "51-03-01", "form": "고체 조각", "monthly_ton": "5",
                                                "address": "경기 화성시 향남읍"}}, a)
call("POST", "requests", {"to": store.session_company(h_)["id"], "search_id": s["id"]}, a)
reqs = {q["search_id"]: q["status"] for q in call("GET", "bootstrap", None, a)["requests"] if q["other"]["id"] == store.session_company(h_)["id"]}
assert reqs.get(s["id"]) == "pending" and s_new["id"] not in reqs
assert api.asked_for(store.session_company(a), s_new["id"]) == {}
print("확인 완료 — 요청 상태는 거래 찾기 기록마다(예전 거래가 새 요청을 막지 않음)")
