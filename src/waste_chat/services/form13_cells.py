"""Fill regions on the bundled (2025-08-07) 별지 제13호, measured from the page's top left."""

DOC_ID = "analysis"
SHA256 = "12259b4697e96343c850f48b18d62fd439b14f7b06a607e236acb6c304ffd385"


def cell(key, label, box, write, help_text):
    return {"key": key, "label": label, "page": 0, "box": list(box),
            "write": list(write), "help": help_text}


# The top four cells can be selected on their printed label; their values start to its right.
CELLS = [
    cell("applicant_name", "① 상호(명칭)", (98.35, 127.18, 320.11, 154.15), (170, 129.5, 317, 152),
         "제출인 사업장의 등록된 상호 또는 명칭을 입력하세요."),
    cell("applicant_registration", "② 사업자등록번호", (320.11, 127.18, 536.35, 154.15), (412, 129.5, 534, 152),
         "제출인의 사업자등록번호를 확인해 입력하세요."),
    cell("applicant_representative", "③ 성명(대표자)", (98.35, 154.15, 320.11, 181.24), (180, 156.5, 317, 179),
         "제출인 사업장의 대표자 성명을 입력하세요."),
    cell("applicant_birth_date", "④ 생년월일", (320.11, 154.15, 536.35, 181.24), (382, 156.5, 534, 179),
         "③란에 기재한 대표자의 생년월일을 입력하세요."),
    cell("applicant_address", "⑤ 주소(사업장)", (98.35, 181.24, 536.35, 198.5), (179, 183.5, 534, 198),
         "제출인의 사업장 주소를 입력하세요. 전화번호는 다음 칸에 따로 적습니다."),
    cell("applicant_phone", "⑤ 사업장 전화번호", (337, 198.5, 536.35, 212.77), (390, 199, 526, 212),
         "제출인 사업장의 전화번호를 입력하세요."),
]

# The source draws one common set of column borders and its six rows vary slightly in height.
ROW_EDGES = (258.20, 286.13, 314.06, 341.99, 369.92, 397.96, 425.89)
COLUMNS = (
    ("quantity", "⑦ 발생량 (kg/월)", 136.13, 189.50, "이 행의 폐기물이 한 달에 발생하는 양을 kg 단위로 입력하세요."),
    ("state", "⑧ 성질·상태", 189.50, 242.63, "이 행의 폐기물에 관해 확인된 성질과 상태를 입력하세요."),
    ("method", "⑨ 처리방법", 242.63, 320.11, "이 행의 폐기물에 적용할 처리방법을 확인해 입력하세요."),
    ("risk", "⑩ 특성 및 취급 시 위험성분", 320.11, 420.61, "이 폐기물의 취급 과정 또는 처리 후 잔재물에서 확인된 위험성분을 입력하세요."),
    ("concentration", "⑪ 유해물질별 함유량", 420.61, 490.18, "해당되는 경우 분석기관이 관측한 시험 결과와 단위를 확인해 입력하세요."),
    ("other", "⑫ 기타", 490.18, 536.35, "이 폐기물에 해당하는 추가 분석 항목을 결과서와 원본 작성방법에서 확인하세요."),
)
for row, (top, bottom) in enumerate(zip(ROW_EDGES, ROW_EDGES[1:]), start=1):
    prefix = f"row{row}_"
    label = f"{row}행 · "
    # The name fits on the line above the pre-printed classification number delimiters.
    CELLS.append(cell(prefix + "type", label + "⑥ 폐기물 종류", (57.93, top, 136.13, top + 9),
                      (60, top + 0.5, 134, top + 8.7), "처리계획서에서 확인한 이 폐기물의 세분류명을 입력하세요."))
    # One selection holds six digits. The renderer prints each pair in its gap without redrawing the hyphens.
    code = cell(prefix + "code", label + "⑥ 분류번호", (57.93, top + 9, 136.13, bottom),
                (69.8, top + 9, 121, bottom - 2),
                "분류번호 여섯 자리를 한 번에 입력하세요. 예를 들어 11-22-33 또는 112233으로 입력할 수 있습니다.")
    code["parts"] = [[start, top + 9, end, bottom - 2] for start, end in ((69.8, 83), (88.9, 102), (107.9, 121))]
    CELLS.append(code)
    for key, title, left, right, help_text in COLUMNS:
        CELLS.append(cell(prefix + key, label + title, (left, top, right, bottom),
                          (left + 2.5, top + 2.5, right - 2, bottom - 2), help_text))

# Separate the date and the submitter's printed details from the signature space.
CELLS.extend([
    cell("date_year", "제출일 · 년", (401, 498, 457, 521), (408, 502, 446, 518), "제출 연도 네 자리를 입력하세요."),
    cell("date_month", "제출일 · 월", (457, 498, 498, 521), (466, 502, 487, 518), "제출 날짜의 월을 숫자로 입력하세요."),
    cell("date_day", "제출일 · 일", (498, 498, 535, 521), (502, 502, 522, 518), "제출 날짜의 일을 숫자로 입력하세요."),
    cell("submitter_business", "제출인 · 업소명", (199, 525, 330, 551), (244, 531, 328, 548),
         "하단 제출인란에 기재할 업소명을 입력하세요."),
    cell("submitter_representative", "제출인 · 대표자명", (330, 525, 467, 551), (383.5, 531, 466, 548),
         "하단 제출인란에 기재할 대표자 성명을 입력하세요."),
])
