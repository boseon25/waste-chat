# route_agent/src/doc_review/build_requirements.py 에서 복사 (2026.11.19). 기준표 원문 대조(--verify)·확정(--confirm)
"""doc_requirements.csv 초안 만들기 (서류 검토 기준표).

한 행 = 서류 하나의 확인 항목 하나. 판정은 review/rules.py 가 이 표대로 한다.
quote 는 법 원문(조문·별지 서식)에 글자 그대로 있어야 한다 (공백만 무시). status 는 draft 로 시작하고
law_api review_tables 대조 + 사람 확인 후 confirmed.

check 종류
  guide        검토하지 않고 작성 안내에만 쓰는 행 (2026.11.19: 칸마다 법 기준을 보여 주려고)
  required     field 값이 있어야 함 ([] 는 목록 각 항목마다)
  enum         field 값이 param("|"로 구분) 중 하나
  number       field 가 0보다 큰 숫자
  date_order   param 의 두 날짜(시작|끝)가 순서대로
  deadline     param = 기준날짜칸|제출날짜칸|일수 : 제출일이 기준일 + 일수 이내
  attachment   attachments 에 param 이 있어야 함
  in_route     field 값이 경로·사례 값(param: waste_codes | treatment_codes) 안에 있어야 함
  partner      field(수탁자)가 경로의 추천·예비 업체 중 하나
  approx       field 값이 사례 물량(param: ton_year | kg_month)과 비슷 (±30%)
  info         항상 안내 (확인은 사람이)
when: 이 행을 적용하는 조건 ("필드==값", "필드!=값"). 비면 항상.
severity: error(법 필수 항목 빠짐·위반) / warning(사례·경로와 다름, 확인 필요) / info(안내)
"""
import csv
import sys
from pathlib import Path

D = "20261112"
K1613 = f"WMA_RULE:16의13/호{{}}@{D}"
K182 = f"WMA_RULE:18의2/항{{}}@{D}"
BF5 = f"WMA_RULE:BF5@{D}"
BF12 = f"WMA_RULE:BF12@{D}"
BF6 = f"WMA_RULE:BF6@{D}"          # 2026.11.19 추가: 배출자 신고서 (별지 제6호서식)
BF144 = f"WMA_RULE:BF14의4@{D}"    # 2026.11.19 추가: 폐기물 유해성 정보자료 (별지 제14호의4서식)

Q_CONTRACT = "수탁자와 서면으로 다음 각 목의 사항이 포함된 위탁계약을 체결할 것"
Q_KIND = "가. 위탁하는 폐기물의 종류 및 수량"
Q_TERM = "나. 계약 기간 및 위탁 비용"
Q_PROP = "다. 위탁하는 폐기물의 성질과 상태 및 취급 시 주의사항"
Q_CC = "다음 각 목의 서류를 포함한 별지 제5호서식의 수탁처리능력 확인서를 수탁자로부터 제출받을 것"
Q_PERMIT = "가. 폐기물처리업 허가증 또는 폐기물처리 신고증명서 사본"
Q_GUAR = "나. 법 제40조제1항에 따른 방치폐기물 처리이행보증을 확인할 수 있는 서류 사본"
Q_LEGAL = "적법한 수탁자에 해당하는지를 확인할 것"
Q_KEEP = "위탁계약서를 계약 체결일부터 3년간 보관할 것"

