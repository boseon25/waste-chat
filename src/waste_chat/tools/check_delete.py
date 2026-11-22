"""거래 지우기 점검: 보낸 요청 취소(보낸 쪽·대기 중만), 거래방 삭제(만든 업체만, 방의 대화·서류·첨부·판단 기록·요청까지).
python tools/check_delete.py"""
import os
import sys
import tempfile
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


cos = store.companies()
emitter = next(c for c in cos if "emitter" in c["profile"].get("roles", []))
hauler = next(c for c in cos if "hauler" in c["profile"].get("roles", []) and c["id"] != emitter["id"])
third = next(c for c in cos if c["id"] not in (emitter["id"], hauler["id"]))
te, th, t3 = (store.new_session(c["id"]) for c in (emitter, hauler, third))
need = {"wastes": ["폐합성수지"], "quantity": "월 5톤"}

# 1) 요청 취소: 받은 쪽은 못 하고, 보낸 쪽은 대기 중일 때만
call("POST", "requests", {"to": third["id"], "need": need}, te)
q = next(r for r in call("GET", "bootstrap", None, te)["requests"] if not r["incoming"])
call("POST", f"requests/{q['id']}/cancel", {}, t3, 409)
call("POST", f"requests/{q['id']}/cancel", {}, te)
assert store.request(q["id"]) is None

# 2) 거래방 열기 → 대화·첨부·서류·판단 기록 만들기
call("POST", "requests", {"to": hauler["id"], "need": need}, te)
q = next(r for r in call("GET", "bootstrap", None, th)["requests"] if r["incoming"] and r["status"] == "pending")
room_id = call("POST", f"requests/{q['id']}/decide", {"accept": True}, th)["room_id"]
call("POST", f"rooms/{room_id}/messages", {"text": "안녕하세요"}, te)
store.add_file(room_id, "a.pdf", "application/pdf", b"%PDF-1.4", emitter["id"])
store.save_draft(room_id, "rfq", emitter["id"], {"waste_name": "폐비닐"})
store.save_casefile(room_id, facts={"name": "폐비닐"})
call("POST", "requests", {"to": third["id"], "room_id": room_id}, te)       # 초대 대기 중
assert call("GET", f"rooms/{room_id}", None, te)["room"]["can_delete"]
assert not call("GET", f"rooms/{room_id}", None, th)["room"]["can_delete"]

# 3) 만든 업체가 아니면 삭제 불가, 만든 업체는 삭제 → 관련 기록 모두 사라짐
call("POST", f"rooms/{room_id}/delete", {}, th, 403)
call("POST", f"rooms/{room_id}/delete", {}, t3, 404)                        # 참여하지 않은 업체(초대만 받음)
call("POST", f"rooms/{room_id}/delete", {}, te)
assert store.room(room_id) is None
for t in store.ROOM_TABLES:
    assert not store.rows(f"SELECT 1 FROM {t} WHERE room_id=?", (room_id,)), t
assert not store.casefile(room_id)["facts"]
call("GET", f"rooms/{room_id}", None, th, 404)
assert not [r for r in call("GET", "bootstrap", None, th)["rooms"] if r["id"] == room_id]
print("확인 완료 — 보낸 요청 취소, 거래방 삭제(만든 업체만, 대화·서류·첨부·판단 기록·요청까지)")
