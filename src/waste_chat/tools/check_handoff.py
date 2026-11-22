"""분류 에이전트 전달 파일(.json)로 시작하기 점검: 읽기 → 분류번호 고정 → 업체 찾기 + 법령 판단(시험용 답) → 엔진 입력에 원래 값이 남는지.
python tools/check_handoff.py"""
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["WASTE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["TIMELYGPT_API_KEY"] = ""
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import directory, engine, intake_handoff, platform_api as api, store  # noqa: E402

store.db()
directory.seed_demo()


def call(method, path, data=None, token=None, expect=200):
    out = api.handle(method, path, data, token)
    assert out["status"] == expect, (method, path, out.get("json"))
    return out["json"]


emitter = next(c for c in store.companies() if "emitter" in c["profile"].get("roles", []))
tok = store.new_session(emitter["id"])
blob = (ROOT / "tools/fixtures/part2_handoff_example.json").read_bytes()
read = call("POST", "intake/read", {"file": {"name": "handoff.json", "data": base64.b64encode(blob).decode()}}, tok)
assert read["kind"] == "handoff" and len(read["items"]) == 2, read.keys()
film, wash = read["items"]
assert film["code_fixed"] and film["facts"]["code"] == "51-03-01", film["facts"]
assert wash["facts"]["code"] == "04-02-00" and wash["facts"]["hazard"] == "예", wash["facts"]
assert film["facts"]["contamination"] == "깨끗" and wash["facts"]["contamination"] == "오염"
assert wash["facts"]["mixture"] == "혼합" and wash["facts"]["separable"] == "불가능"
assert film["facts"]["monthly_ton"] == "0.195" and film["facts"]["address"].startswith("전북특별자치도 전주시")
assert any("발열량" in t for t in film["sources"]) and any("순환자원 인정" in t for t in film["sources"])
call("POST", "intake/read", {"file": {"name": "x.json", "data": base64.b64encode(b'{"cases": []}').decode()}}, tok, 400)
call("POST", "intake/read", {"file": {"name": "x.json", "data": base64.b64encode(b"{not json").decode()}}, tok, 400)

# 화면이 분류번호를 바꿔 보내도 전달 파일 값으로 정한다
s = call("POST", "intake/start", {"facts": {**wash["facts"], "code": "51-03-01"}, "want": ["recycler"],
                                  "handoff": wash["handoff"], "file": "handoff.json"}, tok)
assert "전달 파일" in s["turns"][0]["text"]
for _ in range(120):
    view = call("GET", "case/" + s["id"], None, tok)
    if view["job"].get("status") != "running":
        break
    time.sleep(1)
assert view["job"]["status"] == "done", view["job"]
assert view["facts"]["code"] == "04-02-00" and view["code_fixed"]["code"] == "04-02-00"
case = json.loads((store.DATA_DIR / "engine" / view["job"]["id"] / "case.json").read_text())["cases"][0]
assert case["schema_version"] == "1.0" and case["classification"]["selected_code"] == "04-02-00"
assert case["analysis"]["heating_value_kcal"] == 6200 and len(case["analysis"]["leaching_results"]) == 3   # 원래 값이 남음
assert case["components"][0]["separable"] is False and case["designated_waste_status"] == "designated"
# 사실 고치기: 바꾼 값(월 발생량)만 덮고 분류번호는 그대로
call("POST", f"case/{s['id']}/run", {"facts": {**view["facts"], "monthly_ton": "0.5", "code": "51-03-01"}}, tok)
ho = engine.handoff(s["id"])
c2 = intake_handoff.engine_case(ho["case"], {**view["facts"], "monthly_ton": "0.5"}, ho["base"], s["id"], 9)
assert c2["quantity"]["monthly_ton"] == [0.5] and c2["analysis"]["chlorine_pct"] == 0.04
assert store.casefile(s["id"])["facts"]["code"] == "04-02-00"
# 전달 파일 없이 시작한 건은 그대로(분류번호를 사람이 정함)
assert engine.handoff("s_none") is None
print("check_handoff: ok")