ROWS = [
    # ── 위탁계약서 (시행규칙 제16조의13 제3·4호) ──
    ("EC-01", "entrust_contract", "위탁자", "consignor.name", "required", "", "", "error", "위탁자(배출자) 상호가 없습니다", K1613.format(3), Q_CONTRACT),
    ("EC-02", "entrust_contract", "수탁자", "consignee.name", "required", "", "", "error", "수탁자(처리 업체) 상호가 없습니다", K1613.format(3), Q_CONTRACT),
    ("EC-03", "entrust_contract", "폐기물 종류", "waste_items[].waste_code", "required", "", "", "error", "위탁하는 폐기물의 종류(분류번호)가 없습니다", K1613.format(3), Q_KIND),
    ("EC-04", "entrust_contract", "폐기물 수량", "waste_items[].quantity_ton_year", "number", "", "", "error", "위탁하는 폐기물의 수량(톤/년)이 없습니다", K1613.format(3), Q_KIND),
    ("EC-05", "entrust_contract", "계약 기간", "contract.start_date", "required", "", "", "error", "계약 시작일이 없습니다", K1613.format(3), Q_TERM),
    ("EC-06", "entrust_contract", "계약 기간", "contract.end_date", "required", "", "", "error", "계약 종료일이 없습니다", K1613.format(3), Q_TERM),
    ("EC-07", "entrust_contract", "계약 기간", "", "date_order", "contract.start_date|contract.end_date", "", "error", "계약 종료일이 시작일보다 빠릅니다", K1613.format(3), Q_TERM),
    ("EC-08", "entrust_contract", "위탁 비용", "contract.fee", "required", "", "", "error", "위탁 비용이 없습니다", K1613.format(3), Q_TERM),
    ("EC-09", "entrust_contract", "성질·상태", "waste_items[].properties", "required", "", "", "error", "위탁하는 폐기물의 성질과 상태가 없습니다", K1613.format(3), Q_PROP),
    ("EC-10", "entrust_contract", "취급 시 주의사항", "waste_items[].handling_cautions", "required", "", "", "error", "취급 시 주의사항이 없습니다", K1613.format(3), Q_PROP),
    ("EC-11", "entrust_contract", "서면 체결", "contract.signed_date", "required", "", "", "error", "서면 계약 체결일(서명·날인)이 없습니다", K1613.format(3), Q_CONTRACT),
    ("EC-12", "entrust_contract", "수탁처리능력 확인서", "", "attachment", "capacity_check", "", "error", "계약 전에 수탁자로부터 수탁처리능력 확인서(별지 제5호서식)를 받아야 합니다", K1613.format(1), Q_CC),
    ("EC-13", "entrust_contract", "경로 일치", "waste_items[].waste_code", "in_route", "waste_codes", "", "warning", "이 경로의 폐기물 분류번호와 다릅니다", K1613.format(3), Q_KIND),
    ("EC-14", "entrust_contract", "경로 일치", "consignee.name", "partner", "", "", "warning", "추천·예비 업체 목록에 없는 수탁자입니다. 허가·신고 범위를 직접 확인하세요", K1613.format(2), Q_LEGAL),
    ("EC-15", "entrust_contract", "경로 일치", "waste_items[].treatment_code", "in_route", "treatment_codes", "", "warning", "이 경로의 처리방법 코드와 다릅니다", K1613.format(3), Q_KIND),
    ("EC-16", "entrust_contract", "물량", "waste_items[].quantity_ton_year", "approx", "ton_year", "", "warning", "사례 배출량(연 환산)과 30% 넘게 다릅니다", K1613.format(3), Q_KIND),
    ("EC-17", "entrust_contract", "적법 수탁자 확인", "", "info", "", "", "info", "폐기물적정처리추진센터 공개 자료 등으로 적법한 수탁자인지 확인하세요", K1613.format(2), Q_LEGAL),
    ("EC-18", "entrust_contract", "보관", "", "info", "", "", "info", "위탁계약서는 계약 체결일부터 3년간 보관해야 합니다", K1613.format(4), Q_KEEP),

    # ── 수탁처리능력 확인서 (별지 제5호서식, 시행규칙 제16조의13 제1호) ──
    ("CC-01", "capacity_check", "위탁자", "consignor.name", "required", "", "", "error", "①위탁자 상호가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("CC-02", "capacity_check", "위탁자", "consignor.biz_no", "required", "", "", "error", "②위탁자 사업자등록번호가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("CC-03", "capacity_check", "위탁자", "consignor.representative", "required", "", "", "error", "③위탁자 대표자가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("CC-04", "capacity_check", "위탁자", "consignor.address", "required", "", "", "error", "⑤위탁자 주소가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("CC-05", "capacity_check", "위탁 폐기물", "waste_items[].waste_code", "required", "", "", "error", "⑥위탁 폐기물 분류번호가 없습니다", BF5, "사업장폐기물의 명칭과 분류번호를 적고"),
    ("CC-06", "capacity_check", "위탁 폐기물", "waste_items[].state", "enum", "1|2|기타", "", "error", "⑥성질·상태는 고체 \"1\", 액체 \"2\", 지정폐기물 함유 용기 \"기타\"로 적어야 합니다", BF5, "고체상태이면 \"1\"로, 액체상태이면 \"2\"로"),
    ("CC-07", "capacity_check", "위탁 폐기물", "waste_items[].ton_year", "number", "", "", "error", "⑥위탁량(톤/년)이 없습니다", BF5, "위탁량(톤/년)란에는 연간 위탁처리하는 폐기물의 양을 톤"),
    ("CC-08", "capacity_check", "수탁자", "consignee.name", "required", "", "", "error", "⑦수탁자 상호가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-09", "capacity_check", "수탁자", "consignee.biz_no", "required", "", "", "error", "⑧수탁자 사업자등록번호가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-10", "capacity_check", "수탁자", "consignee.address", "required", "", "", "error", "⑪수탁자 주소가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-11", "capacity_check", "허가·신고", "consignee.permit_no", "required", "", "", "error", "⑭폐기물처리업 허가(신고) 번호가 없습니다", BF5, "⑭폐기물처리업 허가 또는 폐기물처리 신고 관련 사항"),
    ("CC-12", "capacity_check", "허가·신고", "consignee.permit_agency", "required", "", "", "error", "⑭허가(신고수리) 기관이 없습니다", BF5, "⑭폐기물처리업 허가 또는 폐기물처리 신고 관련 사항"),
    ("CC-13", "capacity_check", "수탁 가능 여부", "capacity.acceptable", "enum", "가능", "", "error", "수탁 가능 여부가 \"가능\"이 아닙니다", BF5, "\"가능 또는 불가능\"으로 적습니다"),
    ("CC-14", "capacity_check", "첨부", "", "attachment", "permit_copy", "", "error", "폐기물처리업 허가증 또는 신고증명서 사본이 첨부되지 않았습니다", K1613.format(1), Q_PERMIT),
    ("CC-15", "capacity_check", "첨부", "", "attachment", "guarantee_copy", "", "warning", "방치폐기물 처리이행보증 확인 서류 사본이 없습니다 (배출자 신고 대상 사업장이면 필요)", K1613.format(1), Q_GUAR),
    ("CC-16", "capacity_check", "경로 일치", "waste_items[].waste_code", "in_route", "waste_codes", "", "warning", "이 경로의 폐기물 분류번호와 다릅니다", BF5, "사업장폐기물의 명칭과 분류번호를 적고"),
    ("CC-17", "capacity_check", "경로 일치", "consignee.name", "partner", "", "", "warning", "추천·예비 업체 목록에 없는 수탁자입니다", K1613.format(2), Q_LEGAL),
    ("CC-18", "capacity_check", "경로 일치", "treatment_items[].treatment_code", "in_route", "treatment_codes", "", "warning", "수탁자의 처리방법 코드가 이 경로의 처리방법과 다릅니다", BF5, "처분 또는 재활용 방법란에는 아래 참고란의 폐기물처리 방법별 분류에 따른 분류번호를 적으며"),
    ("CC-19", "capacity_check", "물량", "waste_items[].ton_year", "approx", "ton_year", "", "warning", "사례 배출량(연 환산)과 30% 넘게 다릅니다", BF5, "위탁량(톤/년)란에는 연간 위탁처리하는 폐기물의 양을 톤"),

    # ── 폐기물 처리계획서 (별지 제12호서식, 시행규칙 제18조의2) ──
    ("TP-01", "treatment_plan", "제출인", "submitter.name", "required", "", "", "error", "①상호가 없습니다", K182.format(4), "폐기물처리계획서는 별지 제12호서식에 따르고"),
    ("TP-02", "treatment_plan", "제출인", "submitter.biz_no", "required", "", "", "error", "②사업자등록번호가 없습니다", K182.format(4), "폐기물처리계획서는 별지 제12호서식에 따르고"),
    ("TP-03", "treatment_plan", "제출인", "submitter.address", "required", "", "", "error", "⑤사업장 주소가 없습니다", K182.format(4), "폐기물처리계획서는 별지 제12호서식에 따르고"),
    ("TP-04", "treatment_plan", "사업장 현황", "business.industry", "required", "", "", "error", "⑥업종(한국표준산업분류 세세분류)이 없습니다", BF12, "⑥란은 한국표준산업분류(세세분류)에 따른 업종명을 적습니다"),
    ("TP-05", "treatment_plan", "사업장 현황", "business.main_materials", "required", "", "", "error", "⑦주 원료명 및 사용량이 없습니다", K182.format(3), "1. 주 원료명 및 사용량"),
    ("TP-06", "treatment_plan", "사업장 현황", "business.main_products", "required", "", "", "error", "⑧주 생산품명 및 생산량이 없습니다", K182.format(3), "2. 주 생산품명 및 생산량"),
    ("TP-07", "treatment_plan", "사업장 현황", "business.process", "required", "", "", "error", "⑨제조공정이 없습니다", K182.format(3), "3. 제조공정"),
    ("TP-08", "treatment_plan", "폐기물", "waste_items[].waste_code", "required", "", "", "error", "⑪분류번호(지정폐기물 세분류)가 없습니다", BF12, "⑪란은 「폐기물관리법 시행규칙」 별표 4 지정폐기물의 분류번호를 세분류로 적습니다"),
    ("TP-09", "treatment_plan", "폐기물", "waste_items[].kg_month", "number", "", "", "error", "⑫배출량(kg/월)이 없습니다", BF12, "⑫ 배출량"),
    ("TP-10", "treatment_plan", "폐기물", "waste_items[].ton_year", "number", "", "", "error", "⑫배출량(톤/년)이 없습니다", BF12, "⑫ 배출량"),
    ("TP-11", "treatment_plan", "폐기물", "waste_items[].cycle", "required", "", "", "error", "⑬배출주기가 없습니다", BF12, "⑬란은 배출주기를 일별ㆍ월별ㆍ계절별로 구분하여 적고"),
    ("TP-12", "treatment_plan", "폐기물", "waste_items[].state", "enum", "고상|액상|기타", "", "error", "⑭성질·상태는 \"고상\", \"액상\", \"기타\" 중 하나여야 합니다", BF12, "⑭란은 고체상태이면 \"고상\"으로, 액체상태이면 \"액상\"으로"),
    ("TP-13", "treatment_plan", "운반계획", "waste_items[].transporter", "required", "", "", "error", "⑮운반자(자가 또는 위탁 업소명)가 없습니다", BF12, "⑮란의 운반자란에는 자가운반의 경우는 \"자가\"로"),
    ("TP-14", "treatment_plan", "처리계획", "waste_items[].treatment_type", "enum", "자가|위탁", "", "error", "처리구분은 \"자가\" 또는 \"위탁\"이어야 합니다", BF12, "처리구분란에는 자가처리의 경우는 \"자가\"로, 위탁처리의 경우는 \"위탁\"으로 적고"),
    ("TP-15", "treatment_plan", "처리계획", "waste_items[].treatment_company", "required", "", "waste_items[].treatment_type==위탁", "error", "위탁 처리 업소명이 없습니다", BF12, "처리구분란에는 자가처리의 경우는 \"자가\"로, 위탁처리의 경우는 \"위탁\"으로 적고"),
    ("TP-16", "treatment_plan", "처리계획", "waste_items[].treatment_code", "required", "", "", "error", "처리방법(처리분류·세분류 번호)이 없습니다", BF12, "처리방법란에는 처리분류와 세분류 번호를 모두 적습니다"),
    ("TP-17", "treatment_plan", "첨부", "", "attachment", "capacity_check", "waste_items[].treatment_type==위탁", "error", "위탁 처리이면 수탁처리능력 확인서(별지 제5호서식)를 첨부해야 합니다", BF12, "1. 수탁처리능력확인서(사업장에서 발생하는 폐기물을 위탁하여 처리하는 경우만 해당합니다)"),
    ("TP-18", "treatment_plan", "첨부", "", "attachment", "analysis_result", "", "error", "폐기물 분석결과서(별지 제13호서식)가 없습니다", K182.format(4), "폐기물 분석결과서는 별지 제13호서식에 따르며"),
    ("TP-19", "treatment_plan", "제출 기한", "", "deadline", "start_date|submit_date|30", "", "error", "사업 개시일 또는 폐기물 발생일부터 1개월 안에 제출해야 합니다", K182.format(5), "사업 개시일 또는 폐기물이 발생한 날부터 1개월 이내에"),
    ("TP-20", "treatment_plan", "제출처", "", "info", "", "", "info", "폐기물 발생지를 관할하는 시·도지사 또는 지방환경관서의 장에게 제출합니다", K182.format(5), "관할하는 시ㆍ도지사 또는 지방환경관서의 장에게 제출하여야 한다"),
    ("TP-21", "treatment_plan", "경로 일치", "waste_items[].waste_code", "in_route", "waste_codes", "", "warning", "이 경로의 폐기물 분류번호와 다릅니다", BF12, "⑪란은 「폐기물관리법 시행규칙」 별표 4 지정폐기물의 분류번호를 세분류로 적습니다"),
    ("TP-22", "treatment_plan", "경로 일치", "waste_items[].treatment_code", "in_route", "treatment_codes", "", "warning", "이 경로의 처리방법 코드와 다릅니다", BF12, "처리방법란에는 처리분류와 세분류 번호를 모두 적습니다"),
    ("TP-23", "treatment_plan", "경로 일치", "waste_items[].treatment_company", "partner", "", "waste_items[].treatment_type==위탁", "warning", "추천·예비 업체 목록에 없는 처리 업소입니다", BF12, "처리구분란에는 자가처리의 경우는 \"자가\"로, 위탁처리의 경우는 \"위탁\"으로 적고"),
    ("TP-24", "treatment_plan", "물량", "waste_items[].kg_month", "approx", "kg_month", "", "warning", "사례 월 평균 배출량과 30% 넘게 다릅니다", BF12, "⑫ 배출량"),
    # ── 2026.11.19 추가: 사업장폐기물배출자 신고서 (별지 제6호서식, 시행규칙 제18조제2항제1호) ──
    # 인용은 waste-chat 동봉 원본 PDF(개정 2021.7.6)의 서식·작성방법 글자. --verify 로 서버 원문과 대조 후 확정
    # (11.19 서버 대조: 표 칸 줄바꿈(│)을 넘는 인용, 원문에서 ?로 깨진 ⑯⑰ 번호는 빼고 인용 — ER-18·19·24, HZ-13·14)
    ("ER-01", "emitter_report", "신고인", "reporter.name", "required", "", "", "error", "①신고인 상호(명칭)가 없습니다", BF6, "① 상호(명칭)"),
    ("ER-02", "emitter_report", "신고인", "reporter.biz_no", "required", "", "", "error", "②사업자등록번호가 없습니다", BF6, "② 사업자등록번호"),
    ("ER-03", "emitter_report", "신고인", "reporter.representative", "required", "", "", "error", "③대표자 성명이 없습니다", BF6, "③ 성명(대표자)"),
    ("ER-04", "emitter_report", "신고인", "reporter.address", "required", "", "", "error", "⑤사업장 주소가 없습니다", BF6, "⑤ 주소(사업장)"),
    ("ER-05", "emitter_report", "사업장 현황", "business.industry", "required", "", "", "error", "⑥업종(한국표준산업분류 소분류)이 없습니다", BF6, "⑥란은 한국표준산업분류(소분류)에 따른 업종명을 적습니다"),
    ("ER-06", "emitter_report", "사업장 현황", "business.main_materials", "required", "", "", "error", "⑦주 원료명 및 사용량(톤/년)이 없습니다", BF6, "⑦ 주 원료명 및 사용량(톤/년)"),
    ("ER-07", "emitter_report", "사업장 현황", "business.main_products", "required", "", "", "error", "⑧주 생산품명 및 생산량(톤/년)이 없습니다", BF6, "⑧ 주 생산품명 및 생산량(톤/년)"),
    ("ER-08", "emitter_report", "사업장 현황", "business.process", "required", "", "", "error", "⑨제조공정이 없습니다", BF6, "⑨란은 원료의 저장 및 투입 시부터 최종 제품의 출하 시까지의 모든 공정을 작업단위별로 작성하되"),
    ("ER-09", "emitter_report", "폐기물", "waste_items[].category", "enum", "1|2", "", "error", "⑩구분은 사업장비배출시설계 \"1\", 사업장배출시설계 \"2\"로 적어야 합니다", BF6, "구분란은 사업장비(非)배출시설계 폐기물이면 \"1\"로, 사업장배출시설계 폐기물이면 \"2\"로 구분하여 적고"),
    ("ER-10", "emitter_report", "폐기물", "waste_items[].waste_code", "required", "", "", "error", "⑩폐기물 분류번호(세분류)가 없습니다", BF6, "별표 4에 따른 사업장폐기물의 종류와 분류번호를 세분류로 적습니다"),
    ("ER-11", "emitter_report", "폐기물", "waste_items[].state", "enum", "1|2", "", "error", "⑪성질·상태는 고체 \"1\", 액체 \"2\"로 적어야 합니다", BF6, "고체상태이면 \"1\"로, 액체상태이면 \"2\"로 구분하여 적습니다"),
    ("ER-12", "emitter_report", "배출량", "waste_items[].ton_month", "number", "", "", "error", "⑫배출량(톤/월)이 없습니다", BF6, "⑫ 배출량"),
    ("ER-13", "emitter_report", "배출량", "waste_items[].ton_year", "number", "", "", "error", "⑫배출량(톤/년)이 없습니다", BF6, "⑫ 배출량"),
    ("ER-14", "emitter_report", "운반", "waste_items[].transporter", "required", "", "", "error", "⑬운반자(자가 또는 위탁 업소명)가 없습니다", BF6, "⑬란의 운반자란에는 자가운반의 경우는 \"자가\"로, 위탁운반의 경우는 그 업소명을 적습니다"),
    ("ER-15", "emitter_report", "처리", "waste_items[].treatment_type", "enum", "자가|위탁", "", "error", "⑭처리구분은 \"자가\" 또는 \"위탁\"이어야 합니다", BF6, "처리구분란에는 자가처리의 경우에는 \"자가\"로, 위탁처리의 경우에는 \"위탁\"으로 적고"),
    ("ER-16", "emitter_report", "처리", "waste_items[].treatment_company", "required", "", "waste_items[].treatment_type==위탁", "error", "⑭위탁 처리 업소명이 없습니다", BF6, "처리구분란에는 자가처리의 경우에는 \"자가\"로, 위탁처리의 경우에는 \"위탁\"으로 적고"),
    ("ER-17", "emitter_report", "처리", "waste_items[].treatment_code", "required", "", "", "error", "⑭처리방법(처리분류·세분류 번호)이 없습니다", BF6, "처리방법란에는 처리분류와 세분류 번호를 모두 적습니다"),
    ("ER-18", "emitter_report", "처리", "waste_items[].treatment_ton_year", "number", "", "", "error", "⑭처리량(톤/년)이 없습니다", BF6, "처리량"),
    ("ER-19", "emitter_report", "첨부", "", "attachment", "capacity_check", "waste_items[].treatment_type==위탁", "error", "위탁 처리이면 수탁처리능력확인서를 첨부해야 합니다", BF6, "수탁처리능력확인서(사업장에서 발생하는 폐기물을 위탁하여 처리하는 경우만 해당"),
    ("ER-20", "emitter_report", "경로 일치", "waste_items[].waste_code", "in_route", "waste_codes", "", "warning", "이 경로의 폐기물 분류번호와 다릅니다", BF6, "별표 4에 따른 사업장폐기물의 종류와 분류번호를 세분류로 적습니다"),
    ("ER-21", "emitter_report", "경로 일치", "waste_items[].treatment_code", "in_route", "treatment_codes", "", "warning", "이 경로의 처리방법 코드와 다릅니다", BF6, "처리방법란에는 처리분류와 세분류 번호를 모두 적습니다"),
    ("ER-22", "emitter_report", "경로 일치", "waste_items[].treatment_company", "partner", "", "waste_items[].treatment_type==위탁", "warning", "추천·예비 업체 목록에 없는 처리 업소입니다", BF6, "처리구분란에는 자가처리의 경우에는 \"자가\"로, 위탁처리의 경우에는 \"위탁\"으로 적고"),
    ("ER-23", "emitter_report", "물량", "waste_items[].ton_year", "approx", "ton_year", "", "warning", "사례 배출량(연 환산)과 30% 넘게 다릅니다", BF6, "⑫ 배출량"),
    ("ER-24", "emitter_report", "분석결과서", "", "info", "", "", "info", "지정폐기물 여부를 미리 확인해야 하는 배출자는 폐기물분석전문기관의 분석결과서를 첨부합니다", BF6, "폐기물분석전문기관이 작성한 폐기물분석결과서(제17조의2제2항에 따라"),
    ("ER-25", "emitter_report", "제출처", "", "info", "", "", "info", "관할 특별자치시장·특별자치도지사·시장·군수·구청장에게 신고합니다 (처리기간 5일, 신고증명서 발급)", BF6, "특별자치시장, 특별자치도지사, 시장ㆍ군수ㆍ구청장"),
    # ── 2026.11.19 추가: 폐기물 수탁확인서 (별지 제5호서식의 수탁확인 용도, 지정폐기물 처리계획 첨부) ──
    ("AC-01", "acceptance_check", "위탁자", "consignor.name", "required", "", "", "error", "①위탁자 상호가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("AC-02", "acceptance_check", "위탁자", "consignor.biz_no", "required", "", "", "error", "②위탁자 사업자등록번호가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("AC-03", "acceptance_check", "위탁자", "consignor.representative", "required", "", "", "error", "③위탁자 대표자가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("AC-04", "acceptance_check", "위탁자", "consignor.address", "required", "", "", "error", "⑤위탁자 주소가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("AC-05", "acceptance_check", "위탁 폐기물", "waste_items[].waste_code", "required", "", "", "error", "⑥위탁 폐기물 분류번호가 없습니다", BF5, "사업장폐기물의 명칭과 분류번호를 적고"),
    ("AC-06", "acceptance_check", "수탁자", "consignee.name", "required", "", "", "error", "⑦수탁자 상호가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("AC-07", "acceptance_check", "수탁자", "consignee.biz_no", "required", "", "", "error", "⑧수탁자 사업자등록번호가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("AC-08", "acceptance_check", "수탁자", "consignee.representative", "required", "", "", "error", "⑨수탁자 대표자가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("AC-09", "acceptance_check", "수탁자", "consignee.industry", "required", "", "", "error", "⑫수탁자 업종(한국표준산업분류 대분류)이 없습니다", BF5, "⑫란은 한국표준산업분류의 대분류에 따른 업종명을"),
    ("AC-10", "acceptance_check", "수탁 확인", "accept_items[].treatment_code", "required", "", "", "error", "(4)처분 또는 재활용 방법(분류번호)이 없습니다", BF5, "처분 또는 재활용 방법란에는 아래 참고란의 폐기물처리 방법별 분류에 따른 분류번호를 적으며"),
    ("AC-11", "acceptance_check", "수탁 확인", "accept_items[].facility", "required", "", "", "error", "(4)처분시설 또는 재활용시설이 없습니다", BF5, "폐기물 처분시설 또는 재활용시설란에는 그 시설을 구체적으로 적고"),
    ("AC-12", "acceptance_check", "수탁 확인", "accept_items[].ton_year", "number", "", "", "error", "(4)수탁(예정)량(톤/년)이 없습니다", BF5, "수탁(예정)량란에는 1년 동안 수탁받을 폐기물 예정량을 적으며"),
    ("AC-13", "acceptance_check", "수탁 확인", "accept_items[].schedule", "required", "", "", "error", "(4)수탁(예정)방법(톤/회, 회/년)이 없습니다", BF5, "수탁(예정)방법란에는 1회에 수탁받을 폐기물 예정량과 1년 동안 수탁받을 예정횟수를 적습니다"),
    ("AC-14", "acceptance_check", "작성 범위", "", "info", "", "", "info", "수탁확인은 (1)·(2)·(4)란을 적고, 수탁처리능력 확인과 함께 하면 (1)~(4)란을 모두 적습니다", BF5, "폐기물 수탁확인 시에는 해당 [ ]에 표시를 하고 (1)란, (2)란 및 (4)란을 적되"),
    ("AC-15", "acceptance_check", "경로 일치", "waste_items[].waste_code", "in_route", "waste_codes", "", "warning", "이 경로의 폐기물 분류번호와 다릅니다", BF5, "사업장폐기물의 명칭과 분류번호를 적고"),
    ("AC-16", "acceptance_check", "경로 일치", "consignee.name", "partner", "", "", "warning", "추천·예비 업체 목록에 없는 수탁자입니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("AC-17", "acceptance_check", "경로 일치", "accept_items[].treatment_code", "in_route", "treatment_codes", "", "warning", "이 경로의 처리방법 코드와 다릅니다", BF5, "처분 또는 재활용 방법란에는 아래 참고란의 폐기물처리 방법별 분류에 따른 분류번호를 적으며"),
    ("AC-18", "acceptance_check", "물량", "accept_items[].ton_year", "approx", "ton_year", "", "warning", "사례 배출량(연 환산)과 30% 넘게 다릅니다", BF5, "수탁(예정)량란에는 1년 동안 수탁받을 폐기물 예정량을 적으며"),
    # ── 2026.11.19 추가: 폐기물 유해성 정보자료 (별지 제14호의4서식) ──
    ("HZ-01", "hazard_info", "관리번호", "record_no", "required", "", "", "error", "①관리번호가 없습니다", BF144, "①란은 사업장폐기물배출자(제공자)가 폐기물 유해성 정보자료를 관리하는 관리번호를 적습니다"),
    ("HZ-02", "hazard_info", "작성일·제공일", "provided_date", "required", "", "", "error", "②작성일(제공일)이 없습니다", BF144, "②란은 배출자의 유해성 정보자료 작성일(또는 전문기관의 작성일) 및 수탁자에게 제공한 날짜를 적습니다"),
    ("HZ-03", "hazard_info", "제공자", "provider.name", "required", "", "", "error", "③제공자 상호가 없습니다", BF144, "③ 상호(명칭)"),
    ("HZ-04", "hazard_info", "제공자", "provider.biz_no", "required", "", "", "error", "④제공자 사업자등록번호가 없습니다", BF144, "④ 사업자등록번호"),
    ("HZ-05", "hazard_info", "제공자", "provider.representative", "required", "", "", "error", "⑤제공자 대표자가 없습니다", BF144, "⑤ 성명(대표자)"),
    ("HZ-06", "hazard_info", "제공자", "provider.address", "required", "", "", "error", "⑦제공자 주소가 없습니다", BF144, "⑦ 주소(사업장)"),
    ("HZ-07", "hazard_info", "폐기물", "waste.waste_code", "required", "", "", "error", "⑧폐기물 명칭과 분류번호가 없습니다", BF144, "⑧란은 「폐기물관리법 시행규칙」 별표 4 폐기물의 종류별 세부분류에 따른 폐기물 명칭과 분류번호를 적습니다"),
    ("HZ-08", "hazard_info", "포장형태", "waste.packaging", "required", "", "", "error", "⑨포장형태가 없습니다", BF144, "⑨란은 폐기물을 수탁자에게 위탁하여 수집ㆍ운반 차량에 적재하는 당시 폐기물의 포장형태를 적습니다"),
    ("HZ-09", "hazard_info", "발생량", "waste.ton_year", "number", "", "", "error", "⑩발생량(톤/년)이 없습니다", BF144, "⑩ 발생량(톤/년)"),
    ("HZ-10", "hazard_info", "제조공정", "waste.process", "required", "", "", "error", "⑪제조공정이 없습니다", BF144, "⑪란은 원료의 저장 및 투입시부터 제품의 출하 시까지의 모든 공정을 작업단위별로 작성하되"),
    ("HZ-11", "hazard_info", "유해특성", "hazard.properties", "required", "", "", "error", "⑫유해특성(해당 없으면 \"없음\")이 없습니다", BF144, "⑫란은 「폐기물관리법 시행령」 별표 4의2에 따른 폐기물의 유해특성 해당 여부를 확인하고"),
    ("HZ-12", "hazard_info", "용출독성", "hazard.leaching", "required", "", "", "error", "⑭유해물질 용출독성이 없습니다", BF144, "⑭란은 지정폐기물 처리계획 확인(변경확인)시 분석한 결과 등을 참고하여 폐기물의 용출독성 및 그 성분을 작성하고"),
    ("HZ-13", "hazard_info", "반응성 성분", "hazard.reactive", "required", "", "", "error", "⑯수분 등 접촉 시 화재·폭발·독성가스 우려 성분(없으면 \"없음\")이 없습니다", BF144, "란은 폐기물이 리튬, 나트륨, 알루미늄, 마그네슘, 구리, 니켈, 카보닐철 및 아연 등을 포함하는지를 확인하고"),
    ("HZ-14", "hazard_info", "취급 시 주의", "handling.protection", "required", "", "", "error", "⑰취급 시 보호조치가 없습니다", BF144, "란은 폐기물을 취급하는 경우 보호구(마스크 등)를 착용하는 것과 같은 보호조치를 작성하고"),
    ("HZ-15", "hazard_info", "저장·보관", "handling.storage", "required", "", "", "error", "⑰저장 및 보관방법이 없습니다", BF144, "저장 및 보관방법란은 온도, 습도 등의 보관조건 및 안전하게 보관하기 위한 방법을 적습니다"),
    ("HZ-16", "hazard_info", "사고 시 조치", "accident.first_aid", "required", "", "", "error", "⑱응급조치가 없습니다", BF144, "응급조치란에는 폐기물에 의해 취급자 등에 피해가 발생한 경우 수행해야 할 조치에 대해 작성하고"),
    ("HZ-17", "hazard_info", "경로 일치", "waste.waste_code", "in_route", "waste_codes", "", "warning", "이 경로의 폐기물 분류번호와 다릅니다", BF144, "⑧란은 「폐기물관리법 시행규칙」 별표 4 폐기물의 종류별 세부분류에 따른 폐기물 명칭과 분류번호를 적습니다"),
    ("HZ-18", "hazard_info", "물량", "waste.ton_year", "approx", "ton_year", "", "warning", "사례 배출량(연 환산)과 30% 넘게 다릅니다", BF144, "⑩ 발생량(톤/년)"),
    ("HZ-19", "hazard_info", "근거 자료", "", "info", "", "", "info", "유해특성·성분은 폐기물공정시험기준에 따른 분석결과 또는 근거 자료로만 적습니다 (추정 금지)", BF144, "분석결과 또는 자료(근거자료 기재)를 선택한 후"),
    ("HZ-20", "hazard_info", "확인필", "", "info", "", "", "info", "직접 작성하면 대표자 직인, 전문기관에 맡기면 전문기관 직인을 찍습니다", BF144, "스스로 작성하는 자의 경우 대표자 직인을"),
    # ── 2026.11.19 추가: 제5호 칸 안내 보강 (①~⑥ 위탁자, ⑦~⑭ 수탁자는 모두 적는 칸) ──
    ("CC-20", "capacity_check", "위탁자 생년월일", "consignor.birthdate", "required", "", "", "error", "④위탁자 생년월일이 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("CC-21", "capacity_check", "위탁자 전화번호", "consignor.phone", "required", "", "", "warning", "⑤위탁자 사업장 전화번호가 없습니다", BF5, "위탁자(배출자)는 ①란부터 ⑥란까지 적은 후"),
    ("CC-22", "capacity_check", "수탁자 대표자", "consignee.representative", "required", "", "", "error", "⑨수탁자 대표자가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-23", "capacity_check", "수탁자 생년월일", "consignee.birthdate", "required", "", "", "error", "⑩수탁자 생년월일이 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-24", "capacity_check", "수탁자 전화번호", "consignee.phone", "required", "", "", "warning", "⑪수탁자 사업장 전화번호가 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-25", "capacity_check", "수탁자 업종", "consignee.industry", "required", "", "", "error", "⑫수탁자 업종(한국표준산업분류 대분류)이 없습니다", BF5, "⑫란은 한국표준산업분류의 대분류에 따른 업종명을"),
    ("CC-26", "capacity_check", "수탁자 기술능력", "consignee.tech", "required", "", "", "error", "⑬수탁자 기술능력이 없습니다", BF5, "수탁자(처리자)는 ⑦란부터"),
    ("CC-27", "capacity_check", "허가(신고)일", "consignee.permit_date", "required", "", "", "error", "⑭허가(신고)일(최종 변경일)이 없습니다", BF5, "⑭폐기물처리업 허가 또는 폐기물처리 신고 관련 사항"),
    ("CC-28", "capacity_check", "허가 업종", "permit.industry", "guide", "", "", "info", "허가증(신고증명서)에 적힌 허가·신고 업종을 그대로 옮깁니다", BF5, "수탁자의 허가 또는 신고 사항"),
    ("CC-29", "capacity_check", "영업대상 폐기물", "permit.waste", "guide", "", "", "info", "허가증에 적힌 영업대상폐기물(신고증명서는 재활용대상폐기물)을 모두 적습니다", BF5, "허가증에 적힌 영업대상폐기물 또는 폐기물처리 신고증명서에 적힌 재활용대상폐기물을 모두 적습니다"),
    ("CC-30", "capacity_check", "처분·재활용 방법", "treatment_items[].treatment_code", "guide", "", "", "info", "처리분류와 세분류 번호(예: 위탁 재활용 중간가공폐기물 제조 2010)를 모두 적습니다", BF5, "폐기물처리 방법별 분류에 따라 처리분류와 세분류 번호를 모두 적습니다"),
    ("CC-31", "capacity_check", "시설", "facility.type", "guide", "", "", "info", "허가받은 중간처분 또는 재활용 시설의 종류와 용량을 적습니다", BF5, "폐기물 처분시설 또는 재활용시설란에는 그 시설을 구체적으로 적고"),
    ("CC-32", "capacity_check", "시설 용량", "facility.capacity", "guide", "", "", "info", "허가받은 중간처분 또는 재활용 시설의 종류와 용량을 적습니다", BF5, "폐기물 처분시설 또는 재활용시설란에는 그 시설을 구체적으로 적고"),
    ("CC-33", "capacity_check", "운반 차량", "vehicle.size", "guide", "", "", "info", "수집·운반업자는 허가받은 수집운반차량을 모두 적습니다", BF5, "수집·운반업자의 경우에는 허가받은 수집운반차량을"),
    ("CC-34", "capacity_check", "운반 차량", "vehicle.count", "guide", "", "", "info", "수집·운반업자는 허가받은 수집운반차량을 모두 적습니다", BF5, "수집·운반업자의 경우에는 허가받은 수집운반차량을"),
    # ── 2026.11.19 추가: 제6호·제12호 칸 안내 보강 ──
    ("ER-26", "emitter_report", "폐기물 종류", "waste_items[].name", "guide", "", "", "info", "별표 4의 사업장폐기물 종류(법정 명칭)를 분류번호와 짝으로 적습니다", BF6, "별표 4에 따른 사업장폐기물의 종류와 분류번호를 세분류로 적습니다"),
    ("ER-27", "emitter_report", "운반량", "waste_items[].haul_amount", "guide", "", "", "info", "운반자가 맡는 양을 적습니다 (서식에 단위 표시가 없어 제출처에 단위·기간 확인)", BF6, "운반량"),
    ("TP-25", "treatment_plan", "제출인 대표자", "submitter.representative", "guide", "", "", "info", "③제출인 성명(대표자)을 적습니다", BF12, "③ 성명(대표자)"),
    ("TP-26", "treatment_plan", "산업단지명", "submitter.industrial_park", "guide", "", "", "info", "사업장이 산업단지에 있으면 고시된 산업단지명을 적습니다", BF12, "⑤란의 사업장 소재지 산업단지명은 해당 사업장이 산업단지에 위치한 경우"),
    ("EC-19", "entrust_contract", "운반 수탁자", "consignee2.name", "guide", "", "", "info", "운반업체도 수탁자이므로 서면 계약 당사자로 적습니다", K1613.format(3), Q_CONTRACT),
    ("EC-20", "entrust_contract", "폐기물 종류", "waste_items[].name", "guide", "", "", "info", "위탁하는 폐기물의 종류와 수량을 적습니다 (법정 명칭·분류번호와 짝으로)", K1613.format(3), Q_KIND),
    ("ER-28", "emitter_report", "신고인 생년월일", "reporter.birthdate", "guide", "", "", "info", "④신고인(대표자) 생년월일을 적습니다", BF6, "④ 생년월일"),
    ("ER-29", "emitter_report", "사업장 전화번호", "reporter.phone", "guide", "", "", "info", "⑤사업장 주소 옆 전화번호를 적습니다", BF6, "전화번호"),
    ("TP-27", "treatment_plan", "제출인 생년월일", "submitter.birthdate", "guide", "", "", "info", "④제출인(대표자) 생년월일을 적습니다", BF12, "④ 생년월일"),
    ("TP-28", "treatment_plan", "사업장 전화번호", "submitter.phone", "guide", "", "", "info", "⑤사업장 주소 옆 전화번호를 적습니다", BF12, "전화번호"),
    ("TP-29", "treatment_plan", "폐기물 종류", "waste_items[].name", "guide", "", "", "info", "⑩폐기물 종류는 시행령 별표 1에 따라 구분해 적습니다", BF12, "⑩란은 「폐기물관리법 시행령」 별표 1에 따라 구분하여 적되"),
    ("TP-30", "treatment_plan", "운반량", "waste_items[].transport_ton_year", "guide", "", "", "info", "⑮운반계획의 운반량(톤/년)을 적습니다", BF12, "운반량"),
    ("TP-31", "treatment_plan", "처리량", "waste_items[].treatment_ton_year", "guide", "", "", "info", "처리계획의 처리량(톤/년)을 적습니다", BF12, "처리량"),
    ("TP-32", "treatment_plan", "의료폐기물", "medical.facility_type", "guide", "", "", "info", "의료폐기물인 경우에만 발생 기관 구분을 적습니다", BF12, "란은 의료폐기물인 경우에만 작성하되"),
]

COLS = ["req_id", "doc_type", "item", "field", "check", "param", "when", "severity", "message", "basis_key", "quote", "status",
        "summary", "duty", "sentence", "sentence_hash"]   # 2026.11.19: 쉬운 요약 · 의무 출처 · 근거 문장 전체 · 문장 지문


def write(path: Path, sentences: dict | None = None) -> None:
    """sentences: {req_id: 근거 문장 전체} (--confirm 때 서버 원문에서 뽑음). 없으면 빈칸"""
    import hashlib
    from law_agent.review.summaries import DUTY, SUMMARY
    sentences = sentences or {}
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in ROWS:
            sent = sentences.get(r[0]) or ""
            h = hashlib.sha1(sent.encode("utf-8")).hexdigest()[:12] if sent else ""
            w.writerow([*r, "draft", SUMMARY.get(r[10], ""), DUTY.get(r[1], ""), sent, h])


def sentences_of(store) -> dict:
    """행마다 근거 인용이 든 원문 문장 전체 (guide.service.sentence)"""
    from law_agent.guide.service import sentence
    return {r[0]: sentence(r[9], r[10], store) for r in ROWS}


def changed_sentences(old_csv: Path, now: dict) -> list[str]:
    """확정본에 저장한 문장 지문과 지금 원문 문장이 다른 행 → 요약을 다시 확인해야 함"""
    import hashlib
    if not old_csv.is_file():
        return []
    old = {r["req_id"]: r.get("sentence_hash") or "" for r in csv.DictReader(old_csv.open(encoding="utf-8-sig"))}
    out = []
    for rid, sent in now.items():
        h = hashlib.sha1((sent or "").encode("utf-8")).hexdigest()[:12] if sent else ""
        if old.get(rid) and h and old[rid] != h:
            out.append(rid)
    return out


def verify(store) -> list[tuple[str, str, str]]:
    """서버 법 원문(chunk_v2)과 대조. 반환: 실패 [(req_id, 근거 키, 이유)]. 통과 행은 confirmed 후보."""
    import re
    norm = lambda s: re.sub(r"\s+", "", s or "")
    bad = []
    for r in ROWS:
        rid, key, quote = r[0], r[9], r[10]
        law, rest = key.split(":", 1)
        art, eff = rest.split("@")
        top = art.split("/")[0]
        c = store.get(law, top)
        if c is None:
            bad.append((rid, key, "조문 없음 (번호 이동?)"))
            continue
        if c.effective != eff:
            bad.append((rid, key, f"시행일 다름: 원문 {c.effective}"))
        text = c.text + "".join(ch.text for ch in store.children(law, top))
        if norm(quote) not in norm(text):
            bad.append((rid, key, "인용이 원문에 없음"))
    return bad


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(Path(__file__).with_name("doc_requirements.csv")))
    ap.add_argument("--verify", action="store_true", help="WASTE_LAW_DIR 원문과 인용 대조")
    ap.add_argument("--confirm", action="store_true", help="대조 통과 행을 confirmed 로 저장 (사람 확인 후)")
    a = ap.parse_args()
    out = Path(a.out)
    bad, sents = [], {}
    if a.verify or a.confirm:
        from law_agent.tools.law_tools import get_store       # law_agent 의 LawStore (WASTE_LAW_DIR)
        store = get_store()
        bad = verify(store)
        for b in bad:
            print("실패", *b)
        print(f"대조: {len(ROWS) - len({b[0] for b in bad})}/{len(ROWS)}행 통과")
        sents = sentences_of(store)
        moved = changed_sentences(out, sents)
        for rid in moved:
            print("요약 다시 확인", rid, "(근거 문장이 확정 때와 달라짐 — review/summaries.py 의 요약을 원문과 다시 맞추세요)")
        print(f"근거 문장 전체 찾음: {sum(1 for v in sents.values() if v)}/{len(ROWS)}행" + (f" · 요약 다시 확인 {len(moved)}행" if moved else ""))
    if not a.confirm and out.exists() and out != Path(__file__).with_name("doc_requirements.csv"):
        print(f"(--verify 만: {out} 는 바꾸지 않음)")
        sys.exit(0)
    write(out, sents)
    if a.confirm:
        failed = {b[0] for b in bad}
        import csv as _csv
        rows = list(_csv.DictReader(out.open(encoding="utf-8-sig")))
        for r in rows:
            r["status"] = "draft" if r["req_id"] in failed else "confirmed"
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
    print(f"{len(ROWS)}행 → {out}")
