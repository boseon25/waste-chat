"""서식의 주체·시점·공유 필드·조건별 표를 한 곳에 둔다. 조건은 사용자가 확인한다."""
from pathlib import Path
from unicodedata import normalize
from .intake_pdf import INTAKE_FIELDS
from .field_info import guidance
from .original_cells import attach_to_catalog
from .extra_pdf import SPECS
from .pdf_writer import OFFICIAL_KEYS

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "templates" / "official"
STAGES = [{"id":i,"label":l} for i,l in [
    ("intake","처리처 결정 전"),("partner","업체 선정·계약"),("filing","행정 신고·확인"),("transfer","반출·처리·정산")]]
DOCS = {}
CONTEXTS = []


CHOICES = {"_bond": ["공제조합가입", "보증보험가입"], "_acceptance": ["가능", "불가능"]}  # 정해진 값만 쓰는 칸


def fields(pairs):
    out = []
    for k, l in pairs:
        f = {"key": k, "label": l}
        for tail, opts in CHOICES.items():
            if k.endswith(tail):
                f["choices"] = opts
        out.append(f)
    return out


def add(id,title,stage,kind,description,entries=(),file=None,link=None):
    entries = [{**field, **guidance(id, field)} for field in entries]
    doc=dict(id=id,title=title,stage=stage,kind=kind,description=description,fields=entries)
    if file:
        path=Path(file)
        if path.is_absolute():
            path=path.relative_to(ROOT)
        doc["file"]=str(path.as_posix())
        doc["blank_url"]="/api/blank/"+id
    elif link:
        doc["blank_url"]=link
    DOCS[id]=doc


def original(group,number):
    # An underscore after the number distinguishes No. 7 from No. 70.
    prefix = f"별지제{number}_"
    matches = [
        path for path in (OFFICIAL / group).glob("*.pdf")
        if normalize("NFC", path.name).startswith(prefix)
    ]
    if len(matches)!=1:
        raise RuntimeError(f"{group} {number} 원본 서식 파일을 확인하세요.")
    return matches[0]


def custom(id,title,stage,description,pairs):
    blank_forms = {
        "rfq": "반입_운반_견적문의서.pdf",
        "contract": "위수탁계약서_검토용_초안.pdf",
        "settlement": "반출_1건_계량_정산대조표.pdf",
    }
    file = ROOT / "templates" / "custom" / blank_forms[id] if id in blank_forms else None
    add(id,title,stage,"자체양식",description,fields(pairs),file=file)


def condition(id,label,*docs):
    CONTEXTS.append(dict(id=id,label=label,docs=list(docs)))


GEN=("generator_name","배출업체명")
WASTE=("waste_name","현장에서 부르는 폐기물 명칭")
PROCESS=("generation_process","발생공정")
STATE=("waste_state","현장 성상")
QTY=("quantity_value","기초 입력서의 수량")
UNIT=("quantity_unit","기초 입력서의 단위")
CONTACT=("contact_phone","담당자 연락처")
HAUL=("hauler_name","선정된 운반업체명")
RECV=("receiver_name","선정된 처리업체명")
METHOD=("treatment_method","확인된 처리분류 및 세분류 번호")
MEASURE=("measured_tons","이번 반출 실계량값(톤)")

add("intake","폐기물 기초 정보 입력서","intake","자체양식",
    "배출업체가 작성하는 프로젝트 원본입니다. 수량은 단위와 함께 적고, 어느 기간 기준인지는 반입 문의에서 따로 확인합니다.",
    INTAKE_FIELDS,file="templates/intake/원본_폐기물기초정보입력서.pdf")
custom("rfq","반입·운반 견적 문의","intake","배출자가 후보 업체에 보내는 문의 초안입니다. 업체별 답을 비교할 때는 각각의 작업 사본으로 저장하세요.",
       [GEN,WASTE,PROCESS,STATE,QTY,UNIT,("quantity_period","수량의 기간·배출 주기"),("contamination_detail","이물질·오염 상세"),
        ("rfq_recipient","문의받을 후보 업체"),("pickup_location","상차 장소"),("rfq_destination","후보 처리시설 주소"),
        ("rfq_questions","반입 기준·시료·견적 문의"),CONTACT])
