"""기초 입력서로 시작하기 점검: 입력서 읽기 → 보충 질문 → 분류번호 확인 → 업체 찾기 + 법령 판단(시험용 답). python tools/check_intake.py"""
import base64
import os
import sys
import tempfile
import time
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


emitter = next(c for c in store.companies() if "emitter" in c["profile"].get("roles", []))
tok = store.new_session(emitter["id"])
from services.paths import PART1  # noqa: E402
blob = (PART1 / "fixtures/intake_records_example.docx").read_bytes()
# Word 입력서 → 파트 1 접수(db_intake) → 폐기물 건마다 확인 질문·분류번호
read = call("POST", "intake/read", {"file": {"name": "a.docx", "data": base64.b64encode(blob).decode()}}, tok)
assert read["kind"] == "part1" and len(read["items"]) == 2, read.get("kind")
film, wash = read["items"]
assert film["wastes"] == ["폐합성수지"], film["wastes"]           # 성분의 종이 라벨로 품목이 늘지 않음
assert film["part1"]["sid"] == read["sid"] and film["p1q"], film["p1q"]   # 파트 1이 묻는 질문
assert not film["code_fixed"]                                      # 이 예시 입력서에는 분류번호 칸 값이 없다
call("POST", "intake/read", {"file": {"name": "b.docx", "data": base64.b64encode(b"PK not a docx").decode()}}, tok, 400)
P1 = film["part1"]
# 다른 업체는 이 접수에 손댈 수 없다
other = next(c for c in store.companies() if c["id"] != emitter["id"])
call("POST", "intake/p1-code", {"part1": P1, "code": "51-03-01"}, store.new_session(other["id"]), 400)
# 확인 질문 답 → 파트 1에 저장되고 다음 질문으로
q = film["p1q"][0]
film2 = call("POST", "intake/p1-answer", {"part1": P1, "request": q["id"], "value": "2026-11-09"}, tok)
assert q["id"] not in [x["id"] for x in film2["p1q"]], film2["p1q"]
call("POST", "intake/p1-code", {"part1": P1, "code": "51-03"}, tok, 400)        # 상위 분류는 받지 않음
film3 = call("POST", "intake/p1-code", {"part1": P1, "code": "51-03-01", "reference": "신고증명서"}, tok)
assert film3["code_fixed"] and film3["facts"]["code"] == "51-03-01" and film3["handoff"]["classification"]["selected_code"] == "51-03-01"
call("POST", "intake/start", {"facts": {"name": "알 수 없는 것"}}, tok, 400)

# 분류번호 없는 건(세척액): 업체만 찾고 판단은 보류
s = call("POST", "intake/start", {"facts": wash["facts"], "part1": wash["part1"]}, tok)
assert s["intake"] and s["results"] and "법령 판단은 아직" in s["turns"][1]["text"], s["turns"][1]["text"]
# 분류번호 기록한 건: 파트 1 내보내기 → 판단 입력 그대로 엔진에
s = call("POST", "intake/start", {"facts": {**film3["facts"], "code": "06-01-03"}, "part1": P1, "want": ["recycler"]}, tok)
for _ in range(120):
    view = call("GET", "case/" + s["id"], None, tok)
    if view["job"].get("status") != "running":
        break
    time.sleep(1)
assert view["job"]["status"] == "done" and view["result"]["routes"], view["job"]
assert view["facts"]["code"] == "51-03-01" and view["code_fixed"]["code"] == "51-03-01"   # 화면이 보낸 번호가 아니라 파트 1 기록
facts = film3["facts"]
print("확인 완료 — 입력서 파트 1 접수, 확인 질문 답, 분류번호 기록, 내보내기로 업체 찾기 + 법령 판단")

# 입력서로 시작한 기록의 채팅: 검색 조건은 바뀌지 않고 답만 붙는다(모델 없으면 근거 원문 안내)
s2 = call("POST", "intake/start", {"facts": {**facts, "code": "51-03-01"}}, tok)
before = (s2["need"], [c["id"] for c in s2["results"]])
s3 = call("POST", "search", {"id": s2["id"], "message": "폐유도 같이 맡길 수 있나요?"}, tok)
assert (s3["need"], [c["id"] for c in s3["results"]]) == before and s3["turns"][-1]["mode"] == "ask", s3["turns"][-1]
print("확인 완료 — 입력서 기록의 채팅은 조건을 바꾸지 않는 질문 답변")
