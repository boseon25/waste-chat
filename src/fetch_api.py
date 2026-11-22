"""
공공데이터포털(data.go.kr) Open API 전체 다운로드 공용 스크립트 → CSV + XLSX

사용법
  pip install requests pandas openpyxl
  python fetch_api.py <요청주소> <저장파일이름> [변수명=값 ...]
  (필수 요청 변수가 있으면 뒤에 '변수명=값'으로 붙임. 변수명은 포털 미리보기/활용가이드에서 확인)

예시
  # 폐기물 적합성 확인
  python fetch_api.py https://apis.data.go.kr/B552584/ifCompatibleInfoService/getList compatible

  # 순환자원 인정업체 정보
  python fetch_api.py https://apis.data.go.kr/B552584/kecoapi/rotatRsrcRcgService/getRotatRsrcRcgBzentyInfo recognized

  → compatible.csv, compatible.xlsx 생성
  ※ 엑셀로 볼 때는 xlsx를 여세요 (CSV를 엑셀로 열면 51-03-01 같은 코드가 날짜로 바뀜)
"""
import os
import sys
import time
import requests
import pandas as pd

SERVICE_KEY = os.environ.get("DATA_GO_KR_KEY", "")   # 마이페이지 > 인증키 발급현황의 '일반 인증키'
NUM_OF_ROWS = 100                    # 한 번에 받을 건수. 일일 트래픽이 적으면 늘리기 (예: 1000)


def find_items(obj):
    """응답 JSON 안에서 레코드 리스트를 찾아 반환."""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            return obj
        for x in obj:
            r = find_items(x)
            if r is not None:
                return r
    if isinstance(obj, dict):
        for k in ("item", "items", "data", "list"):
            if k in obj:
                v = obj[k]
                if isinstance(v, dict) and "item" in v:
                    v = v["item"]
                if isinstance(v, dict):
                    return [v]
                if isinstance(v, list):
                    return v
        for v in obj.values():
            r = find_items(v)
            if r is not None:
                return r
    return None


def find_total(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == "totalcount":
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
            r = find_total(v)
            if r is not None:
                return r
    return None


EXTRA = {}   # 명령줄의 이름=값 인자로 채워짐 (필수 요청 변수)


def fetch_page(endpoint, page):
    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": page,
        "numOfRows": NUM_OF_ROWS,
        "returnType": "json",
        **EXTRA,
    }
    for _ in range(3):
        r = requests.get(endpoint, params=params, timeout=30)
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                raise SystemExit(
                    "JSON이 아닌 응답입니다. 인증키·주소·필수 파라미터를 확인하세요.\n"
                    f"응답 앞부분:\n{r.text[:800]}"
                )
        time.sleep(2)
    raise SystemExit(f"요청 실패 {r.status_code}: {r.text[:800]}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    endpoint, name = sys.argv[1], sys.argv[2]
    for arg in sys.argv[3:]:            # 예: indtyBgngYmd=20200101
        if "=" in arg:
            k, v = arg.split("=", 1)
            EXTRA[k] = v
    if EXTRA:
        print("추가 요청 변수:", EXTRA)

    first = fetch_page(endpoint, 1)
    total = find_total(first)
    rows = find_items(first) or []
    if not rows:
        print("데이터가 비어 있습니다. 응답 원문:")
        print(str(first)[:1500])
        return
    print(f"전체 {total if total is not None else '?'}건, 1페이지 {len(rows)}건")

    page = 2
    while total is None or len(rows) < total:
        items = find_items(fetch_page(endpoint, page)) or []
        if not items:
            break
        rows.extend(items)
        print(f"  {page}페이지 누적 {len(rows)}건")
        page += 1
        time.sleep(0.3)

    df = pd.DataFrame(rows).astype(str).replace({"None": "", "nan": ""})
    df.to_csv(f"{name}.csv", index=False, encoding="utf-8-sig")
    df.to_excel(f"{name}.xlsx", index=False)
    print(f"저장 완료: {name}.csv, {name}.xlsx ({len(df)}건, 컬럼 {len(df.columns)}개)")
    print("컬럼:", ", ".join(df.columns))


if __name__ == "__main__":
    main()