"""Case-scoped document packets, attachments and human-checked value inventory.
No legal fact acceptance, no external transmission, no automatic OCR.
"""
import hashlib,json,re
from contextlib import closing
from datetime import datetime,timezone,date
from pathlib import Path
from db_store import connect,verify_connection
from db_followup import table_exists
from intake_parser import uid
from phase2_store import snapshot,target_case
import phase2_rules
from external_fields import FIELDS,DEFAULT_NEEDS,UNKNOWN,normalize
from external_routes import KINDS,DOCUMENT_NAMES
from internal_records import records

SCHEMA=[
'''CREATE TABLE IF NOT EXISTS external_packets(packet_id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES case_candidates(case_id),context TEXT NOT NULL,kind TEXT NOT NULL,purpose TEXT NOT NULL,sample_id TEXT,created_at TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS external_packet_fields(packet_id TEXT NOT NULL REFERENCES external_packets(packet_id),field_id TEXT NOT NULL,PRIMARY KEY(packet_id,field_id))''',
'''CREATE TABLE IF NOT EXISTS external_documents(document_id TEXT PRIMARY KEY,packet_id TEXT NOT NULL REFERENCES external_packets(packet_id),name TEXT NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,issuer TEXT NOT NULL,document_date TEXT NOT NULL,sample_id TEXT,received_at TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS external_values(review_id INTEGER PRIMARY KEY,packet_id TEXT NOT NULL REFERENCES external_packets(packet_id),document_id TEXT NOT NULL REFERENCES external_documents(document_id),field_id TEXT NOT NULL,raw_value TEXT NOT NULL,normalized_value TEXT,unit TEXT,basis TEXT,method TEXT,locator TEXT NOT NULL,status TEXT NOT NULL,reviewer TEXT NOT NULL,reason TEXT NOT NULL,reviewed_at TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS external_drafts(draft_id TEXT PRIMARY KEY,packet_id TEXT NOT NULL REFERENCES external_packets(packet_id),format TEXT NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,created_at TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS external_events(event_id INTEGER PRIMARY KEY,packet_id TEXT NOT NULL REFERENCES external_packets(packet_id),kind TEXT NOT NULL,reference TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''',
]

