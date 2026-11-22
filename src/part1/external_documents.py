"""Human-editable preparation documents, not official signed application forms."""
from contextlib import closing
from io import BytesIO
from pathlib import Path
import json
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn
from db_store import connect
from external_store import current_packet,get,now,sha,uid,latest_values
from external_fields import FIELDS
from external_routes import status, DOCUMENT_NAMES, DRAFT_NAMES, RECORD_INSTRUCTIONS
from internal_records import records
import official_forms


def display(v):
    if v is None:return '미확인 (추정하여 채우지 않음)'
    if v is True:return '있음/가능 — 사용자 진술'
    if v is False:return '없음/불가능 — 사용자 진술, 시험상 불검출이 아님'
    return str(v)

def content(packet,route,forms=()):
    if packet['stale']:raise ValueError('맥락이 바뀐 묶음으로 새 문의 초안을 만들 수 없습니다.')
    if not packet['requestable_fields']:raise ValueError('추가 요청 가능한 값이 없습니다. 대조 완료 또는 자료 없음/불검출 보류 항목은 반복 요청하지 않습니다.')
    s=packet['source'];w=s['waste'];q=w['quantity']
    kind=packet['kind']
    title=DRAFT_NAMES[kind]+' — 미제출 초안'
    lines=['# '+title,'가상 시연 자료 — 실제 제출 금지' if s['synthetic'] else '사용자 검토 전 초안 — 자동 접수/발행 아님',
           '폐기물 건: '+packet['case_id'],'문서 묶음: '+packet['packet_id'],'목적: '+packet['purpose'],
           '## 1. 입력서·채팅에서 가져온 정보 (사용자 진술, 기관 인증 아님)',
           '업체: '+display(s['company']['name']),'사업자번호: '+display(s['company']['business_number']),
           '연락 담당자: '+display(s['company']['contact_name']),'연락처: '+display(s['company']['contact_phone']),
           '현장: '+display(s['site_address']),'폐기물명: '+display(w['name']['value']),
           '발생공정: '+display(w['process_description']['value']),'형태: '+display(w['physical_state']['value']),
           '이번 처리량: '+display(q['value'])+' '+(q['unit'] or ''),
           '실제 측정일: '+display(q['measured_at']['value']),'측정 방법: '+display(q['method']['value']),
           '구성 재질: '+(', '.join(display(x['name']['value'])+' / 비율 '+display(x['proportion_percent']['value'])+('%' if x['proportion_percent']['value'] is not None else '') for x in w['components']) or '미확인'),
           '기존 기재 분류번호 (현재 건 적용 미확인): '+display(w['reported_code']['value']),
           '유해물질 포함 진술: '+display(w['hazard_declaration']['value']),
           '혼합 상태: '+display({'single':'단일 재질','mixed':'따로 섞임','composite':'코팅·접착 등으로 붙음',
                                  'multiple_unspecified':'혼합 형태 미확인'}.get(w['mixture_type']['value'])),
           '시료 식별자: '+display(packet['sample_id']),
           '시료 채취일/채취자/시험 기준: [기관과 협의 후 기재 — 작성일로 대신하지 않음]',
           ]
    intake=s.get('intake_records',{})
    for key,label in [('raw_materials','사용 원료'),('chemicals','사용 약품'),('label_material','라벨상 재질')]:
        item=intake.get(key,{})
        if item.get('value'):
            lines.append(label+' '+item['value']+' / 사용자 기재 근거 '+display(item.get('source')))
    for item in intake.get('components',[]):
        if item['percent'] is not None:
            lines.append('기재된 실제 구성비 '+item['name']+' '+item['percent']+'% / '+item['source'])
    lines += ['## 2. 이번에 필요한 값',
              '대상 문서 '+DOCUMENT_NAMES[kind],
              '확인할 값 '+', '.join(FIELDS[f]['label'] for f in packet['requestable_fields'])]
    for field in packet['requestable_fields']:lines.append('- '+FIELDS[field]['label']+' ['+field+']')
    lines.append(RECORD_INSTRUCTIONS[kind])
    lines+=['## 3. 문의처·접수 경로','기관: '+route['provider'],
            '기관/지정현황 확인: '+route['discovery_url'],
            '공식 경로 안내: '+route['source_url'],
            (('실제 접수 서비스 URL: ' if kind=='authority' else '접수 연결: ')+
             (route['submission_url'] or route['submission_note'])),
            '현재 상태: '+route['monitor_result']+' / '+route['submission_note'],
            '메뉴 경로: '+' → '.join(route['menu']),
            '실사이트 확인 범위: '+route['browser_scope'],
            '브라우저 확인일: '+route['verified_on']+' / 경로 확인 기록: '+route['last_checked_at'],
            '대안: '+route['fallback'],
            '공식 접수·수령 절차: '+route['procedure_url'],'## 4. 작성 문서·첨부 준비']
    lines[-1:-1] = [label+' '+url for label,url in route.get('extra_links',[])]
    lines+=['- '+x for x in route['preparation']]
    if forms:
        lines.append('함께 제공한 기관 배포 원본 (official_forms 폴더)')
        lines.extend(f"- {entry['filename']} · {entry['usage']} · 배포처 {entry['source_url']}" for entry in forms)
    lines+=['## 5. 접수부터 회신·성적서 수령까지']
    lines += [str(i)+'. '+x for i,x in enumerate(route['steps'],1)]
    lines+=['## 6. 접수 전에 사용자가 확인할 사항',
            '수신 기관/지점: [소관·시험 가능 항목 확인 후 기재]',
            '개인정보 동의·서명·인증·결제·제출: 사용자가 직접 수행. 이 문서는 동의/서명을 대신하지 않습니다.',
            '접수번호: [실제 접수 후 기재] / 회신·성적서 수령 방식: [사용자 확인]',
            '기관 공식 서식은 위 공식 안내 페이지에서 받으세요. 이 준비 초안은 그 서식을 대체하지 않습니다.',
            '회신/성적서 첨부만으로 필요한 값이 채워지지는 않습니다. 위치·시료·기간·현장 적용성을 대조한 항목만 문서값 목록에 반영합니다.',
            '## 7. 한계']+['- '+x for x in route['limits']]
    return '\n\n'.join(lines)+'\n'

