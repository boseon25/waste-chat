"""
한국폐기물협회 처리업체 목록(2023 기준) MVP 품목만 한 번에 다운로드
사용법: pip install requests  →  python fetch_kwaste.py  →  ./kwaste/ 폴더에 저장
"""
import os
import time
import requests

BASE = "https://kwaste.or.kr/resources/doc/data02/"
FILES = {
    # 재활용업
    "4_1.xlsx":  "재활용업_전체",
    "4_48.xlsx": "재활용업_폐합성고분자화합물",
    "4_28.xlsx": "재활용업_폐식용유",
    "4_10.xlsx": "재활용업_왕겨및쌀겨",
    "4_15.xlsx": "재활용업_폐금속류",
    "4_31.xlsx": "재활용업_폐유",
    "4_3.xlsx":  "재활용업_동식물성잔재물",
    "4_20.xlsx": "재활용업_폐목재류",
    # 처리신고
    "5_1.xlsx":  "처리신고_전체",
    "5_37.xlsx": "처리신고_폐합성고분자화합물",
    "5_21.xlsx": "처리신고_폐식용유",
    "5_9.xlsx":  "처리신고_왕겨및쌀겨",
    "5_12.xlsx": "처리신고_폐금속류",
    "5_22.xlsx": "처리신고_폐유",
    "5_3.xlsx":  "처리신고_동식물성잔재물",
    "5_15.xlsx": "처리신고_폐목재류",
    # 소각·매립
    "2_1.xlsx":  "중간처분업_소각_생활사업장일반",
    "3_1.xlsx":  "최종처분업_매립_일반건설",
}

os.makedirs("kwaste", exist_ok=True)
headers = {"User-Agent": "Mozilla/5.0"}
for fname, label in FILES.items():
    out = os.path.join("kwaste", f"{label}.xlsx")
    try:
        r = requests.get(BASE + fname, headers=headers, timeout=60)
        r.raise_for_status()
        with open(out, "wb") as f:
            f.write(r.content)
        print(f"OK   {label}  ({len(r.content)//1024} KB)")
    except Exception as e:
        print(f"FAIL {label}: {e}")
    time.sleep(0.5)
print("완료: ./kwaste/")
