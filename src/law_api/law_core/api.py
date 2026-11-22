"""국가법령정보 OPEN API

- 인증키는 환경변수 LAW_OC로만 읽는다. 코드·문서·보고서에 키 값을 쓰지 않는다.
- 주소·예외 문자열을 밖으로 내보낼 때는 반드시 mask()를 거친다 (OC=***).
- LAW_MOCK_DIR이 있으면 네트워크 대신 그 폴더의 {md5(OC를 뺀 주소)}.json을 읽는다 (테스트용).
"""
import hashlib, json, os, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

from .chunker import walk

BASE = "https://www.law.go.kr"
SLEEP = 1.2
TIMEOUT = 40
HEADERS = {"User-Agent": "law-watch/0.2"}
NAME_KEYS = ["법령명한글", "법령명", "행정규칙명"]

_RX_OC = re.compile(r"(?<![A-Za-z])(OC=)[^&\s'\"<>]*", re.I)


def mask(s) -> str:
    """주소·오류 문자열에서 인증키를 가린다. 'OC=값' 형태와 키 값 자체 모두."""
    s = _RX_OC.sub(r"\1***", str(s))
    key = os.environ.get("LAW_OC", "")
    if len(key) >= 4:
        s = s.replace(key, "***")
    return s


def oc() -> str:
    v = os.environ.get("LAW_OC")
    if not v:
        sys.exit("환경변수 LAW_OC가 없습니다. 발급받은 인증키를 LAW_OC에 넣어 주세요.")
    return v


def mock_key(url: str) -> str:
    """LAW_MOCK_DIR 파일 이름 (예전 law_watch와 같은 규칙: OC를 뺀 주소의 md5)"""
    return hashlib.md5(re.sub(r"OC=[^&]*&?", "", url).encode()).hexdigest()


def get_json(url: str):
    mock = os.environ.get("LAW_MOCK_DIR")
    if mock:
        p = Path(mock) / f"{mock_key(url)}.json"
        if not p.exists():
            raise FileNotFoundError(f"mock 없음: {mask(url)} -> {p.name}")
        return json.loads(p.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8")
    except Exception as exc:  # from None: 원래 예외(주소가 담길 수 있음)를 이어 붙이지 않음
        raise RuntimeError(f"API 요청 실패: {mask(url)} / {mask(exc)}") from None
    finally:
        time.sleep(SLEEP)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"JSON이 아닌 응답 (인증키·신청 항목 확인): {mask(url)} / {mask(body[:200])}") from None


def download(link: str, dest: Path):
    """별표 PDF 등 파일 받기 (link는 '/LSW/…' 같은 상대 주소도 됨)"""
    url = urllib.parse.urljoin(BASE, link)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
    except Exception as exc:
        raise RuntimeError(f"내려받기 실패: {mask(url)} / {mask(exc)}") from None
    finally:
        time.sleep(SLEEP)


def search(t, page=1):
    target = "eflaw" if t["kind"] == "law" else "admrul"
    q = urllib.parse.urlencode({"OC": oc(), "target": target, "type": "JSON", "query": t["name"],
                                "display": 100, "page": page})
    return get_json(f"{BASE}/DRF/lawSearch.do?{q}")


def pick_item(t, result):
    """목록에서 이름이 정확히 같은 현행 항목 하나를 고른다."""
    cands = [d for d in walk(result) if any(k in d for k in NAME_KEYS)]
    exact = [d for d in cands if any(str(d.get(k, "")).strip() == t["name"] for k in NAME_KEYS)]
    if not exact:
        names = sorted({str(d.get(k)) for d in cands for k in NAME_KEYS if d.get(k)})[:10]
        raise LookupError(f"'{t['name']}' 정확히 일치하는 항목 없음. 후보: {names}")
    exact.sort(key=lambda d: (str(d.get("현행연혁코드", d.get("현행여부", ""))) != "현행",
                              -int(re.sub(r"\D", "", str(d.get("시행일자", "0"))) or 0)))
    return exact[0]


def find_item(t):
    """정확히 같은 이름이 나올 때까지 최대 5쪽까지 확인"""
    last = None
    for page in range(1, 6):
        res = search(t, page)
        try:
            return pick_item(t, res)
        except LookupError as e:
            last = e
            if not [d for d in walk(res) if any(k in d for k in NAME_KEYS)]:
                break  # 더 이상 결과 없음
    raise last or LookupError(f"'{t['name']}' 검색 실패")


def meta_of(item):
    g = lambda *ks: next((str(item[k]) for k in ks if item.get(k) not in (None, "")), "")
    return {
        "serial": g("법령일련번호", "행정규칙일련번호", "MST"),
        "id": g("법령ID", "행정규칙ID"),
        "effective": g("시행일자"),
        "promulgated": g("공포일자", "발령일자"),
        "number": g("공포번호", "발령번호"),
        "detail_link": g("법령상세링크", "행정규칙상세링크"),
    }


def fetch_detail(meta):
    link = meta["detail_link"]
    if not link:
        raise LookupError("상세링크가 없습니다")
    link = re.sub(r"OC=[^&]*", f"OC={oc()}", link)
    link = re.sub(r"type=[A-Za-z]+", "type=JSON", link) if "type=" in link else link + "&type=JSON"
    return get_json(urllib.parse.urljoin(BASE, link))
