"""경로 결과에 근거 법령 목록(laws)을 붙인다.

관문 결과의 근거는 두 가지 모양으로 들어 있다.
  - 근거 키:   "WMA_RULE:B4의3@20261002#7aeb303d…"  (판정표의 근거 칸)
  - 법령 이름: "폐기물관리법 제25조"                 (관문 코드가 직접 쓴 조건 근거)
둘 다 {law, article, effective_date, key, used_for[]} 로 바꿔 경로마다 `laws` 목록을 만든다.

  route_dict = attach_laws(route.to_dict())

원문 발췌(text)는 아직 없다. 서버의 조문 저장소에서 같은 키로 찾아 붙일 예정.
키 형식과 대응표: 파트2_법령사용_정리.md 3장
"""
from __future__ import annotations

import re

LAW_NAMES = {
    "WMA": "폐기물관리법",
    "WMA_DEC": "폐기물관리법 시행령",
    "WMA_RULE": "폐기물관리법 시행규칙",
    "CEA": "순환경제사회 전환 촉진법",
    "CEA_DEC": "순환경제사회 전환 촉진법 시행령",
    "CEA_RULE": "순환경제사회 전환 촉진법 시행규칙",
    "CEA_DESIG": "순환자원 지정 등에 관한 고시",
    "CEA_RECOG": "순환자원 인정 절차 및 방법 등에 관한 고시",
    "GUIDE_DECL": "사업장폐기물 배출자신고 및 처리계획 확인 업무처리지침",
    "ALLBARO_NOTICE": "폐기물 전자정보처리 프로그램 운영 및 사용 등에 관한 고시",
}
# 이름으로 적힌 근거를 찾을 때 긴 이름부터 (시행규칙이 법률보다 먼저 맞도록)
_NAMES_LONGEST_FIRST = sorted(set(LAW_NAMES.values()), key=len, reverse=True)

# 판정표 이름이 근거 자리에 들어간 경우
SPECIAL = {
    "not_in_4_3": {"law": "폐기물관리법 시행규칙", "article": "별표 4의3에 없는 분류번호 → 별표 4의2 R-11 (재활용환경성평가)",
                   "effective_date": None, "key": None},
}

_KEY = re.compile(r"^(?P<law>[A-Z_]+):(?P<article>.+?)@(?P<date>\d{8})(?:#(?P<hash>[0-9a-f]+))?$")


def _article(raw: str) -> str:
    """조항 부분 → 읽을 수 있는 조항.
    B4의3 → 별표 4의3 / B5:3 → 별표 5 제3호 / BF5@처리구분 → 별지 제5호서식 처리구분
    13의2/항2 → 제13조의2 제2항 / 17/항1/호3 → 제17조 제1항 제3호 / §Ⅷ.1 → Ⅷ.1 / * → 전체
    """
    extra = ""
    if "@" in raw:                               # BF5@처리구분
        raw, extra = raw.split("@", 1)
        extra = " " + extra
    if raw == "*":
        return "전체" + extra
    if raw.startswith("§"):
        return raw[1:] + extra
    m = re.match(r"^BF(\S+)$", raw)
    if m:
        return f"별지 제{m.group(1)}호서식" + extra
    m = re.match(r"^B([^:]+)(?::(\S+))?$", raw)
    if m:
        return f"별표 {m.group(1)}" + (f" 제{m.group(2)}호" if m.group(2) else "") + extra
    parts = raw.split("/")
    out = [f"제{parts[0].replace('의', '조의', 1)}" if "의" in parts[0] else f"제{parts[0]}조"]
    for p in parts[1:]:
        m = re.match(r"^(항|호|목)(\S+)$", p)
        out.append(f"제{m.group(2)}{m.group(1)}" if m else p)
    return " ".join(out) + extra


def parse(basis: str) -> dict | None:
    """근거 하나 → {law, article, effective_date, key}. 알 수 없으면 None."""
    if not basis:
        return None
    basis = basis.strip()
    if basis in SPECIAL:
        return dict(SPECIAL[basis])
    head = basis.split("#", 1)[0]
    # 키 안에 @가 두 번 나올 수 있음 (BF5@처리구분@20261002)
    if "@" in head and head.rsplit("@", 1)[1].isdigit():
        art_part, date = head.rsplit("@", 1)
        law_code, _, article = art_part.partition(":")
        if law_code in LAW_NAMES and len(date) == 8:
            return {"law": LAW_NAMES[law_code], "article": _article(article),
                    "effective_date": f"{date[:4]}-{date[4:6]}-{date[6:]}", "key": basis}
    for name in _NAMES_LONGEST_FIRST:            # "폐기물관리법 시행규칙 별표 4의2 R-10"
        if basis.startswith(name):
            return {"law": name, "article": basis[len(name):].strip() or None,
                    "effective_date": None, "key": None}
    return None


def _norm_article(article: str | None) -> str:
    """같은 조항을 하나로 묶기 위한 비교값 ("별표 4의3 (사전분석확인)" → "별표 4의3")"""
    return re.sub(r"\s*\(.*\)$", "", article or "")


def collect(route: dict) -> list[dict]:
    """경로 dict → 근거 법령 목록. 같은 법령·조항은 하나로 묶고 쓰인 곳(used_for)을 모은다."""
    found: list[tuple[str, str]] = []           # (근거, 쓰인 곳)
    for e in route.get("evidence_ids") or []:
        found.append((e, f"판정: {route.get('title', '')}"))
    for c in route.get("conditions") or []:
        found.append((c.get("basis") or "", f"조건: {c.get('text', '')}"))
    for d in route.get("r_code_detail") or []:
        found.append((d.get("basis") or "", f"재활용 유형 {d.get('r_code')}: {d.get('description', '')}"))
    for t in route.get("required_tests") or []:
        found.append((t.get("basis") or "", f"시험: {t.get('test', '')} ({', '.join(t.get('r_codes') or [])})"))

    laws: dict[tuple, dict] = {}
    unknown: list[str] = []
    for basis, used in found:
        p = parse(basis)
        if p is None:
            if basis and basis not in unknown:
                unknown.append(basis)
            continue
        k = (p["law"], _norm_article(p["article"]))
        item = laws.get(k)
        if item is None:
            item = laws[k] = {**p, "used_for": []}
        elif p["key"] and not item["key"]:      # 이름으로 먼저 들어온 항목에 키·시행일 채우기
            item.update(key=p["key"], effective_date=p["effective_date"], article=p["article"])
        if used not in item["used_for"]:
            item["used_for"].append(used)
    out = list(laws.values())
    for item in out:
        item["text"] = None                     # 원문 발췌: 다음 단계
    if unknown:
        out.append({"law": None, "article": None, "effective_date": None, "key": None,
                    "used_for": [f"해석 못 한 근거: {u}" for u in unknown], "text": None})
    return out


def attach_laws(route: dict) -> dict:
    route["laws"] = collect(route)
    return route


__all__ = ["LAW_NAMES", "parse", "collect", "attach_laws"]
