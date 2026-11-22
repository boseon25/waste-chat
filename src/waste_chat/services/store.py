"""플랫폼 저장소. 업체·세션·AI 대화·요청·거래방·메시지·서류 상태를 SQLite 파일 하나에 둔다."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("WASTE_DATA_DIR") or ROOT / "data")
LOCK = threading.RLock()
_db = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS company(id TEXT PRIMARY KEY, login TEXT UNIQUE, salt TEXT, pw TEXT,
  profile TEXT NOT NULL, demo INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS session(token TEXT PRIMARY KEY, company_id TEXT, created REAL);
CREATE TABLE IF NOT EXISTS search(id TEXT PRIMARY KEY, company_id TEXT, title TEXT, need TEXT,
  turns TEXT, results TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS request(id TEXT PRIMARY KEY, from_id TEXT, to_id TEXT, room_id TEXT,
  as_role TEXT, need TEXT, note TEXT, status TEXT, created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS room(id TEXT PRIMARY KEY, title TEXT, need TEXT, created_by TEXT,
  flags TEXT, announced TEXT, created REAL);
CREATE TABLE IF NOT EXISTS member(room_id TEXT, company_id TEXT, role TEXT, agreed INTEGER DEFAULT 0,
  joined REAL, PRIMARY KEY(room_id, company_id));
CREATE TABLE IF NOT EXISTS message(id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, sender TEXT,
  kind TEXT, body TEXT, file_id TEXT, created REAL);
CREATE TABLE IF NOT EXISTS ai_turn(id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, company_id TEXT,
  role TEXT, body TEXT, created REAL);
CREATE TABLE IF NOT EXISTS docstate(room_id TEXT, item TEXT, company_id TEXT, status TEXT, extra TEXT,
  updated REAL, PRIMARY KEY(room_id, item, company_id));
CREATE TABLE IF NOT EXISTS casefile(owner TEXT PRIMARY KEY, facts TEXT, job TEXT, version INTEGER, updated REAL);
CREATE TABLE IF NOT EXISTS docdraft(room_id TEXT, item TEXT, company_id TEXT, doc_values TEXT, updated REAL,
  PRIMARY KEY(room_id, item, company_id));
CREATE TABLE IF NOT EXISTS desk(room_id TEXT, company_id TEXT, data TEXT, updated REAL,
  PRIMARY KEY(room_id, company_id));
CREATE TABLE IF NOT EXISTS file(id TEXT PRIMARY KEY, room_id TEXT, name TEXT, mime TEXT, data BLOB,
  by_id TEXT, created REAL);
"""


def db():
    global _db
    with LOCK:
        if _db is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _db = sqlite3.connect(DATA_DIR / "platform.db", check_same_thread=False, isolation_level=None)
            _db.row_factory = sqlite3.Row
            _db.executescript(SCHEMA)
        return _db


def run(sql, args=()):
    with LOCK:
        return db().execute(sql, args)


def rows(sql, args=()):
    with LOCK:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


def one(sql, args=()):
    found = rows(sql, args)
    return found[0] if found else None


def new_id(prefix):
    return prefix + secrets.token_hex(6)


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()


# ── 업체 ──────────────────────────────────────────────────────────────
def _company(row):
    if not row:
        return None
    return {"id": row["id"], "login": row["login"], "demo": bool(row["demo"]),
            "profile": json.loads(row["profile"]), "created": row["created"]}


def create_company(login, password, profile, demo=False, company_id=None):
    salt = secrets.token_hex(16)
    cid = company_id or new_id("c_")
    try:
        run("INSERT INTO company VALUES(?,?,?,?,?,?,?)",
            (cid, login, salt, _hash(password, salt), json.dumps(profile, ensure_ascii=False), int(demo), time.time()))
    except sqlite3.IntegrityError:
        raise ValueError("이미 사용 중인 아이디입니다.") from None
    return cid


def authenticate(login, password):
    row = one("SELECT * FROM company WHERE login=?", (login,))
    if not row or not hmac.compare_digest(row["pw"], _hash(password, row["salt"])):
        return None
    return _company(row)


def company(cid):
    return _company(one("SELECT * FROM company WHERE id=?", (cid,)))


def companies():
    return [_company(r) for r in rows("SELECT * FROM company ORDER BY created")]