custom("material","물질 재활용 검토 메모","intake","선별·가공 후 산출물이 중간가공 폐기물인지 재생원료·제품인지 후보 업체의 답을 기록합니다.",
       [WASTE,PROCESS,("material_resin","재질·혼합 정도"),("material_intake","재활용업체 반입 조건"),
        ("material_output","가공 후 산출물과 용도"),("material_next","중간가공 후 다음 인수처")])
custom("energy","에너지 이용 검토 메모","intake","후보 시설의 허가, 품목별 사전 분석·확인 대상과 연료 제조 또는 에너지 회수 조건을 확인합니다.",
       [WASTE,("energy_process","후보 시설과 연료 제조·열 회수 방법"),("energy_test","확인할 성분·수분·발열량 등"),("energy_result","시설에서 회신한 반입 기준")])
custom("disposal","처분 경로 검토 메모","intake","소각 등 중간처분인지 매립 등 최종처분인지, 반입 제한·선행 처리 및 잔재물 처리 조건을 확인합니다.",
       [WASTE,("disposal_method","제안받은 소각·처분 방법"),("disposal_condition","선행 처리·반입 조건"),("disposal_residue","처분 후 잔재물 처리")])
# 분류 확인: 분석기관·관할 기관에 문의할 때 쓰는 자체 양식. 같은 키는 거래방 안에서 서로 이어받는다.
GEN_REG=("generator_registration","배출업체 사업자등록번호")
GEN_ADDR=("generator_address","배출업체 사업장 주소")
CONTACT_NAME=("contact_name","담당자 이름")
CUR_CODE=("current_classification","현재 적용 중인 분류번호·명칭")
AMOUNT=(("quantity_value","배출량"),("quantity_unit","배출량 단위"))
custom("process_note","폐기물 발생 공정 설명서","intake","분석 의뢰와 분류 질의에 함께 첨부하는 설명서입니다. 여기 적은 공정·원료·성상은 같은 거래방의 의뢰서와 질의서에 그대로 이어집니다.",
       [GEN,GEN_ADDR,("site_name","발생 현장·작업장"),PROCESS,("process_steps","공정 순서와 폐기물이 나오는 단계"),
        ("raw_materials","투입 원료·약품(MSDS 보유 여부)"),WASTE,STATE,*AMOUNT,("quantity_period","수량의 기간·배출 주기"),
        ("storage_method","보관 방법·장소"),("evidence_list","첨부 사진·자료 목록")])
custom("analysis_request","폐기물 성분 분석 의뢰서(공통)","intake","폐기물분석전문기관에 낼 의뢰 내용을 정리한 공통 양식입니다. 기관 고유 양식이 있으면 이 내용을 옮겨 적으면 됩니다.",
       [GEN,GEN_REG,GEN_ADDR,CONTACT_NAME,CONTACT,("lab_name","의뢰할 분석기관"),WASTE,PROCESS,STATE,
        ("raw_materials","투입 원료·약품(MSDS 보유 여부)"),("analysis_purpose","분석 목적"),("analysis_items","분석 요청 항목"),
        ("sample_count","시료 수·채취량"),("sampling_plan","시료 채취 예정일·채취 주체"),("report_use","결과서 사용처·제출 기관")])
custom("sampling_record","시료 채취 기록부","intake","시료를 언제·어디서·어떻게 채취했는지 남기는 기록입니다. 분석 의뢰 때 시료와 함께 제출하거나 보관합니다.",
       [GEN,WASTE,("sampling_datetime","채취 일시"),("sampling_place","채취 장소(보관 위치)"),("sampling_method","채취 방법·지점 수"),
        ("sample_count","시료 수·채취량"),("sample_container","용기·보존 방법"),("sampler","채취자"),("sampling_witness","입회자"),
        ("sampling_notes","채취 당시 상태·특이사항")])
