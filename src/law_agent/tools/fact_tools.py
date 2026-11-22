"""사실 데이터 조회 도구 — settings.yaml allowed_tables 만 읽는다. 판정하지 않는다.

판정표(blocked_tables)를 열려 하면 BlockedTableError. allowed_tables 에 없는 표도 NotAllowedTableError.
route_agent 관문 코드의 표 읽기 클래스는 판정표를 같이 읽으므로 law_agent 에서는 쓰지 않는다 (여기서 CSV 를 직접 읽음).

  waste_code_exists / waste_code_name / waste_code_info    분류번호 (waste_codes_4.csv, '지정폐기물' 칸은 빼고 돌려줌)
  r_code_exists / r_code_info / r_code_tier                R코드 (rcode_detail_4_2.csv, tier 는 composer 전용)
  treatment_code_exists / treatment_code_name / _info      처리방법 코드 (treatment_code_map.csv)
  treatment_codes(처리구분)                                 처리구분별 코드 목록 (composer 경로 묶음)
  code_kind / check_code                                   verifier 2겹: 값이 사실 데이터에 있는지
"""
from __future__ import annotations

import csv
import fnmatch
import re
from functools import lru_cache
from pathlib import Path

from law_agent.settings import env, load_settings


class BlockedTableError(PermissionError):
    """판정표를 열려 함 (law_agent 규칙 2)."""


class NotAllowedTableError(PermissionError):
    """allowed_tables 에 없는 표."""


# 판단 쪽(judge·verifier)에 보이면 안 되는 칸 — 사람이 해석해 넣은 값
HIDDEN_COLUMNS = {
    "waste_codes_4.csv": {"지정폐기물"},            # A-2 는 judge 가 원문으로 판단
    "rcode_detail_4_2.csv": {"tier"},             # 설계값 (r_code_tier 로만)
}


def _cfg() -> dict:
    return load_settings().get("data") or {}


def check_table_name(name: str) -> None:
    """읽어도 되는 표인지. 안 되면 예외. 이름은 파일 이름만 (폴더 경로 금지)."""
    if Path(name).name != name:
        raise NotAllowedTableError(f"표 이름에 경로를 넣지 않는다: {name}")
    cfg = _cfg()
    if name in set(cfg.get("blocked_tables") or []):
        raise BlockedTableError(f"{name} 은 판정표라 law_agent 에서 읽지 않는다 (eval 만)")
    if not any(fnmatch.fnmatch(name, pat) for pat in cfg.get("allowed_tables") or []):
        raise NotAllowedTableError(f"{name} 은 settings.yaml allowed_tables 에 없다")


EXCEPTION_USES: list[dict] = []      # 예외 표를 읽은 기록 (감사용 — run.py 가 결과에 붙인다)


def read_exception_table(name: str, directory: str | Path | None = None) -> list[dict]:
    """판정표지만 settings data.partner_matching_exception 에 올린 예외 (데이터 지도 1안: reporter_scope — 업체 매칭이
    신고 업체 범위를 거르는 데 씀). 목록에 없으면 BlockedTableError. 읽을 때마다 EXCEPTION_USES 에 기록."""
    cfg = _cfg()
    if name not in set(cfg.get("partner_matching_exception") or []):
        raise BlockedTableError(f"{name} 은 예외 목록(partner_matching_exception)에 없다")
    d = Path(directory) if directory else tables_dir()
    rows = [dict(r) for r in _read(str((d / name).resolve()))]
    EXCEPTION_USES.append({"table": name, "rows": len(rows), "used_for": "업체 매칭 (신고 업체 범위)",
                           "note": "판정표 예외 사용 — eval 에서 I-2 비교 제외"})
    return rows


TABLE_USES: list[dict] = []          # 표 답(table_answer)으로 읽은 판정표 기록


def read_answer_table(name: str, directory: str | Path | None = None) -> list[dict]:
    """법 조문이 '분류번호별 표'인 질문(D-1·C-1·F-1·F-2)에 답하려고 읽는 판정표 (2026.11.18 규칙 2 개정).
    settings data.table_answer_tables 에 있는 것만. 같은 표는 한 실행에 한 번만 기록."""
    cfg = _cfg()
    if name not in set(cfg.get("table_answer_tables") or []):
        raise BlockedTableError(f"{name} 은 table_answer_tables 에 없다")
    d = Path(directory) if directory else tables_dir()
    rows = [dict(r) for r in _read(str((d / name).resolve()))]
    if not any(u["table"] == name for u in TABLE_USES):
        TABLE_USES.append({"table": name, "rows": len(rows), "used_for": "표 답 (법 조문이 분류번호별 표인 질문)"})
    return rows


def tables_dir() -> Path:
    return Path(env("tables_dir_env"))


@lru_cache(maxsize=32)
def _read(path: str) -> tuple[dict, ...]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return tuple(dict(r) for r in csv.DictReader(f))


def read_table(name: str, directory: str | Path | None = None) -> list[dict]:
    """허용된 표 읽기 (캐시). 행은 복사본이라 고쳐도 캐시에 영향 없음."""
    check_table_name(name)
    d = Path(directory) if directory else tables_dir()
    return [dict(r) for r in _read(str((d / name).resolve()))]


def clear_cache() -> None:
    _read.cache_clear()
    _index.cache_clear()


@lru_cache(maxsize=16)
def _index(name: str, key_col: str, directory: str) -> dict[str, dict]:
    return {r[key_col].strip(): r for r in read_table(name, directory) if (r.get(key_col) or "").strip()}