def docx_bytes(text):
    doc=Document();style=doc.styles['Normal'];style.font.name='맑은 고딕';style.font.size=Pt(10)
    style.element.rPr.rFonts.set(qn('w:eastAsia'),'맑은 고딕')
    for line in text.split('\n\n'):
        if line.startswith('# '):doc.add_heading(line[2:],0)
        elif line.startswith('## '):doc.add_heading(line[3:],1)
        else:doc.add_paragraph(line.strip())
    out=BytesIO();doc.save(out);return out.getvalue()

def draft(db,cid,pid,outdir,registry=None,sid=None):
    p=get(db,cid,pid,sid);route=status(p['kind'],registry)
    forms = official_forms.catalog(p['kind'])
    text=content(p,route,forms);files={'request.md':text.encode(),'request.docx':docx_bytes(text)}
    originals = {entry['filename']: (official_forms.DIRECTORY / entry['filename']).read_bytes() for entry in forms}
    out=Path(outdir)
    if out.exists():raise ValueError('새 출력 폴더를 지정하세요. 기존 초안은 덮어쓰지 않습니다.')
    with closing(connect(db,writable=True,create=False)) as c,c:
        c.execute('BEGIN IMMEDIATE');_,current,all_packets=current_packet(c,cid,pid,sid)
        latest=latest_values(all_packets,cid,current['context'])
        revision={f:latest.get(f,{}).get('review_id',0) for f in current['fields']}
        if revision!=p['inventory_revision']:raise ValueError('초안 생성 중 검토 기록이 바뀌었습니다. 다시 생성하세요.')
        for fmt,blob in files.items():
            h=sha(blob);did=uid(pid+':draft:'+fmt+':'+h)
            c.execute('INSERT INTO external_drafts VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING',(did,pid,fmt,h,blob,now()))
        c.execute('INSERT INTO external_events(packet_id,kind,reference,actor,created_at) VALUES(?,?,?,?,?)',(pid,'draft_created',sha(files['request.md']),'로컬 초안 생성',now()))
        out.mkdir(parents=True,exist_ok=False)
        for name,blob in files.items():
            with (out/name).open('xb') as f:f.write(blob)
        if originals:
            form_dir = out / 'official_forms'
            form_dir.mkdir()
            for name, blob in originals.items():
                (form_dir / name).write_bytes(blob)
    return {name:str(out/name) for name in files} | {
        'official_forms': [str(out / 'official_forms' / name) for name in originals],
    }

def export_case(db,cid,sid=None):
    from guided_review import decisions, history_documents
    from external_store import state,projection,context
    from phase2_store import target_case
    with closing(connect(db)) as c:
        c.execute('BEGIN');b,packets=state(c,cid,sid)
        case=target_case(b,cid);own=[]
        for p in packets:
            if p['case_id']!=cid:continue
            p['stale']=p['context']!=context(b,cid,p['purpose'],p['sample_id']);own.append(p)
        return dict(export_kind='phase3-document-workflow-review',schema_version='0.5.0',published=False,
                    case_version=case['case_version'],draft_revision=case['draft_revision'],
                    workflow_revision=sum(1+len(p['events'])+len(p['reviews']) for p in own),
                    case=case,source=projection(b,cid),document_packets=own,intake_records=records(case),
                    guidance_decisions=decisions(c,b,cid,include_old=True),history_documents=history_documents(c,cid),
                    note='문서값 대조 목록은 법적 사실 승인/분류코드 확정과 별도입니다. 기존 DB-first export와 구분되는 건별 문서 워크플로 검토본입니다.')