def now():return datetime.now(timezone.utc).isoformat()
def sha(blob):return hashlib.sha256(blob).hexdigest()
def encoded(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def init(c):
    for sql in SCHEMA:c.execute(sql)

def projection(bundle,cid):
    c=target_case(bundle,cid)
    s=next((s for s in bundle['sites'] if s['site_id']==c['site_id']),None)
    fields=['name','process_description','physical_state','mixture_type','hazard_declaration']
    waste={f:dict(value=c[f]['value'],evidence_ids=c[f]['evidence_ids']) for f in fields}
    def stable(v):
        if isinstance(v,dict):return {k:stable(x) for k,x in v.items() if k!='updated_at'}
        if isinstance(v,list):return [stable(x) for x in v]
        return v
    for f in ('quantity','components','substances','visible_contamination','separability','industry'):
        waste[f]=stable(c[f])
    waste['reported_code']=stable(c['classification']['reported_code'])
    return dict(case_id=cid,submission_id=c['submission_id'],site_id=c['site_id'],site_address=s['address']['value'] if s else None,
                company={k:bundle['company'][k]['value'] for k in ('name','business_number','contact_name','contact_phone')},
                waste=waste,synthetic=c['synthetic'])

def context(bundle,cid,purpose,sample):return sha(encoded(dict(source=projection(bundle,cid),purpose=purpose,sample_id=sample)))
def packet_key(cid,ctx,kind,fields):return uid('external-040:'+cid+':'+ctx+':'+kind+':'+','.join(sorted(fields)))
def document_key(pid,h,issuer,day,sample):return uid(pid+':document:'+h+':'+sha(encoded([issuer,day,sample])))

def load(c,sid):
    if not table_exists(c,'external_packets'):return []
    out=[]
    for row in c.execute('SELECT p.* FROM external_packets p JOIN case_candidates x USING(case_id) WHERE x.submission_id=? ORDER BY p.created_at,p.packet_id',(sid,)):
        p=dict(row)
        p['fields']=[r[0] for r in c.execute('SELECT field_id FROM external_packet_fields WHERE packet_id=? ORDER BY field_id',(p['packet_id'],))]
        if not p['fields'] or any(f not in FIELDS or FIELDS[f]['kind']!=p['kind'] for f in p['fields']):raise ValueError('외부 문서 묶음 항목 오류')
        if p['packet_id']!=packet_key(p['case_id'],p['context'],p['kind'],p['fields']):raise ValueError('외부 문서 묶음 식별 오류')
        p['documents']=[]
        for d in c.execute('SELECT * FROM external_documents WHERE packet_id=? ORDER BY received_at,document_id',(p['packet_id'],)):
            d=dict(d)
            if sha(d['content'])!=d['sha256'] or d['document_id']!=document_key(p['packet_id'],d['sha256'],d['issuer'],d['document_date'],d['sample_id']):raise ValueError('회신/성적서 원본 해시 오류')
            if d['document_date']:date.fromisoformat(d['document_date'])
            if not d['issuer'].strip() or Path(d['name']).name!=d['name']:raise ValueError('첨부 메타데이터 오류')
            d.pop('content');p['documents'].append(d)
        docmap={d['document_id']:d for d in p['documents']}
        p['reviews']=[]
        for v in c.execute('SELECT * FROM external_values WHERE packet_id=? ORDER BY review_id',(p['packet_id'],)):
            v=dict(v);d=docmap.get(v['document_id'])
            if not d or v['field_id'] not in p['fields'] or not v['locator'].strip() or not v['reviewer'].strip() or not v['reason'].strip():raise ValueError('문서값 근거/건 연결 오류')
            value=normalize(v['field_id'],v['raw_value'],v['unit'])
            if value!=v['normalized_value'] or v['status'] not in ('accepted_candidate','held','unknown'):raise ValueError('문서값 정규화/상태 오류')
            if value is None and v['status']!='unknown':raise ValueError('미확인 값을 채택할 수 없습니다.')
            if v['status']=='accepted_candidate' and (not d['document_date'] or d['issuer'] in UNKNOWN):raise ValueError('문서일/발급기관 미확인')
            if v['status']=='accepted_candidate' and p['kind'] in ('lab','hazard') and (not p['sample_id'] or p['sample_id']!=d['sample_id'] or not v['method'] or (p['kind']=='lab' and v['basis'] not in ('dry','wet','as_received'))):raise ValueError('시료/시험 조건 확인 누락')
            p['reviews'].append(v)
        for d in c.execute('SELECT * FROM external_drafts WHERE packet_id=?',(p['packet_id'],)):
            if sha(d['content'])!=d['sha256']:raise ValueError('발행 전 초안 원본 해시 오류')
        p['events']=[dict(r) for r in c.execute('SELECT * FROM external_events WHERE packet_id=? ORDER BY event_id',(p['packet_id'],))]
        out.append(p)
    return out

def state(c,cid,sid=None):
    check=verify_connection(c,sid);bundle=snapshot(c,check['submission_id'],check)
    target_case(bundle,cid)
    return bundle,load(c,check['submission_id'])

def current_packet(c,cid,pid,sid=None,allow_stale=False):
    bundle,packets=state(c,cid,sid);p=next((p for p in packets if p['packet_id']==pid and p['case_id']==cid),None)
    if not p:raise ValueError('현재 제출/폐기물 건의 문서 묶음이 아닙니다.')
    p['stale']=p['context']!=context(bundle,cid,p['purpose'],p['sample_id'])
    if p['stale'] and not allow_stale:raise ValueError('대상/값이 바뀐 이전 문서 묶음입니다. plan으로 현재 맥락을 재확인하세요.')
    return bundle,p,packets

def latest_values(packets,cid,ctx):
    found={}
    for p in packets:
        if p['case_id']!=cid or p['context']!=ctx:continue
        for v in p['reviews']:
            if v['review_id']>found.get(v['field_id'],{}).get('review_id',0):found[v['field_id']]=v
    return found

def plan(db,cid,purpose,needs=None,sample=None,sid=None):
    needs=sorted(set(DEFAULT_NEEDS if needs is None else needs));purpose=purpose.strip()
    if not purpose or len(purpose)>2000 or not needs or any(f not in FIELDS for f in needs):raise ValueError('판단 목적과 지원하는 필요 항목을 명시하세요.')
    if sample is not None and not sample.strip():raise ValueError('빈 시료 식별자 금지')
    from phase2_store import next_questions
    questions=next_questions(db,sid=sid,case_id=cid)['questions']
    chat=[q['field'] for q in questions if q.get('field') in phase2_rules.FIELDS and phase2_rules.FIELDS[q['field']]['kind']!='evidence'][:3]
    with closing(connect(db,writable=True,create=False)) as c,c:
        c.execute('BEGIN IMMEDIATE');bundle,packets=state(c,cid,sid);case=target_case(bundle,cid)
        sites={s['site_id']:s for s in bundle['sites']}
        if phase2_rules.identity_blocked(case,sites):
            return dict(case_id=cid,blocked=True,chat_fields=chat,packets=[],reason='현장/폐기물 식별을 먼저 확인합니다. 같은 건이 불명확하면 기관 문서를 만들지 않습니다.')
        ctx=context(bundle,cid,purpose,sample);latest=latest_values(packets,cid,ctx)
        covered=[f for f in needs if latest.get(f,{}).get('status')=='accepted_candidate']
        # Existing source-checked reported code may supply this one value, not all applicability conditions.
        fact=case['classification']['reported_code']
        if 'classification.reported_code' in needs and fact['value'] is not None and fact['verification']=='source_checked' and fact['applicability']=='matched':covered.append('classification.reported_code')
        missing=[f for f in needs if f not in covered]
        unavailable=[f for f in missing if latest.get(f,{}).get('status')=='unknown']
        requestable=[f for f in missing if f not in unavailable];result=[]
        if missing:init(c)
        for kind in KINDS:
            fields=[f for f in requestable if FIELDS[f]['kind']==kind]
            if not fields:continue
            pid=packet_key(cid,ctx,kind,fields)
            c.execute('INSERT INTO external_packets VALUES(?,?,?,?,?,?,?) ON CONFLICT(packet_id) DO NOTHING',(pid,cid,ctx,kind,purpose,sample,now()))
            for f in fields:c.execute('INSERT INTO external_packet_fields VALUES(?,?) ON CONFLICT DO NOTHING',(pid,f))
            document=DOCUMENT_NAMES[kind]
            message=', '.join(FIELDS[f]['label'] for f in fields)+' 항목의 근거로 '+document+'를 요청하거나 보유본을 확인합니다.'
            result.append(dict(packet_id=pid,kind=kind,fields=fields,message=message,
                condition='신청 또는 의뢰 전에 적용 조건과 실제 접수기관을 확인합니다.'))
        load(c,case['submission_id'])
        return dict(case_id=cid,blocked=False,chat_fields=chat,covered_fields=sorted(set(covered)),missing_fields=missing,unavailable_fields=unavailable,packets=result,context=ctx,
                    unavailable_note='자료 없음/모름은 같은 맥락에서 다시 요구하지 않습니다. 불검출은 0이 아니며 검출한계·시험법 등 추가 검토가 필요합니다. 다른 근거 확보 또는 보류 범위를 검토하세요.')

def get(db,cid,pid,sid=None):
    with closing(connect(db)) as c:
        c.execute('BEGIN');b,p,packets=current_packet(c,cid,pid,sid,True)
        values=latest_values(packets,cid,p['context'])
        p['still_missing']=[f for f in p['fields'] if p['stale'] or values.get(f,{}).get('status')!='accepted_candidate']
        p['inventory_revision']={f:values.get(f,{}).get('review_id',0) for f in p['fields']}
        p['requestable_fields']=[f for f in p['still_missing'] if values.get(f,{}).get('status')!='unknown']
        p['source']=projection(b,cid)
        p['source']['intake_records']=records(target_case(b,cid))
        return p

def attach(db,cid,pid,file,issuer,document_date,sample=None,sid=None):
    path=Path(file)
    if path.suffix.lower() not in ('.pdf','.docx','.hwpx','.hwp','.png','.jpg','.jpeg','.txt','.csv','.xlsx'):raise ValueError('지원하지 않는 첨부 형식')
    if not 0<path.stat().st_size<=20*1024*1024:raise ValueError('첨부는 1바이트~20MiB')
    if document_date:
        from zoneinfo import ZoneInfo
        d=date.fromisoformat(document_date)
        if d.isoformat()!=document_date or d>datetime.now(ZoneInfo('Asia/Seoul')).date():raise ValueError('문서일 형식/미래 날짜 오류')
    if not issuer.strip():raise ValueError('발급기관이 미확인이면 미확인으로 기록하세요.')
    blob=path.read_bytes();h=sha(blob)
    if len(blob)>20*1024*1024:raise ValueError('첨부 크기 변경')
    with closing(connect(db,writable=True,create=False)) as c,c:
        c.execute('BEGIN IMMEDIATE');_,p,_=current_packet(c,cid,pid,sid,True)
        did=document_key(pid,h,issuer.strip(),document_date,sample)
        old=c.execute('SELECT * FROM external_documents WHERE document_id=?',(did,)).fetchone()
        if old:
            if (old['issuer'],old['document_date'],old['sample_id'])!=(issuer.strip(),document_date,sample):raise ValueError('같은 파일의 발급기관/일자/시료 메타데이터가 다릅니다. 기존 기록을 먼저 확인하세요.')
            return did
        c.execute('INSERT INTO external_documents VALUES(?,?,?,?,?,?,?,?,?)',(did,pid,path.name,h,blob,issuer.strip(),document_date,sample,now()))
        c.execute('INSERT INTO external_events(packet_id,kind,reference,actor,created_at) VALUES(?,?,?,?,?)',(pid,'document_received',did,'사용자 첨부',now()))
        return did

def record_value(db,cid,pid,did,field,value,locator,reviewer,reason,expected_review=0,unit=None,basis=None,method=None,confirm=False,sid=None):
    if not all(x.strip() for x in (locator,reviewer,reason)):raise ValueError('페이지/위치, 검토자, 기록 사유가 필요합니다.')
    normalized=normalize(field,value,unit)
    with closing(connect(db,writable=True,create=False)) as c,c:
        c.execute('BEGIN IMMEDIATE');_,p,packets=current_packet(c,cid,pid,sid)
        if field not in p['fields']:raise ValueError('이 문서 묶음에서 요청한 항목이 아닙니다.')
        d=next((d for d in p['documents'] if d['document_id']==did),None)
        if not d:raise ValueError('문서와 폐기물 건/문서 묶음 연결 불일치')
        prior=latest_values(packets,cid,p['context']).get(field)
        if expected_review!=(prior['review_id'] if prior else 0):raise ValueError('REVIEW_CONFLICT: 현재 검토 기록 번호를 확인하세요.')
        status='unknown' if normalized is None else ('accepted_candidate' if confirm else 'held')
        if status=='accepted_candidate' and (not d['document_date'] or d['issuer'] in UNKNOWN):raise ValueError('문서일/발급기관 미확인 상태입니다.')
        if status=='accepted_candidate' and p['kind'] in ('lab','hazard'):
            if not p['sample_id'] or p['sample_id']!=d['sample_id'] or not method or not method.strip() or (p['kind']=='lab' and basis not in ('dry','wet','as_received')):raise ValueError('시험값의 시료 일치·건조/습윤 등 기준·시험법을 확인해야 합니다.')
        data=(pid,did,field,value.strip(),normalized,unit,basis,method,locator.strip(),status,reviewer.strip(),reason.strip(),now())
        cur=c.execute('INSERT INTO external_values(packet_id,document_id,field_id,raw_value,normalized_value,unit,basis,method,locator,status,reviewer,reason,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',data)
        return cur.lastrowid

def submitted(db,cid,pid,reference,actor,sid=None):
    if not reference.strip() or not actor.strip():raise ValueError('사용자가 확인한 접수번호/참조와 기록자가 필요합니다.')
    with closing(connect(db,writable=True,create=False)) as c,c:
        c.execute('BEGIN IMMEDIATE');current_packet(c,cid,pid,sid)
        if not c.execute('SELECT 1 FROM external_drafts WHERE packet_id=?',(pid,)).fetchone():raise ValueError('작성 초안을 먼저 확인하세요.')
        c.execute('INSERT INTO external_events(packet_id,kind,reference,actor,created_at) VALUES(?,?,?,?,?)',(pid,'user_reported_submission',reference.strip(),actor.strip(),now()))
