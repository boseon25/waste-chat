"""doc_requirements.csv 초안 만들기 (서류 검토 기준표).

한 행 = 서류 하나의 확인 항목 하나. 판정은 doc_review/__init__.py 가 이 표대로 한다.
quote 는 법 원문(조문·별지 서식)에 글자 그대로 있어야 한다 (공백만 무시). status 는 draft 로 시작하고
law_api review_tables 대조 + 사람 확인 후 confirmed.

check 종류
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
    ("CC-01", "capacity_check", "위탁자", "consignor.name", "required", "", "", "error", "①위탁자 상호가 없습니다", BF5, "위탁자(배출자)는①란부터⑥란까지적은후"),
    ("CC-02", "capacity_check", "위탁자", "consignor.biz_no", "required", "", "", "error", "②위탁자 사업자등록번호가 없습니다", BF5, "위탁자(배출자)는①란부터⑥란까지적은후"),
    ("CC-03", "capacity_check", "위탁자", "consignor.representative", "required", "", "", "error", "③위탁자 대표자가 없습니다", BF5, "위탁자(배출자)는①란부터⑥란까지적은후"),
    ("CC-04", "capacity_check", "위탁자", "consignor.address", "required", "", "", "error", "⑤위탁자 주소가 없습니다", BF5, "위탁자(배출자)는①란부터⑥란까지적은후"),
    ("CC-05", "capacity_check", "위탁 폐기물", "waste_items[].waste_code", "required", "", "", "error", "⑥위탁 폐기물 분류번호가 없습니다", BF5, "사업장폐기물의 명칭과 분류번호를 적고"),
    ("CC-06", "capacity_check", "위탁 폐기물", "waste_items[].state", "enum", "1|2|기타", "", "error", "⑥성질·상태는 고체 \"1\", 액체 \"2\", 지정폐기물 함유 용기 \"기타\"로 적어야 합니다", BF5, "고체상태이면 \"1\"로, 액체상태이면 \"2\"로"),
    ("CC-07", "capacity_check", "위탁 폐기물", "waste_items[].ton_year", "number", "", "", "error", "⑥위탁량(톤/년)이 없습니다", BF5, "위탁량(톤/년)란에는 연간 위탁처리하는 폐기물의 양을 톤"),
    ("CC-08", "capacity_check", "수탁자", "consignee.name", "required", "", "", "error", "⑦수탁자 상호가 없습니다", BF5, "수탁자(처리자)는⑦란부터"),
    ("CC-09", "capacity_check", "수탁자", "consignee.biz_no", "required", "", "", "error", "⑧수탁자 사업자등록번호가 없습니다", BF5, "수탁자(처리자)는⑦란부터"),
    ("CC-10", "capacity_check", "수탁자", "consignee.address", "required", "", "", "error", "⑪수탁자 주소가 없습니다", BF5, "수탁자(처리자)는⑦란부터"),
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
]

COLS = ["req_id", "doc_type", "item", "field", "check", "param", "when", "severity", "message", "basis_key", "quote", "status"]


def write(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in ROWS:
            w.writerow([*r, "draft"])


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
    bad = []
    if a.verify or a.confirm:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lawstore import LawStore
        bad = verify(LawStore.load())
        for b in bad:
            print("실패", *b)
        print(f"대조: {len(ROWS) - len({b[0] for b in bad})}/{len(ROWS)}행 통과")
    write(out)
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