def update_profile(cid, profile):
    run("UPDATE company SET profile=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), cid))


def new_session(cid):
    token = secrets.token_urlsafe(32)
    run("INSERT INTO session VALUES(?,?,?)", (token, cid, time.time()))
    return token


def session_company(token):
    if not token:
        return None
    row = one("SELECT company_id FROM session WHERE token=?", (token,))
    return company(row["company_id"]) if row else None


def drop_session(token):
    run("DELETE FROM session WHERE token=?", (token,))


# ── 거래 찾기 대화 ─────────────────────────────────────────────────────
def _search(row):
    if not row:
        return None
    return {"id": row["id"], "company_id": row["company_id"], "title": row["title"], "need": json.loads(row["need"]),
            "turns": json.loads(row["turns"]), "results": json.loads(row["results"]), "updated": row["updated"]}


def save_search(s):
    run("INSERT OR REPLACE INTO search VALUES(?,?,?,?,?,?,?)",
        (s["id"], s["company_id"], s["title"], json.dumps(s["need"], ensure_ascii=False),
         json.dumps(s["turns"], ensure_ascii=False), json.dumps(s["results"], ensure_ascii=False), time.time()))


def search(sid, cid):
    return _search(one("SELECT * FROM search WHERE id=? AND company_id=?", (sid, cid)))


def searches(cid):
    return rows("SELECT id,title,updated FROM search WHERE company_id=? ORDER BY updated DESC LIMIT 40", (cid,))


def delete_search(sid, cid):
    run("DELETE FROM search WHERE id=? AND company_id=?", (sid, cid))


# ── 요청 ──────────────────────────────────────────────────────────────
def _request(row):
    if row:
        row["need"] = json.loads(row["need"])
    return row


def create_request(from_id, to_id, room_id, as_role, need, note):
    rid = new_id("q_")
    run("INSERT INTO request VALUES(?,?,?,?,?,?,?,?,?,?)",
        (rid, from_id, to_id, room_id, as_role, json.dumps(need, ensure_ascii=False), note, "pending", time.time(), None))
    return rid


def request(rid):
    return _request(one("SELECT * FROM request WHERE id=?", (rid,)))


def requests_for(cid):
    found = rows("SELECT * FROM request WHERE from_id=? OR to_id=? ORDER BY created DESC LIMIT 80", (cid, cid))
    return [_request(r) for r in found]


def pending_between(from_id, to_id, room_id):
    return one("SELECT id FROM request WHERE from_id=? AND to_id=? AND status='pending' AND room_id IS ?",
               (from_id, to_id, room_id))


def room_requests(room_id):
    return [_request(r) for r in rows("SELECT * FROM request WHERE room_id=? ORDER BY created", (room_id,))]


def decide_request(rid, status, room_id=None):
    run("UPDATE request SET status=?, decided=?, room_id=COALESCE(?, room_id) WHERE id=?",
        (status, time.time(), room_id, rid))


def delete_request(rid):
    run("DELETE FROM request WHERE id=?", (rid,))


# ── 거래방 ────────────────────────────────────────────────────────────
ROOM_TABLES = ("member", "message", "ai_turn", "docstate", "docdraft", "desk", "file", "request")


def delete_room(rid):
    """거래방과 그 방의 대화·서류 작성본·첨부·진행판·법령 판단 기록·관련 요청을 지운다(되돌릴 수 없음)."""
    with LOCK:
        c = db()
        c.execute("BEGIN")
        try:
            for t in ROOM_TABLES:
                c.execute(f"DELETE FROM {t} WHERE room_id=?", (rid,))
            c.execute("DELETE FROM casefile WHERE owner=?", (rid,))
            c.execute("DELETE FROM room WHERE id=?", (rid,))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def _room(row):
    if row:
        row["need"] = json.loads(row["need"])
        row["flags"] = json.loads(row["flags"] or "{}")
        row["announced"] = json.loads(row["announced"] or "[]")
    return row


def create_room(title, need, created_by):
    rid = new_id("r_")
    run("INSERT INTO room VALUES(?,?,?,?,?,?,?)",
        (rid, title, json.dumps(need, ensure_ascii=False), created_by, "{}", "[]", time.time()))
    return rid


def room(rid):
    return _room(one("SELECT * FROM room WHERE id=?", (rid,)))


def update_room(rid, **changes):
    for name, value in changes.items():
        if name not in {"title", "need", "flags", "announced"}:
            raise KeyError(name)
        if name != "title":
            value = json.dumps(value, ensure_ascii=False)
        run(f"UPDATE room SET {name}=? WHERE id=?", (value, rid))


def add_member(room_id, cid, role):
    run("INSERT OR IGNORE INTO member VALUES(?,?,?,?,?)", (room_id, cid, role, 0, time.time()))


def members(room_id):
    return rows("SELECT * FROM member WHERE room_id=? ORDER BY joined", (room_id,))


def set_agreed(room_id, cid, agreed):
    run("UPDATE member SET agreed=? WHERE room_id=? AND company_id=?", (int(agreed), room_id, cid))


def reset_agreement(room_id):
    run("UPDATE member SET agreed=0 WHERE room_id=?", (room_id,))


def rooms_for(cid):
    found = rows("SELECT room.* FROM room JOIN member ON member.room_id=room.id WHERE member.company_id=? "
                 "ORDER BY room.created DESC", (cid,))
    return [_room(r) for r in found]


def add_message(room_id, sender, kind, body, file_id=None):
    cur = run("INSERT INTO message(room_id,sender,kind,body,file_id,created) VALUES(?,?,?,?,?,?)",
              (room_id, sender, kind, body, file_id, time.time()))
    return cur.lastrowid


def messages(room_id, after=0):
    return rows("SELECT * FROM message WHERE room_id=? AND id>? ORDER BY id LIMIT 500", (room_id, after))


def last_message_id(room_id):
    row = one("SELECT MAX(id) AS n FROM message WHERE room_id=?", (room_id,))
    return row["n"] or 0


def add_ai_turn(room_id, cid, role, body):
    run("INSERT INTO ai_turn(room_id,company_id,role,body,created) VALUES(?,?,?,?,?)",
        (room_id, cid, role, body, time.time()))


def ai_turns(room_id, cid):
    return rows("SELECT role,body,created FROM ai_turn WHERE room_id=? AND company_id=? ORDER BY id LIMIT 200",
                (room_id, cid))


# ── 서류 상태 ─────────────────────────────────────────────────────────
def set_doc(room_id, item, cid, status, extra=None):
    run("INSERT OR REPLACE INTO docstate VALUES(?,?,?,?,?,?)",
        (room_id, item, cid, status, json.dumps(extra or {}, ensure_ascii=False), time.time()))


def clear_doc(room_id, item, cid):
    run("DELETE FROM docstate WHERE room_id=? AND item=? AND company_id=?", (room_id, item, cid))


def doc_states(room_id):
    found = rows("SELECT * FROM docstate WHERE room_id=?", (room_id,))
    for r in found:
        r["extra"] = json.loads(r["extra"] or "{}")
    return found


def save_draft(room_id, item, cid, values):
    run("INSERT OR REPLACE INTO docdraft VALUES(?,?,?,?,?)",
        (room_id, item, cid, json.dumps(values, ensure_ascii=False), time.time()))


def casefile(owner):
    """법령 판단 대상(거래 찾기 대화 또는 거래방)의 사실과 마지막 판단 작업."""
    row = one("SELECT * FROM casefile WHERE owner=?", (owner,))
    if not row:
        return {"facts": {}, "job": {}, "version": 0}
    return {"facts": json.loads(row["facts"] or "{}"), "job": json.loads(row["job"] or "{}"), "version": row["version"] or 0}


def save_casefile(owner, facts=None, job=None, version=None):
    cur = casefile(owner)
    run("INSERT OR REPLACE INTO casefile VALUES(?,?,?,?,?)",
        (owner, json.dumps(cur["facts"] if facts is None else facts, ensure_ascii=False),
         json.dumps(cur["job"] if job is None else job, ensure_ascii=False),
         cur["version"] if version is None else version, time.time()))


def draft(room_id, item, cid):
    row = one("SELECT doc_values FROM docdraft WHERE room_id=? AND item=? AND company_id=?", (room_id, item, cid))
    return json.loads(row["doc_values"]) if row else {}


def room_drafts(room_id, cid):
    """이 업체의 작성본과 공유본. 최근 것이 앞에 온다."""
    found = rows("SELECT item, company_id, doc_values FROM docdraft WHERE room_id=? AND company_id IN (?, '') "
                   "ORDER BY updated DESC", (room_id, cid))
    return [(r["item"], r["company_id"], json.loads(r["doc_values"])) for r in found]


def add_file(room_id, name, mime, data, by_id):
    fid = new_id("f_")
    run("INSERT INTO file VALUES(?,?,?,?,?,?,?)", (fid, room_id, name, mime, data, by_id, time.time()))
    return fid


def file(fid):
    return one("SELECT * FROM file WHERE id=?", (fid,))


# ── 서류 비서(칸별 출처·확신도, 제안, 올린 원본, 보낸 요청) ─────────────
def desk(room_id, cid):
    row = one("SELECT data FROM desk WHERE room_id=? AND company_id=?", (room_id, cid))
    return json.loads(row["data"]) if row else {}


def update_desk(room_id, cid, change):
    """change(data)가 data를 고친다. 읽고 쓰는 사이에 다른 요청이 끼지 않게 잠근다."""
    with LOCK:
        data = desk(room_id, cid)
        out = change(data)
        run("INSERT OR REPLACE INTO desk VALUES(?,?,?,?)",
            (room_id, cid, json.dumps(data, ensure_ascii=False), time.time()))
        return out
