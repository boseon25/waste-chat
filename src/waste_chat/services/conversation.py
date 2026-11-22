"""현재 서식에 허용된 변경만 반영하고, 작업 단계에 맞는 다음 질문을 선택한다."""
import re
import unicodedata
from . import timely, workflow, workspace_help

STAGE_PROMPTS = {
    "intake":"배출자가 알고 있는 발생공정·성상·예상량을 기록합니다. 배출량 기간을 추정하지 마세요.",
    "partner":"당사자와 역할을 대조하세요. 법정 증빙에 해당하는 값은 명확히 제공한 자료 내용만 기록하세요.",
    "filing":"신고 또는 확인용 자료를 정리합니다. 확인되지 않은 법적 분류, 처리방법 코드, 검사 결과를 제안하지 마세요.",
    "transfer":"이번 차량의 실제 인계·운반·인수와 처리 증빙을 구분하세요. 연간 계약량으로 실제 반출량을 채우지 마세요.",
}
SECRET_PATTERN = re.compile(r"tgpt_sk_[A-Za-z0-9_-]{16,}", re.I)
UNKNOWN = re.compile(r"모르겠|모르는데|모르는|모릅니|몰라|모름|나중에|(?:미정|미확인)(?:입니다|이에요|이야|\s|$)")
QUESTION = re.compile(
    r"[?？]|무엇|뭔지|뭐(?:야|예요|에요|지|를|라고|죠)|뭘|어떻게|어디(?:서|에)|어떤|어느|"
    r"알려\s*(?:줘|주)|설명해|예시|궁금|써야|적어야|어디(?:를)?\s*(?:누르|눌|클릭|터치)|"
    r"(?:사용법|(?:입력|작성|선택|첨부|저장|사용)(?:하는)?\s*방법|쓰는\s*법)[.! ]*$|"
    r"(?:인가요|나요|까요|는지요|(?:되|하)는\s*(?:거[야지죠니]|거예요|건가(?:요)?)|"
    r"(?:되|돼|맞|가능한)\s*(?:건가|거야|거지)|(?:해도|써도|어도|아도|려도|줘도|면)\s*(?:돼(?:요)?|되니|됨)|"
    r"맞(?:아|지|죠)|되죠|됩니까|되는지|(?:할|쓸|받을|올)\s*수\s*있(?:는가|을까|어|나요))[.! ]*$"
)
FIELD_REFERENCE = re.compile(r"이\s*(?:칸|항목|필드)|여기(?:에|서)|어디(?:서|에서)\s*확인|(?:예시|예를\s*들)")


def field_guidance(field):
    help_text = field.get("help") or f"‘{field['label']}’에 해당하는 확인된 내용을 입력해 주세요."
    if field.get("example"):
        help_text += "\n입력 예시  " + field["example"]
    return help_text


def clean_values(values, labels):
    if not isinstance(values,dict) or len(values)>250:
        raise ValueError("항목의 형식을 확인하세요.")
    result={}
    for k,v in values.items():
        if k not in labels or not isinstance(v,str) or len(v)>5000:
            raise ValueError("현재 서식의 입력 항목과 길이를 확인하세요.")
        if SECRET_PATTERN.search(v):
            raise ValueError("API 키는 연결 설정에만 입력하세요.")
        if v.strip():
            result[k]=unicodedata.normalize("NFC",v.strip())
    return result


