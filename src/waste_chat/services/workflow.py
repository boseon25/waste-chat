"""경로 선택과 서식 열기는 로컬 문서 목록에서 결정한다."""
import re
from . import catalog

ALIASES = {
    "기초입력서":"intake", "기초정보입력서":"intake","기초입력":"intake",
    "제6호":"form6","6호":"form6","지정12호":"dplan","제12호":"dplan","제13호":"analysis",
    "운반업체용":"form5_b","운반업체용제5호":"form5_b","처리업체용":"form5_c","처리업체용제5호":"form5_c",
    "일반제7호":"sitework","건설제7호":"cplan","일반제8호":"joint","건설제9호":"simple",
    "제4호의2":"assessment","제4호의4":"approval","제14호의4":"hazard","반입문의":"rfq","견적문의":"rfq",
    "계약조건":"contract","계약서":"contract","위수탁계약서":"contract","정산표":"settlement","올바로":"allbaro",
}
OPEN = re.compile(r"열어\s*(?:줘|주세요)?|띄워\s*(?:줘|주세요)?|보여\s*(?:줘|주세요)?|열기")
def compact(s):
    return re.sub(r"[\s·.?!]","",s).replace("별지","")


def guide(doc, data):
    msg=data.get("message","")
    if not isinstance(msg,str) or "=" in msg: return None
    phrase=compact(msg)
    # Explicit commands only: a descriptive sentence containing a form no. stays a statement.
    wanted=OPEN.split(msg,maxsplit=1)
    if len(wanted)>1:
        query=re.sub(r"(?:서식을|서식|양식을|양식|을|를)$","",compact(wanted[0]))
        possible={d["id"] for d in catalog.DOCS.values() if compact(d["title"])==query}
        if query in ALIASES: possible.add(ALIASES[query])
        if not possible and query and query not in {"서류","문서","필요한서류","필요서류"}:
            possible={d["id"] for d in catalog.DOCS.values() if query in compact(d["title"])}
        if len(possible)==1:
            selected=catalog.DOCS[next(iter(possible))]
            return {"updates":{},"open_doc":selected["id"],"reply":selected["title"]+" 문서를 열었습니다. "+selected["description"]}
        if len(possible)>1:
            return {"updates":{},"reply":"어느 업무의 서식을 뜻하나요? "+" / ".join(catalog.DOCS[k]["title"] for k in catalog.DOCS if k in possible)}
    if any(x in phrase for x in ("필요한서류","필요서류","어떤서류","무슨서류","문서목록","서류목록")):
        chosen=data.get("contexts",[])
        if not isinstance(chosen,list) or any(not isinstance(c,str) for c in chosen):
            raise ValueError("경로 선택을 확인하세요.")
        ids={v for c in catalog.CONTEXTS if c["id"]=="common" or c["id"] in chosen for v in c["docs"]}
        lines=[]
        for stage in catalog.STAGES:
            titles=[d["title"] for d in catalog.DOCS.values() if d['id'] in ids and d['stage']==stage['id']]
            if titles: lines.append(stage['label']+" — "+", ".join(titles))
        return {"updates":{},"reply":"현재 선택한 조건으로 검토할 자료입니다. 신고·확인 대상 여부에 따라 달라집니다.\n"+"\n".join(lines)+
                ("\n업무 경로에서 외부 위탁과 해당 신고 유형 등을 고르면 관련 목록이 추가됩니다." if not chosen else "")}
    if any(q in phrase for q in ("어디에제출","누가작성","누구에게","무슨용도","작성주체")):
        return {"updates":{},"reply":doc["description"]}
    return None


def intake_hint(doc,updated):
    if doc["id"]!="intake": return ""
    if updated.get("hazard_status") in {"있음","확인 필요"}:
        return " 유해성 관련 표시가 있어 발생공정과 원료 자료, 지정 여부 사전분석 및 유해성 정보자료 적용 대상을 확인할 필요가 있습니다."
    if "quantity_value" in updated:
        return " 초기 수량의 기간과 실측·예상 여부를 반입 문의에 적어 주세요. 신고 기준과 비교하려면 기간이 필요합니다."
    return ""
