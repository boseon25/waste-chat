"""서류 작성 에이전트·법령 검토 점검(모델 없이). python tools/check_agent.py"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["TIMELYGPT_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import directory, doc_agent, lawbase, platform_api as api, store, tables  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out


def login(name):
    return call("POST", "login", {"login": name, "password": directory.DEMO_PASSWORD})["cookie"]


# 법령·표
assert lawbase.ready() and lawbase.label("WMA_RULE", "16의13/호3") == "폐기물관리법 시행규칙 제16조의13 제3호"
assert lawbase.search("수탁처리능력 확인서", 1)[0]["law"] == "WMA_RULE"
assert tables.check_code("51-03-01")[0] and not tables.check_code("51-03")[0] and not tables.check_code("99-99-99")[0]
assert tables.search_partners(["폐합성수지"], ["recycler"], (37.27, 127.0), 3)

# 거래방
profile = {"name": "점검배출", "roles": ["emitter"], "address": "경기 수원시", "manager": "담당", "manager_phone": "010",
           "wastes": ["폐합성수지"], "lat": 37.27, "lng": 127.0, "process": "사출 성형"}
a = call("POST", "signup", {"login": "checker", "password": "secret1", "profile": profile})["cookie"]
found = call("POST", "search", {"message": "PE 필름 스크랩이 월 5톤 나옵니다. 재활용업체를 찾아요"}, a)["json"]
target = [r for r in found["results"] if not r.get("public")][0]
call("POST", "requests", {"to": target["id"], "search_id": found["id"], "as_role": "processor"}, a)
c = login(target["id"].removeprefix("demo_"))
inbox = call("GET", "bootstrap", None, c)["json"]["requests"]
room = call("POST", f"requests/{inbox[0]['id']}/decide", {"accept": True}, c)["json"]["room_id"]
for who in (a, c):
    call("POST", f"rooms/{room}/agree", {"agree": True}, who)

pre = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)["json"]
assert pre["basis"]["waste_classification"][0]["label"].startswith("폐기물관리법 시행규칙 제16조의13")
assert pre["review"]["verdict"] == "fix"
values = pre["values"]
# 값 넣기: 잘못된 분류번호는 거절, 올바른 번호는 반영
out = call("POST", f"rooms/{room}/docs/contract/agent", {"message": "51-03", "values": values, "focus": "waste_classification"}, a)["json"]
assert not out["updates"] and out["rejected"] and "세분류" in out["rejected"][0]["why"], out
out = call("POST", f"rooms/{room}/docs/contract/agent", {"message": "51-03-01", "values": values, "focus": "waste_classification"}, a)["json"]
assert out["updates"] == {"waste_classification": "51-03-01"}, out
values.update(out["updates"])
# 질문: 근거 조문이 붙는다
out = call("POST", f"rooms/{room}/docs/contract/agent", {"message": "이 칸은 왜 꼭 써야 해?", "values": values, "focus": "waste_classification"}, a)["json"]
assert out["citations"] and not out["updates"], out
# 검토: 상호가 거래방과 다르면 경고, 기간 역전은 오류
bad = {**values, "receiver_name": "엉뚱한상사", "contract_period": "2027-05-31 ~ 2026-12-01"}
rv = call("POST", f"rooms/{room}/docs/contract/review", {"values": bad}, a)["json"]
msgs = [(f["key"], f["severity"]) for f in rv["findings"]]
assert ("receiver_name", "warning") in msgs and ("contract_period", "error") in msgs, msgs
assert all(f["basis"] is None or f["basis"]["label"] for f in rv["findings"])
# 상대 업체 몫 칸은 요청으로
out = call("POST", f"rooms/{room}/docs/contract/agent", {"message": "다음은 뭐 하면 돼?", "values": {k: v for k, v in values.items() if not k.startswith("receiver_")}}, a)["json"]
call("POST", f"rooms/{room}/docs/contract/ask", {"message": "처리업체 칸 확인 부탁드립니다."}, a)
# 근거 등급: 실무 안내가 법령 검색에 묻히지 않는다
tiers = lawbase.tiered("분석 의뢰는 어떻게 하고 뭘 준비해?")
assert {t["kind"] for t in tiers} >= {"law", "practice"}, [t["kind"] for t in tiers]
# 법령 판단 사실 → 서류 값 (서식 작성방법의 고체 1·액체 2)
assert doc_agent.from_facts("form5_c", {"code": "51-03-01", "form": "마른 필름 조각"}) == {"waste_classification": "51-03-01", "waste_state_code": "1"}
store.save_casefile(room, facts={"code": "51-03-01", "name": "PE 필름", "form": "필름 조각", "monthly_ton": "5"})
# 내 서류 초안 한 번에
made = call("POST", f"rooms/{room}/drafts", {}, a)["json"]["docs"]
assert made and all(d["filled"] for d in made), made
pre = call("GET", f"rooms/{room}/docs/contract/prefill", None, a)["json"]
assert pre["values"].get("waste_classification") == "51-03-01"
# 검토 → 고치기: 다른 서류·판단 사실로 고칠 수 있는 것은 고치고 나머지는 질문으로
vals = {k: v for k, v in pre["values"].items() if k != "waste_classification"}
fx = call("POST", f"rooms/{room}/docs/contract/fix", {"values": vals}, a)["json"]
assert fx["updates"].get("waste_classification") == "51-03-01", fx
assert fx["questions"], fx
print("확인 완료 — 법령 검색·인용(근거 등급), 분류번호 확인, 값 검증, 서류 검토·자동 수정, 업체 간 요청, 초안 한 번에")