def update(doc,data,key):
    labels={f["key"]:f["label"] for f in doc["fields"]}
    values=clean_values(data.get("values",{}),labels)
    msg=data.get("message")
    if not isinstance(msg,str) or not 0<len(msg.strip())<=5000:
        raise ValueError("1~5,000자 사이의 문장을 입력하세요.")
    msg=unicodedata.normalize("NFC",msg.strip())
    if SECRET_PATTERN.search(msg):
        raise ValueError("키가 대화문에 들어 있습니다. 연결 설정에서만 입력하세요.")
    focus = data.get("focus")
    if not isinstance(focus, str) or focus not in labels:
        focus = None
    pinned = focus if focus and data.get("pinned_field") == focus else None
    answer = msg
    if pinned and "=" in msg:
        label, _, value = msg.partition("=")
        if "\n" not in msg and label.strip() in {focus, labels[focus]}:
            answer = value.strip()

    cell = None
    if data.get("original_position") is True and "original_cell" in data:
        cell = next((f for f in doc.get("original_cells", []) if f["key"] == data["original_cell"]), None)
        if cell is None:
            raise ValueError("현재 원본 서식에서 작성할 칸을 다시 선택해 주세요.")

    # A selected field is a destination for answers, not the subject of every question.
    question = "=" not in answer and (QUESTION.search(answer) or workspace_help.is_input_problem(answer))
    if question:
        usage = workspace_help.response(doc, answer)
        wants_field_contents = workspace_help.FIELD_CONTENT.search(workflow.compact(answer))
        if cell and wants_field_contents and (not usage or re.search(r"이\s*(칸|항목)", answer)):
            return {"updates": {}, "reply": f"‘{cell['label']}’ · " + field_guidance(cell)}
        if usage:
            return {"updates": {}, "reply": usage}
    navigation = workflow.guide(doc, data)
    if navigation:
        return navigation
    if not labels:
        if doc.get("file") and data.get("original_position") is True:
            if question:
                if cell:
                    return {"updates": {}, "reply": field_guidance(cell)}
                return {"updates": {}, "reply": "선택한 위치는 사용자가 직접 찍은 곳입니다. 원본에 인쇄된 항목명과 작성방법을 확인하세요. " + doc["description"]}
            if UNKNOWN.search(msg):
                return {"updates": {}, "reply": "내용이 확인되면 이 위치를 다시 선택해 입력할 수 있습니다."}
            if len(msg) > 500:
                raise ValueError("원본에 배치할 문구는 한 곳에 500자까지 입력할 수 있습니다.")
            reply = f"‘{cell['label']}’에 반영했습니다." if cell else "선택한 위치의 작성 문구를 확인했습니다."
            return {"updates": {}, "placement_text": msg, "reply": reply}
        if doc.get("file") and question:
            return {"updates": {}, "reply": doc["description"] + " 문구를 넣으려면 원본 페이지에서 작성할 위치를 먼저 누르세요."}
        return {"updates": {}, "reply": doc["description"]}

    if question:
        phrase = workflow.compact(answer)
        matched = [f for f in doc["fields"] if workflow.compact(f["label"]) in phrase]
        if len(matched) == 1:
            return {"updates": {}, "reply": field_guidance(matched[0])}
        if pinned and (workspace_help.FIELD_CONTENT.search(phrase) or FIELD_REFERENCE.search(answer)):
            field = next(f for f in doc["fields"] if f["key"] == pinned)
            return {"updates": {}, "reply": field_guidance(field)}
        return {"updates": {}, "reply": "화면 사용법이 궁금하신가요, 특정 칸에 쓸 내용이 궁금하신가요? "
                                        "사용법은 ‘칸을 선택하면 값만 보내면 돼?’처럼 물어보세요. "
                                        "항목에 관한 질문은 해당 칸을 선택하고 ‘무엇을 적나요?’를 누르면 됩니다."}

    if pinned and "=" not in answer:
        field = next(f for f in doc["fields"] if f["key"] == pinned)
        if UNKNOWN.search(answer) and answer not in field.get("choices", []):
            return {"updates": {}, "defer_field": pinned, "reply": field_guidance(field) + "\n확인할 때까지 기존 입력값을 유지합니다."}
        value = answer.removeprefix("입력 ").strip()
        if not value:
            return {"updates": {}, "reply": field_guidance(field)}
        return {"updates": clean_values({pinned: value}, labels), "reply": f"‘{field['label']}’에 입력했습니다."}
    if not pinned and "=" not in msg and UNKNOWN.search(msg):
        return {"updates": {}, "reply": "어느 칸을 확인 중인가요? ‘작성할 항목’에서 선택하면 그 칸의 설명을 볼 수 있고, 나중에 확인하도록 남겨둘 수도 있습니다."}
    aliases={f["key"]:f["key"] for f in doc["fields"]}
    aliases.update({f["label"]:f["key"] for f in doc["fields"]})
    # 명시 입력은 모델 없이도 재현 가능하다. 한 줄에는 한 항목만 받는다.
    updates={}
    for line in msg.splitlines():
        name,sep,val=line.partition("=")
        if sep and name.strip() in aliases and val.strip():
            updates[aliases[name.strip()]]=val.strip()
    mode_hint=""
    if not updates and key:
        model_labels={k:v for k,v in labels.items() if k!="attachment_types"}
        proposed=timely.extract(msg,key,data.get("model") or timely.MODEL,model_labels,STAGE_PROMPTS[doc["stage"]],focus)
        for field,value in proposed.items():
            if field in model_labels and isinstance(value,str):
                value=unicodedata.normalize("NFC",value.strip())
                if value and value in msg:
                    updates[field]=value
    elif not updates and focus and msg.startswith("입력 ") and msg[3:].strip():
        updates[focus]=msg[3:].strip()
    elif not updates:
        mode_hint="‘항목명=값’으로 입력해 주세요. 자연어 입력은 AI 설정에서 연결할 수 있습니다. "
    updated=clean_values(updates,labels)
    values.update(updated)
    missing=[k for k in labels if k not in values]
    changed=", ".join(labels[k] for k in updated)
    first=f"{changed} 항목에 반영했습니다. " if updated else mode_hint or "문장에서 확정된 값을 확인하지 못했습니다. "
    if missing:
        nxt=labels[missing[0]]
        following=f"다음 빈칸은 ‘{nxt}’입니다."
    else:
        following="화면에 연결된 칸을 모두 채웠습니다. 원본의 나머지 칸·첨부·서명과 발급 주체를 확인하세요."
    return {"updates":updated,"reply":first+following+workflow.intake_hint(doc,updated)}
