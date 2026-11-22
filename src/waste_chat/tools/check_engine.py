"""법령 판단 엔진 연결 점검. 키가 없으면 엔진의 시험용 답(WASTE_DRY)으로 형식만 본다. python tools/check_engine.py [--real]"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
REAL = "--real" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if REAL:
    from services import envfile  # noqa: E402
    envfile.load()
else:
    os.environ["TIMELYGPT_API_KEY"] = ""

from services import directory, engine, platform_api as api, store  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out


profile = {"name": "점검배출", "roles": ["emitter"], "address": "경기 수원시 권선구 산업로 1", "manager": "담당",
           "manager_phone": "010", "wastes": ["폐합성수지"], "lat": 37.27, "lng": 127.0, "process": "포장 공정"}
a = call("POST", "signup", {"login": "checker", "password": "secret1", "profile": profile})["cookie"]
s = call("POST", "search", {"message": "PE 포장필름 자투리가 월 5톤 나옵니다. 오염은 거의 없고 재활용업체를 찾아요"}, a)["json"]
view = call("GET", f"case/{s['id']}", None, a)["json"]
facts = view["facts"]
assert view["prefilled"] and facts["monthly_ton"] == "5" and facts["address"].startswith("경기"), facts
assert any(c["code"] == "51-03-01" for c in view["codes"]), view["codes"]
call("POST", f"case/{s['id']}/run", {"facts": {**facts, "code": "51-03"}}, a, expect=400)
facts.update(code="51-03-01", name="PE 포장필름 자투리", components="PE 98%, 종이 라벨 2%", form="마른 필름 조각",
             contamination="깨끗", hazard="아니오")
view = call("POST", f"case/{s['id']}/run", {"facts": facts}, a)["json"]
assert view["job"]["status"] == "running"
t = time.time()
while time.time() - t < 400:
    view = call("GET", f"case/{s['id']}", None, a)["json"]
    if view["job"]["status"] != "running":
        break
    time.sleep(2)
assert view["job"]["status"] == "done", (view["job"], view["progress"])
res = view["result"]
assert res["code"] == "51-03-01" and res["routes"], res
route = res["routes"][0]
print("경로:", route["kind"], route["legal"], "| 업체", len(route["partners"]), "| 서류", [d["title"] for d in res["docs"]],
      "| 질문", len(res["questions"]), "| 판단", len(res["checks"]), f"| {time.time() - t:.0f}초")
# 쉬운 결과: 결론·할 일·맡길 곳·서류·근거. 내부 표기(None, 관문, 모델 이름)는 화면 문장에 나오지 않는다
plain = view["plain"]
assert plain["verdict"]["tone"] in ("ok", "warn", "bad") and plain["verdict"]["title"] and plain["trust"].startswith("판단")
import json as _json  # noqa: E402
shown = _json.dumps({k: plain[k] for k in ("verdict", "todo", "docs")}, ensure_ascii=False)
assert "None" not in shown and "관문" not in shown, shown
assert all(t["ask"] and t["key"] in api.engine.FACT_KEYS for t in plain["todo"]), plain["todo"]
# 분류번호가 폐기물과 맞지 않으면(A-1 '아니오') 업체를 숨기고 맞는 번호 후보를 낸다
from services import judgment_view  # noqa: E402
fake = {**res, "code_mismatch": {"code": "51-16-00", "code_name": "폐식용유", "name": "폐유압유", "reason": "", "candidates": []}}
pv = judgment_view.build(fake, facts)
assert pv["verdict"]["tone"] == "bad" and all(not r["partners"] for r in pv["routes"])
print("확인 완료 — 사례 만들기, 판단 실행(" + ("실제 모델" if REAL else "시험용 답") + "), 결과 정리")
