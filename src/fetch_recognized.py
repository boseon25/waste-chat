"""
순환자원 인정업체 정보 전체 다운로드 → CSV
(공공데이터포털: 한국환경공단_순환자원 인정업체 정보 조회 서비스)

사용법
  1) pip install requests pandas
  2) 아래 SERVICE_KEY, ENDPOINT 두 줄을 채운다
     - SERVICE_KEY : 마이페이지 > 데이터활용 > Open API > 활용신청 현황 > 일반 인증키(Decoding)
     - ENDPOINT    : 데이터 상세 페이지 '활용명세'의 요청주소 중 "업체 정보" 오퍼레이션 URL
  3) python fetch_recognized.py
  → recognized_companies.csv 생성
"""
import os
import time
import requests
import pandas as pd

SERVICE_KEY = os.environ.get("DATA_GO_KR_KEY", "")   # 마이페이지 > 인증키 발급현황의 '일반 인증키'
ENDPOINT = "https://apis.data.go.kr/B552584/kecoapi/rotatRsrcRcgService/getRotatRsrcRcgBzentyInfo"

NUM_OF_ROWS = 100   # 한 번에 받을 건수 (명세상 최대값이 더 크면 늘려도 됨)
OUT = "recognized_companies.csv"

# 특정 폐기물만 받고 싶으면 명세의 파라미터 이름으로 추가 (선택)
EXTRA_PARAMS = {
    # "대상폐기물명_파라미터": "폐합성수지",
}


def find_items(obj):
    """응답 JSON 안에서 레코드 리스트를 찾아 반환 (포털 API마다 구조가 조금씩 달라서 일반화)."""
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
                return int(v)
            r = find_total(v)
            if r is not None:
                return r
    return None


def fetch_page(page):
    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": page,
        "numOfRows": NUM_OF_ROWS,
        "returnType": "json",
        **EXTRA_PARAMS,
    }
    for attempt in range(3):
        r = requests.get(ENDPOINT, params=params, timeout=30)
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                raise SystemExit(f"JSON이 아닌 응답입니다. 인증키·주소를 확인하세요:\n{r.text[:500]}")
        time.sleep(2)
    raise SystemExit(f"요청 실패 {r.status_code}: {r.text[:500]}")


def main():
    first = fetch_page(1)
    total = find_total(first)
    rows = find_items(first) or []
    if total is None:
        print("totalCount를 못 찾음 → 빈 페이지가 나올 때까지 받습니다.")
    else:
        print(f"전체 {total}건")

    page = 2
    while True:
        if total is not None and len(rows) >= total:
            break
        data = fetch_page(page)
        items = find_items(data) or []
        if not items:
            break
        rows.extend(items)
        print(f"  {page}페이지 누적 {len(rows)}건")
        page += 1
        time.sleep(0.3)   # 트래픽 제한 배려

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")   # 엑셀에서 한글 안 깨지게
    print(f"저장 완료: {OUT} ({len(df)}건, 컬럼 {len(df.columns)}개)")


if __name__ == "__main__":
    main()