custom("classify_query","폐기물 분류 질의서","intake","관할 시·군·구 또는 유역·지방환경청에 분류번호 판단을 묻는 질의서 초안입니다. 법정 서식이 없어 공문·국민신문고 본문으로 옮겨 씁니다.",
       [GEN,GEN_REG,GEN_ADDR,CONTACT_NAME,CONTACT,("query_recipient","질의할 기관"),WASTE,PROCESS,STATE,*AMOUNT,CUR_CODE,
        ("candidate_classification","검토 중인 분류번호·명칭"),("change_reason","분류가 바뀔 수 있다고 보는 이유"),
        ("analysis_summary","분석 결과 요약(기관·결과서 번호)"),("treatment_route","운반·처리 예정 경로"),
        ("query_text","질의 내용"),("evidence_list","첨부 사진·자료 목록")])
add("analysis","제13호 폐기물분석결과서","intake","발급자료","제출인 정보는 미리 준비할 수 있습니다. 지정폐기물 처리계획 확인에는 폐기물분석전문기관이 작성한 결과서가 필요합니다. 하단은 제출인의 서명 또는 인을 위한 칸입니다.",file=original("지정","13호"))
add("hazard","제14호의4 폐기물 유해성 정보자료","intake","법정서식","규칙상 대상 폐기물은 배출자가 직접 또는 전문기관에 의뢰해 준비하고, 위탁 시 수탁자에게 제공합니다. 물리·화학적 성질과 취급·방제 정보를 근거자료로 확인하세요.",file=original("지정","14호의4"))
add("credentials","허가·신고·처리이행보증 증빙","partner","발급자료","배출자가 B·C로부터 허가증 또는 신고증명서와 해당 처리이행보증 자료 사본을 회수하고 영업 대상·방법·시설·능력과 현재 적법성을 대조합니다. 업체의 원본 발급자료를 별도 보관하세요.")
BASE_LABELS = {
    "waste_name":"폐기물 종류(법정 명칭 확인)", "waste_legal_name":"폐기물의 법정 명칭", "waste_state_code":"성질·상태 코드(원본 지침 확인)",
    "waste_classification":"폐기물 분류번호(00-00-00)", "annual_tons":"예상 연간 배출량(톤/년)", "monthly_tons":"예상 월 배출량(톤/월)",
    "haul_amount":"운반량(서식 단위)", "treatment_type":"처리 구분(자가·위탁)", "treatment_method":METHOD[1],
    "treatment_amount":"처리량(톤/년)", "generator_industry":"배출업체 업종",
    "raw_materials":"주 원료명 및 사용량(톤/년)", "main_products":"주 생산품명 및 생산량(톤/년)", "process_steps":"제조공정",
    "change_before":"변경사항 · 변경 전(변경신고만)", "change_after":"변경사항 · 변경 후(변경신고만)", "change_reason":"변경사유(변경신고만)",
}
for role,prefix in [("generator","배출업체"),("hauler","운반업체"),("receiver","처리업체")]:
    for key,label in {
        "name":"상호", "registration":"사업자등록번호","representative":"대표자","birthdate":"대표자 생년월일","address":"사업장 주소",
        "phone":"사업장 전화","industry":"업종","technical_capacity":"기술능력","permit_number":"허가·신고 번호",
        "permit_date":"허가·신고일(최종 변경일)","permit_authority":"허가·신고 수리기관","permit_industry":"허가·신고 업종",
        "permit_waste":"허가된 영업대상 폐기물","method":"허가된 처분·재활용 방법","facility_type":"시설 종류",
        "facility_capacity":"시설 용량(톤/시간)","vehicle_size":"차량 규모(톤)","vehicle_count":"차량 수(대)",
        "acceptance":"회신한 수탁 가능 여부","entrusted_annual_tons":"해당 업체 연간 위탁량(톤/년)",
        "bond":"방치폐기물처리 이행보증(⑮)"}.items():
        BASE_LABELS[role+"_"+key]=prefix+" "+label
