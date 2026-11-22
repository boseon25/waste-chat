"""API 키 없이 대화 분기, 서식 선택, PDF 생성 및 원본 구성을 검사한다."""
from pathlib import Path
from io import BytesIO
import base64
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pypdf import PdfReader
from services import catalog, conversation, finish, pdf_writer


def check_chat_usage(doc):
    site = next(field for field in doc['fields'] if field['key'] == 'site_name')
    question = '칸을 누르고 내가 입력할 것만 입력하면 되는거야?'
    for selection in ({'pinned_field': site['key']}, {}):
        for message in (question, question.rstrip('?'), '빈칸 먼저 눌러서 내가 가진 정보만 쓰면 돼',
                        '필드 하나 클릭하고 텍스트만 쓰면 되는 거지', '그냥 쓸 칸만 누르면 돼?'):
            saved = {'site_name': '서부 자원센터'}
            request = {'message': message, 'values': saved, 'focus': site['key'], **selection}
            response = conversation.update(doc, request, None)
            assert not response['updates'] and not response.get('defer_field') and not response.get('open_doc')
            assert saved == {'site_name': '서부 자원센터'}
            reply = response['reply']
            assert any(word in reply for word in ('채팅', '대화', '보내', '전송')), reply
            assert any(word in reply for word in ('값', '내용', '입력')), reply

    request = {'values': {}, 'focus': site['key'], 'pinned_field': site['key']}
    for question in ('이 칸에는 무엇을 적나요?', '공장 이름만 적으면 되나요?'):
        field_help = conversation.update(doc, {**request, 'message': question}, None)
        assert not field_help['updates'] and '공장' in field_help['reply']
    for name in ('서부 자원센터', '전산 입력 장비 제조동', '방법론 연구실'):
        assert conversation.update(doc, {**request, 'message': name}, None)['updates'] == {site['key']: name}


def check_original_cells(docs):
    from hashlib import sha256
    from services.original_cells import TEMPLATES
    for doc_id, (digest, cells) in TEMPLATES.items():
        doc = docs[doc_id]
        assert doc["original_cells"] == cells
        raw = (catalog.ROOT / doc["file"]).read_bytes()
        assert sha256(raw).hexdigest() == digest
        assert len({c["key"] for c in cells}) == len(cells)
        pages = PdfReader(BytesIO(raw)).pages
        for cell in cells:
            width, height, _ = finish._view(pages[cell["page"]])
            x, y, right, bottom = cell["box"]
            l, t, r, b = cell["write"]
            assert 0 <= x <= l < r <= right <= width and 0 <= y <= t < b <= bottom <= height, cell["label"]
    original = docs["analysis"]
    prompt = {"original_position": True, "original_cell": "row2_quantity", "message": "이 칸에 무엇을 적나요?"}
    reply = conversation.update(original, prompt, None)
    assert "placement_text" not in reply and "kg" in reply["reply"]
    source = (catalog.ROOT / original["file"]).read_bytes()
    mark = {"page": 0, "cellKey": "applicant_name", "size": 10, "text": "서해자원"}
    case = {"doc": "analysis", "pdf": base64.b64encode(source).decode(), "placements": [mark]}
    page = PdfReader(BytesIO(finish.export(case))).pages[0]
    assert "서해자원" in page.extract_text()
    for invalid in ([mark,mark], [dict(mark, cellKey="row1_code", text="12-345")]):
        try:
            finish.export(dict(case, placements=invalid))
        except ValueError:
            pass
        else:
            raise AssertionError("잘못된 칸 값이 출력되었습니다.")


def main():
    docs=catalog.DOCS
    assert set(docs)=={doc for ctx in catalog.CONTEXTS for doc in ctx['docs']}
    originals=list(catalog.OFFICIAL.rglob('*.pdf'))
    assert len(originals)==17 and all(f.with_suffix('.hwp').is_file() for f in originals)
    req={'values':{},'message':'업체명 (상호)=다온자원'}
    assert conversation.update(docs['intake'],req,None)['updates']=={'generator_name':'다온자원'}
    check_chat_usage(docs['intake'])
    check_original_cells(docs)
    pinned = {"focus":"generator_name","pinned_field":"generator_name","values":{}}
    for question in ("여기에는 무엇을 적나요?", "업체명 (상호)=모르겠어요"):
        response = conversation.update(docs["intake"],{**pinned,"message":question},None)
        assert not response["updates"] and response["reply"]
    assert conversation.update(docs["intake"],{**pinned,"message":"다온자원"},None)["updates"] == {"generator_name":"다온자원"}
    assert not conversation.update(docs["analysis"],{**pinned,"message":"다온자원"},None)["updates"]
    placed = {"original_position": True, "values": {}}
    original = docs["analysis"]
    assert conversation.update(original, {**placed, "message": "다온자원"}, None)["placement_text"] == "다온자원"
    assert "placement_text" not in conversation.update(original, {"message": "다온자원"}, None)
    for question in ("원본 pdf에 대해서는 입력이 안되는 것 같은데", "칸을 누르고 내가 입력할 것만 입력하면 되는거야?"):
        result = conversation.update(original, {**placed, "message": question}, None)
        assert "placement_text" not in result and "작성 사본" in result["reply"]
    consent_question = {**placed, "message": "이러한 원본 서류들을 이렇게 컴퓨터로 작성해도 되는거야?"}
    signed = conversation.update(original, consent_question, None)
    assert "placement_text" not in signed and "분석전문기관" in signed["reply"] and "제출인" in signed["reply"]
    assert "신고인" in conversation.update(docs["form6"], consent_question, None)["reply"]
    go={'message':'건설 제7호 열어줘','values':{}}
    assert conversation.update(docs['intake'],go,None)['open_doc']=='cplan'
    assert 'open_doc' not in conversation.update(docs['intake'],{'message':'제7호 열어줘','values':{}},None)
    total=0
    for d in docs.values():
        if d.get('file'):
            assert (catalog.ROOT/d['file']).is_file()
        if not d['fields']: continue
        names={f['key'] for f in d['fields']}
        field=next((x for x in ('generator_name','applicant_name') if x in names),None)
        vals={field:'다온자원'} if field else {}
        content=pdf_writer.render_document(d,vals)
        pages=PdfReader(BytesIO(content)).pages
        assert pages
        if field: assert '다온자원' in ''.join(p.extract_text() for p in pages), d['id']
        total+=1
    assert 'waste_legal_name' in {f['key'] for f in docs['form6']['fields']}
    assert 'waste_name' not in {f['key'] for f in docs['form6']['fields']}
    print(f'확인 완료 — 원본 {len(originals)}종, 작성 화면 {total}개, 기초 정보 및 서식 선택')
if __name__=='__main__':
    main()
