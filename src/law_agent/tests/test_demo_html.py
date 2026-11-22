from law_agent.output import demo_html


def test_build_demo_page():
    case = {"case_id": "C1", "name": "PE 필름", "classification": {"selected_code": "51-03-01", "code_label": "폐합성수지류"},
            "designated_waste_status": "non_designated", "quantity": {"monthly_ton": [0.18, 0.2]},
            "region": {"sido": "전북특별자치도", "sigungu": "전주시"}}
    res = {"case_id": "C1", "preliminary": {"classification": {"normalized_waste_code": "51-03-01"}},
           "routes": [{"partners": {"steps": [{"role": "preprocessor", "total_found": 3,
                                               "candidates": [{"name": "가공A", "sigungu": "전주시", "treatment_codes": ["2010"],
                                                               "location": {"road_km": 6.2}}]}]}}],
           "law_review": [{"question_id": "B-1", "status": "agreed", "answer": "no", "value": {}, "quotes": []}]}
    html = demo_html.build([res], {"C1": case}, "가상 자료")
    assert "분류 에이전트가 준 정보" in html and "51-03-01 폐합성수지류" in html and "190 kg" in html
    assert "추천 업체" in html and "가공A" in html and "6.2 km (도로)" in html and "가상 자료" in html
    assert html.index("추천 업체") < html.index("판단 근거 보기")


def test_funnel_with_reasons():
    res = {"case_id": "C1", "law_review": [], "routes": [{"partners": {"steps": [{
        "role": "preprocessor", "total_found": 10, "rejected_summary": {"처리 방법 다름": 5, "지정/일반 범위 밖": 616},
        "candidates": [{"name": "가공A", "location": {"road_km": 100}}]}]}}]}
    html = demo_html.partners_html(res)
    assert "631곳" in html and "−616곳" in html and "지정폐기물·일반폐기물 허가 구분이 다름" in html
    assert "폐기물관리법 제25조" in html and "도로 거리 (서비스 기준, 법 아님)" in html
    assert html.index("−616곳") < html.index("−5곳")


def test_partner_checklist():
    c = {"name": "가공A", "permit_type": "허가", "waste_group": "04-02 폐유기용제", "treatment_codes": ["2010"],
         "r_codes": ["R-10"], "registration_type": "중간재활용업", "data_date": "2023"}
    rows = demo_html.checks(c, "preprocessor", designated=True)
    marks = {t: m for m, t, _ in rows}
    assert marks["지정폐기물 처리 허가"] == "✓" and marks["재활용 유형 R-10"] == "✓"
    assert marks["허가증(신고증명서) 사본"] == "?" and any("제18조" in b for _, _, b in rows)
    res = {"case_id": "C1", "preliminary": {"waste_status": "designated"}, "law_review": [],
           "routes": [{"partners": {"steps": [{"role": "preprocessor", "total_found": 1, "candidates": [c]}]}}]}
    html = demo_html.partners_html(res)
    assert "확인표 보기" in html and "지정폐기물 처리 허가" in html


def test_partner_checklist_uses_match_evidence():
    c = {"name": "운반A", "evidence": [{"item": "영업 상태", "value": "영업 중", "law": "폐기물관리법 제25조"}],
         "cautions": ["비배출시설계 폐기물만 운반합니다 — 확인"]}
    rows = demo_html.checks(c, "transporter", designated=False)
    assert ("✓", "영업 상태: 영업 중", "폐기물관리법 제25조") in rows and any(m == "!" for m, _, _ in rows)