for docid,title,form,stage,desc in [
    ("form5_b","제5호 수탁처리능력 확인 · 운반업체 B","5호","partner","별도 운반업체가 있는 예입니다. A 정보와 B 회신을 반영합니다. 수탁능력·허가·서명은 B가 확인해야 합니다."),
    ("form5_c","제5호 수탁처리능력 확인 · 처리업체 C","5호","partner","처리업체 C의 허가·시설·능력은 C가 확인합니다. 원본의 목적 체크·처리능력 세부표·서명·첨부를 별도로 마무리하세요."),
    ("form6","제6호 사업장폐기물배출자 신고·변경신고","6호","filing","규칙 제18조제2항제1호 해당 시 관할 시·군·구 등에 제출합니다. 신규와 변경은 기재 범위가 다릅니다. 연결된 칸은 최초 신고의 1개 폐기물 행을 중심으로 구성했습니다.")]:
    keys=OFFICIAL_KEYS[docid]
    add(docid,title,stage,"법정서식",desc,fields((k,BASE_LABELS[k]) for k in keys),original("일반",form))
custom("contract","폐기물 수집·운반 및 처리 위·수탁계약서 초안","partner","당사자가 협의해 작성하는 자체 계약 초안입니다. 실제 조건을 검토하고 당사자가 체결한 서명본을 보관합니다. 수탁자 능력과 계약 기간·단가·폐기물 종류·수량·성상·주의사항을 확인하세요.",
       [GEN,("generator_address","배출업체 사업장 주소"),("generator_registration","배출업체 사업자등록번호"),("generator_representative","배출업체 대표자"),HAUL,("hauler_address","운반업체 사업장 주소"),("hauler_registration","운반업체 사업자등록번호"),("hauler_representative","운반업체 대표자"),RECV,("receiver_address","처리업체 사업장 주소"),("receiver_registration","처리업체 사업자등록번호"),("receiver_representative","처리업체 대표자"),WASTE,("waste_classification","폐기물 분류번호(00-00-00)"),STATE,("contract_quantity","계약 수량과 단위·기준기간"),("contract_period","계약 기간"),
        ("pickup_location","상차 장소"),("contract_destination","처리시설 주소"),METHOD,("transport_price","운반 단가 및 단위"),
        ("processing_price","처리 단가 및 단위"),("payer","지급 주체·정산 기준"),("precautions","취급 주의사항"),
        ("rejection_terms","반입 거절·계량 차이 처리 조건")])
for id,title,role in [
    ("dplan","제12호 지정폐기물 처리계획(변경)","확인 대상 지정폐기물을 처리하기 전 제출. 분석기관 결과와 위탁 시 수탁확인서를 함께 준비합니다."),
    ("cplan","건설 제7호 건설폐기물 처리계획(변경)","건설폐기물법 적용 공사의 배출자가 관할청에 제출. 현장·공사기간·종류별 예상량·처리계획을 확인하세요."),
    ("assessment","제4호의2 재활용환경성평가 신청","법정 평가 대상일 때 신청인이 평가기관에 제출. 재활용계획서 등 원본 뒷면의 첨부 요건을 확인하세요."),
    ("approval","제4호의4 재활용승인 신청","평가서가 발급된 뒤 해당 신청인이 재활용환경성평가서를 첨부하여 승인 신청합니다.")]:
    spec=SPECS[id]
    add(id,title,"filing","법정서식",role,spec["fields"],spec["file"])
add("self","자가처리 여부·시설 요건 확인","filing","증빙·보관","외부 위탁을 하지 않는 경우에는 자가처리 방법과 시설 허가·승인·신고 및 기록 의무를 따로 검토합니다. 위탁업체 B·C가 없는 건입니다. 이 프로젝트는 외부 위탁용 자료를 중심으로 제공합니다.")
for args in [
    ("sitework","일반 제7호 공사·작업 배출신고","일반","7호","건설폐기물법 적용 여부를 먼저 확인합니다. 폐기물관리법 시행규칙 제18조제1항제4호 적용 시 사용합니다."),
    ("joint","일반 제8호 공동처리 배출신고","일반","8호","공동처리 운영기구 대표자가 해당하는 경우에 사용합니다."),
    ("simple","건설 제9호 간이인계서","건설","9호","건설폐기물법상 전자입력을 갈음할 수 있는 경우만 사용합니다. 원본의 각 보관 주체를 확인하세요."),
    ("aggregate_plan","건설 제33호 순환골재 사용계획서","건설","33호","순환골재 등 의무사용 대상 공사의 발주자가 준비합니다. 건설폐기물 반출 계획과 별개입니다."),
    ("construction_recycling","건설 제32호 재활용실적보고서","건설","32호","대상 건설공사의 배출자가 준공 관련 실적을 보고합니다. 반출 당일 인계서와 용도가 다릅니다.")]:
    id,title,group,num,desc=args
    stage="transfer" if id in {"simple","construction_recycling"} else "filing"
    add(id,title,stage,"법정서식",desc,file=original(group,num))