def _row(name: str, key_col: str, key: str, directory: str | Path | None) -> dict | None:
    d = str(Path(directory).resolve()) if directory else str(tables_dir().resolve())
    r = _index(name, key_col, d).get((key or "").strip())
    if r is None:
        return None
    hidden = HIDDEN_COLUMNS.get(name, set())
    return {k: v for k, v in r.items() if k not in hidden}


# ── 분류번호 ───────────────────────────────────────────────────

def waste_code_info(code: str, directory: str | Path | None = None) -> dict | None:
    """분류번호 행 (지정폐기물 칸 제외): 분류번호, 폐기물_구분, 단계, 상위, 명칭, 근거, 말단."""
    return _row("waste_codes_4.csv", "분류번호", code, directory)


def waste_code_exists(code: str, directory: str | Path | None = None) -> bool:
    return waste_code_info(code, directory) is not None


def waste_code_name(code: str, directory: str | Path | None = None) -> str | None:
    r = waste_code_info(code, directory)
    return r.get("명칭") if r else None


# ── R코드 ──────────────────────────────────────────────────────

def r_code_info(rc: str, directory: str | Path | None = None) -> dict | None:
    """R코드 행 (tier 제외): r_code, 단계, 상위, 설명, 근거."""
    return _row("rcode_detail_4_2.csv", "r_code", rc, directory)


def r_code_exists(rc: str, directory: str | Path | None = None) -> bool:
    return r_code_info(rc, directory) is not None


def top_rcode(rc: str) -> str:
    """R-3-3 → R-3"""
    parts = rc.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else rc


def r_code_tier(rc: str, directory: str | Path | None = None) -> str | None:
    """경로 묶음(reuse / material_recycle / energy_recovery). composer 전용 설계값 — judge 에 넘기지 않는다.
    세부 코드에 없으면 상위 코드(R-3)의 값."""
    d = str(Path(directory).resolve()) if directory else str(tables_dir().resolve())
    idx = _index("rcode_detail_4_2.csv", "r_code", d)
    for code in (rc, top_rcode(rc)):
        tier = ((idx.get(code) or {}).get("tier") or "").strip()
        if tier:
            return tier
    return None


# ── 처리방법 코드 ──────────────────────────────────────────────

_TCODE_COLS = ("code", "처리구분", "처리분류", "세분류", "근거", "r_code")


def treatment_code_info(code: str, directory: str | Path | None = None) -> dict | None:
    """처리방법 코드: code, 처리구분, 처리분류, 세분류, 근거, r_code. (탄소 계산용 칸은 빼고)"""
    r = _row("treatment_code_map.csv", "code", code, directory)
    return {k: r.get(k, "") for k in _TCODE_COLS} if r else None


def treatment_code_exists(code: str, directory: str | Path | None = None) -> bool:
    return treatment_code_info(code, directory) is not None


def treatment_code_name(code: str, directory: str | Path | None = None) -> str | None:
    r = treatment_code_info(code, directory)
    if not r:
        return None
    return r.get("세분류") or r.get("처리분류") or None


def treatment_code_for_rcode(rc: str, directory: str | Path | None = None) -> str | None:
    """R코드 → 위탁처리 처리방법 코드 (route_agent Tables.treatment_code_of 와 같은 규칙:
    treatment_code_map 에서 처리구분 = 위탁처리 이고 r_code 가 R코드의 상위(R-3-3 → R-3)인 행의 code)."""
    top = top_rcode(rc)
    for r in read_table("treatment_code_map.csv", directory):
        if (r.get("처리구분") or "") == "위탁처리" and (r.get("r_code") or "").strip() in (top, rc):
            return r["code"]
    return None


def treatment_codes(kind: str | None = None, directory: str | Path | None = None) -> list[dict]:
    """처리방법 코드 목록. kind 를 주면 처리구분 또는 처리분류가 kind 를 포함하는 것만 (예: "소각", "매립")."""
    rows = read_table("treatment_code_map.csv", directory)
    out = []
    for r in rows:
        if kind and kind not in (r.get("처리구분") or "") and kind not in (r.get("처리분류") or ""):
            continue
        out.append({k: r.get(k, "") for k in _TCODE_COLS})
    return sorted(out, key=lambda r: r["code"])


# ── 값 검사용 (verifier 2겹) ──────────────────────────────────

RX_WASTE_CODE = re.compile(r"^\d{2}(?:-\d{2}){0,3}$")       # 51, 51-03, 51-03-01
RX_R_CODE = re.compile(r"^R-\d+(?:-\d+){0,2}$")             # R-3, R-3-2
RX_T_CODE = re.compile(r"^\d{4}$")                          # 2003


def code_kind(code: str) -> str | None:
    """코드 모양으로 종류: waste_code / r_code / treatment_code / None(모름)."""
    c = (code or "").strip()
    if RX_R_CODE.match(c):
        return "r_code"
    if RX_T_CODE.match(c):
        return "treatment_code"
    if RX_WASTE_CODE.match(c):
        return "waste_code"
    return None


def check_code(code: str, directory: str | Path | None = None) -> dict:
    """{"code", "kind", "exists", "name"}. kind 가 None 이면 exists=None (코드가 아닌 값: 허가 종류 등)."""
    kind = code_kind(code)
    if kind == "r_code":
        info = r_code_info(code, directory)
        return {"code": code, "kind": kind, "exists": info is not None, "name": (info or {}).get("설명")}
    if kind == "treatment_code":
        return {"code": code, "kind": kind, "exists": treatment_code_exists(code, directory),
                "name": treatment_code_name(code, directory)}
    if kind == "waste_code":
        return {"code": code, "kind": kind, "exists": waste_code_exists(code, directory),
                "name": waste_code_name(code, directory)}
    return {"code": code, "kind": None, "exists": None, "name": None}
