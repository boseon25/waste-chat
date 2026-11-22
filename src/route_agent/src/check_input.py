"""파트 1 입력(WasteCase JSON) 점검 — pipeline 돌리기 전에 칸이 다 왔는지 본다.

    python3 check_input.py 파트1출력.json

❌ 필수 칸 없음 (판정이 안 되거나 틀림) / ⚠ 있으면 좋은 칸 없음 (확인 필요·질문으로 나감) / ✅ 있음
종료 코드: 0 필수 칸 모두 있음 / 1 필수 칸 빠짐 / 2 파일을 읽을 수 없음
"""
import json
import sys
from pathlib import Path

# (칸 경로, 설명, 필수 여부)
FIELDS = [
    ("case_id", "폐기물 번호", True),
    ("case_version", "몇 번째 고친 정보인지", True),
    ("classification.selected_code", "분류번호 (별표 4)", True),
    ("quantity.monthly_ton", "월 배출량 목록 (톤)", True),
    ("region.sido", "시도", True),
    ("region.sigungu", "시군구 (업체 거리 계산)", True),
    ("designated_waste_status", "지정폐기물 여부", False),
    ("material_form", "형태 (고상·액상, 원형·파쇄 등)", False),
    ("components", "성분 (혼합 여부)", False),
    ("mixed_with_other", "다른 폐기물과 섞였는지", False),
    ("foreign_matter.items", "이물질", False),
    ("contamination.level", "오염 정도", False),
    ("combustible", "가연성", False),
    ("analysis.heating_value_kcal", "저위발열량 (에너지 회수 판정)", False),
    ("analysis.leaching_ok", "용출시험 결과 (지정폐기물 판정)", False),
    ("storage_state", "보관 상태", False),
    ("item_specific", "품목 특이사항 (순환자원 판정)", False),
    ("existing_declaration.treatment_code", "기존 신고 처리방법", False),
]


def get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def empty(v):
    return v is None or v == "" or v == [] or v == {}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    p = Path(argv[1])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"❌ 파일을 읽을 수 없습니다: {p} ({e})")
        return 2
    cases = data.get("cases", [data]) if isinstance(data, dict) else data
    print(f"파일: {p}")
    print(f"형식: schema_version={data.get('schema_version')}  synthetic={data.get('synthetic')}  사례 {len(cases)}건\n")
    bad_total = 0
    for c in cases:
        bad, warn = [], []
        print(f"■ {c.get('case_id')}  {c.get('name') or ''}")
        for path, label, must in FIELDS:
            v = get(c, path)
            if empty(v):
                (bad if must else warn).append(path)
                mark = "❌" if must else "⚠ "
                print(f"   {mark} {path:<38} {label}")
        code = get(c, "classification.selected_code") or ""
        if code and not (len(code) == 8 and code[2] == "-" and code[5] == "-"):
            bad.append("classification.selected_code")
            print(f"   ❌ 분류번호 형식이 NN-NN-NN 이 아님: {code}")
        mt = get(c, "quantity.monthly_ton")
        if mt and not all(isinstance(x, (int, float)) for x in mt):
            bad.append("quantity.monthly_ton")
            print(f"   ❌ 월 배출량에 숫자가 아닌 값: {mt}")
        if c.get("missing"):
            print(f"   · 파트 1이 '모름'으로 보낸 칸: {', '.join(map(str, c['missing']))}")
        print(f"   → 필수 {'모두 있음' if not bad else str(len(bad)) + '개 빠짐'}, 확인 필요 {len(warn)}개"
              f"  (분류번호 {code or '-'}, 월 평균 "
              f"{round(sum(mt) / len(mt), 3) if mt and not bad else '-'}톤, "
              f"{get(c, 'region.sido') or ''} {get(c, 'region.sigungu') or ''})\n")
        bad_total += len(bad)
    print("결과:", "pipeline 실행 가능" if not bad_total else "필수 칸이 빠져 있음 → 파트 1에 다시 요청")
    return 0 if not bad_total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