add("designated_acceptance","제5호 폐기물 수탁확인 · 지정 처리계획","partner","법정서식","제5호의 두 번째 용도입니다. 협의한 수탁 예정 사실을 처리자가 확인합니다. 능력 확인을 병행할 때는 작성방법에 따라 두 목적을 표시하고 (4) 수탁 확인도 기재합니다.",file=original("일반","5호"))
add("allbaro","올바로 전자인계·인수","transfer","시스템기록","전자입력 대상이면 배출자는 인계 전에 예약·확정 입력하고 운반자에게 인계번호를 전달합니다. 운반자와 처리자가 자기 단계의 인수·운반·처리 내용을 입력합니다. 이 앱은 공식 시스템에 전송하지 않습니다.",link="https://www.allbaro.or.kr")
custom("settlement","반출 1건 계량·정산 대조표","transfer","차량 한 번의 실계량·처리 회신과 계약 조건을 대조하는 자체 표입니다. 다음 차량은 별도 작업 사본으로 작성하세요.",
       [GEN,HAUL,RECV,WASTE,("pickup_date","이번 반출일"),("vehicle_id","이번 반출 차량번호"),("manifest_id","공식 인계번호"),MEASURE,
        ("receiver_tons","처리업체 인수량(톤)"),("weigh_id","계량 증빙 번호"),("process_evidence","처리 확인 증빙"),
        ("bill_reference","청구·세금계산서 참조번호"),("discrepancy","중량 차이·처리 결과")])
add("receipts","계량·처리 확인·청구 증빙","transfer","증빙·보관","계근소·계약업체·공급자가 발급한 실제 계량표, 계약이 요구하는 처리확인서·거래내역 및 과세관계에 맞는 청구·세무 증빙을 받습니다. 초안으로 발급본을 대체할 수 없습니다.")
add("checks","처리과정 확인 기록","transfer","증빙·보관","규칙 제17조의3·제17조의4와 별표5의8의 대상이면 정기적으로 위탁한 폐기물의 처리과정을 확인하고 결과를 남깁니다.")
for id,title,group,num in [
    ("ledger","제36호 사업장폐기물 관리대장","일반","36호"),("annual","제49호 배출·처리 실적보고","일반","49호"),
    ("cledger","건설 제22호 관리대장","건설","22호"),("creport","건설 제28호 배출·처리 실적보고","건설","28호")]:
    add(id,title,"transfer","법정서식","해당 장부·보고 의무가 있는 배출자가 처리 내역을 누적 관리하고 정해진 시점에 기록·보고합니다. 전산 처리 또는 갈음 요건과 기한을 별도로 확인하세요.",file=original(group,num))
condition("common","공통 준비","intake","rfq")
condition("outsourced","외부 위탁 검토","form5_b","form5_c","credentials","contract","allbaro","settlement","receipts","checks")
condition("material","원료·제품 / 중간가공","material")
condition("energy","연료 제조 / 에너지 회수","energy")
condition("disposal","소각·기타 처분 / 매립","disposal")
condition("self","자가처리 검토","self")
condition("business","일반 정기 배출 신고 대상","form6","ledger","annual")
condition("work","공사·작업 / 폐기물관리법","sitework","ledger","annual")
condition("joint","공동처리 신고 대상","joint")
condition("designated","지정폐기물 처리계획 확인 대상","analysis","designated_acceptance","dplan","ledger","annual")
condition("hazard","유해성 정보자료 대상","hazard")
condition("classification","지정 여부 사전분석 필요","process_note","analysis_request","sampling_record","analysis","classify_query")
condition("construction","건설폐기물법 적용 공사","cplan","simple","cledger","creport","construction_recycling")
condition("aggregate","순환골재 의무사용 공사","aggregate_plan")
condition("assessment","재활용환경성평가 대상 검토","assessment","approval")

attach_to_catalog(DOCS, ROOT)
