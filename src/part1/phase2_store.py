"""Phase-2 requests, typed append-only answers, optimistic per-question revisions."""
from contextlib import closing
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import re
from intake_parser import uid
from db_followup import table_exists
import phase2_rules as rules

SCHEMA=[
'''CREATE TABLE IF NOT EXISTS phase2_requests(
 request_id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES case_candidates(case_id),
 field TEXT NOT NULL,context TEXT NOT NULL,route TEXT,priority INTEGER NOT NULL,
 asked_at TEXT NOT NULL,rule_version TEXT NOT NULL,asked_reason TEXT NOT NULL,
 UNIQUE(case_id,field,context))''',
'''CREATE TABLE IF NOT EXISTS phase2_answers(
 request_id TEXT NOT NULL REFERENCES phase2_requests(request_id),revision INTEGER NOT NULL CHECK(revision>=1),
 raw_value TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('answered','unavailable')),
 normalized_text TEXT,normalized_bool INTEGER CHECK(normalized_bool IN(0,1)),
 quantity_value TEXT,quantity_unit TEXT,quantity_dimension TEXT,conversion_rule TEXT,
 reason TEXT NOT NULL,answered_at TEXT NOT NULL,PRIMARY KEY(request_id,revision))''',
'''CREATE TABLE IF NOT EXISTS phase2_sites(
 site_id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES case_candidates(case_id),
 company_id TEXT NOT NULL REFERENCES companies(company_id),request_id TEXT NOT NULL,revision INTEGER NOT NULL,
 address TEXT NOT NULL,FOREIGN KEY(request_id,revision) REFERENCES phase2_answers(request_id,revision))''',
]


def columns(value):
    out=dict(normalized_text=None,normalized_bool=None,quantity_value=None,quantity_unit=None,quantity_dimension=None,conversion_rule=None)
    if isinstance(value,bool):out['normalized_bool']=int(value)
    elif isinstance(value,dict):
        for k in ('value','unit','dimension'):out['quantity_'+k]=value[k]
        out['conversion_rule']=value['conversion_rule']
    elif value is not None:out['normalized_text']=value
    return out


def load_state(conn,sid):
    if not table_exists(conn,'phase2_requests'):return dict(requests=[],sites=[])
    result=[]
    for row in conn.execute('SELECT r.* FROM phase2_requests r JOIN case_candidates c USING(case_id) WHERE c.submission_id=? ORDER BY r.asked_at,r.request_id',(sid,)):
        r=dict(row)
        if r['field'] not in rules.FIELDS or r['rule_version']!=rules.VERSION:
            raise ValueError('지원하지 않는 2단계 규칙/필드')
        if r['request_id']!=rules.request_id(r['case_id'],r['field'],r['context']):
            raise ValueError('2단계 질문 식별자 불일치')
        if not r['asked_reason'].strip():raise ValueError('질문 사유 누락')
        if r['priority']!=rules.FIELDS[r['field']]['priority']:
            raise ValueError('질문 우선순위 변조')
        if r['route'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',r['route']):raise ValueError('경로 ID 형식 오류')
        prev=datetime.fromisoformat(r['asked_at'])
        if prev.utcoffset() is None:raise ValueError('질문 시각 시간대 누락')
        answers=[]
        for n,a in enumerate(conn.execute('SELECT * FROM phase2_answers WHERE request_id=? ORDER BY revision',(r['request_id'],)),1):
            a=dict(a);dt=datetime.fromisoformat(a['answered_at'])
            if dt.utcoffset() is None or dt<prev or a['revision']!=n:raise ValueError('답변 순번/시각 불일치')
            value=rules.normalize(r['field'],a['raw_value'],today=dt.astimezone(ZoneInfo('Asia/Seoul')).date())
            if any(a[k]!=v for k,v in columns(value).items()):raise ValueError('답변 원문·정규화 값 불일치')
            if a['status']!=('unavailable' if value is None else 'answered'):raise ValueError('답변 상태 불일치')
            if not a['reason'].strip():raise ValueError('답변/정정 사유 누락')
            if answers and a['raw_value']==answers[-1]['raw_value']:raise ValueError('연속 동일 답변 중복')
            a['value']=value;answers.append(a);prev=dt
        r.update(history=answers,revision=len(answers),status=answers[-1]['status'] if answers else 'open')
        result.append(r)
    sites=[dict(s) for s in conn.execute('SELECT s.* FROM phase2_sites s JOIN case_candidates c ON s.case_id=c.case_id WHERE c.submission_id=? ORDER BY s.site_id',(sid,))]
    reqs={r['request_id']:r for r in result}
    expected=set()
    for r in result:
        if r['field']=='site_address':
            for a in r['history']:
                if a['value'] is not None:expected.add(uid(f"{r['request_id']}:site:{a['revision']}"))
    if {s['site_id'] for s in sites}!=expected:raise ValueError('현장 보완 행 누락/추가')
    for s in sites:
        r=reqs.get(s['request_id']);a=r['history'][s['revision']-1] if r and 0<s['revision']<=len(r['history']) else None
        co=conn.execute('SELECT company_id FROM case_candidates WHERE case_id=?',(s['case_id'],)).fetchone()
        if not a or r['field']!='site_address' or s['site_id']!=uid(f"{s['request_id']}:site:{s['revision']}") or r['case_id']!=s['case_id'] or a['value']!=s['address'] or not co or co[0]!=s['company_id']:
            raise ValueError('현장 보완/답변 연결 불일치')
    return dict(requests=result,sites=sites)


def snapshot(conn,sid,report=None):
    from db_store import verify_connection,get_submission,load_rows
    from db_export import build_export
    import db_followup,db_guidance
    from guidance_export import apply_guidance
    from phase2_export import apply_phase2
    report=report or verify_connection(conn,sid)
    sid=report['submission_id']
    bundle=build_export(get_submission(conn,sid),load_rows(conn,sid),report,db_followup.load_requests(conn,sid))
    guides=db_guidance.load_requests(conn,sid)
    bundle=apply_guidance(bundle,guides)
    return apply_phase2(bundle,load_state(conn,sid),guides)


def target_case(bundle,case_id):
    case=next((c for c in bundle['cases'] if c['case_id']==case_id),None)
    if case is None:raise ValueError('현재 제출에서 해당 폐기물을 찾을 수 없습니다.')
    return case


def guidance_for(guides,cid,topic,context,initial_context):
    marker='[phase2-context:'+context+']'
    matched=[r for r in guides if r['case_id']==cid and r['topic']==topic and r['blocking_scope']=='classification'
             and (marker in r['purpose'] or ('[phase2-context:' not in r['purpose'] and context==initial_context))]
    return matched[-1] if matched else None


def initial_bundle(conn,sid,report):
    from db_store import get_submission,load_rows
    from db_export import build_export
    return build_export(get_submission(conn,sid),load_rows(conn,sid),report)


def next_questions(db,sid=None,case_id=None,route=None,limit=3):
    from db_store import connect,verify_connection
    import db_guidance,db_followup
    if not 1<=limit<=3:raise ValueError('질문은 한 번에 1~3개입니다.')
    if route and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',route):raise ValueError('경로 ID는 영문/숫자/_/- 1~80자입니다.')
    with closing(connect(db,writable=True,create=False)) as conn,conn:
        conn.execute('BEGIN IMMEDIATE');report=verify_connection(conn,sid);sid=report['submission_id']
        bundle=snapshot(conn,sid,report);sites={s['site_id']:s for s in bundle['sites']}
        initial={c['case_id']:c for c in initial_bundle(conn,sid,report)['cases']}
        if case_id:target_case(bundle,case_id)
        states=load_state(conn,sid);existing={r['request_id']:r for r in states['requests']}
        guides=db_guidance.load_requests(conn,sid);legacy={r['case_id']:r for r in db_followup.load_requests(conn,sid)}
        pending=[];blocked=[]
        for c in bundle['cases']:
            if case_id and c['case_id']!=case_id:continue
            if rules.identity_blocked(c,sites):blocked.append(dict(case_id=c['case_id'],reason='현장/폐기물 식별 미확인: 이 건의 후속 질문은 보류'))
            for field in rules.candidates(c,sites,route):
                spec=rules.FIELDS[field];ctx=rules.dependency(c,field,route)
                rid=rules.request_id(c['case_id'],field,ctx)
                row=existing.get(rid)
                if row and row['status']!='open':continue
                source='phase2'
                if spec['kind']=='evidence':
                    g=guidance_for(guides,c['case_id'],spec['topic'],ctx,rules.dependency(initial[c['case_id']],field,route))
                    if g:
                        if g['status']!='open':continue
                        source='guidance';rid=g['request_id']
                if field=='quantity.measured_at' and c['case_id'] in legacy:
                    old=legacy[c['case_id']]
                    # A currently open legacy request is reused, not duplicated. Stale old answers are not reused.
                    if old['answer_text'] is None and ctx==rules.dependency(initial[c['case_id']],field,route):
                        source='measurement';rid=old['request_id']
                priority=spec['priority']
                if field=='quantity' and ('QUANTITY_NEEDS_REVIEW' in c['limitations'] or rules.quantity_conflict(c)):priority=5
                if field in ('mixture_type','hazard_declaration') and any('UNCHECKED_OR_MULTIPLE' in x for x in c['limitations']):priority=5
                reason='누락/불일치 규칙: '+field
                question=spec['question']
                if field=='quantity' and rules.quantity_conflict(c):
                    reason='처리량과 명시된 실제 측정량 불일치'
                    question=f"입력서 처리량 {c['quantity']['value']} {c['quantity']['unit']}와 측정기록 {c['quantity']['method']['value']}가 다릅니다. "+question
                elif priority==5:
                    reason='값 형식/체크 선택 불일치: '+field
                pending.append(dict(request_id=rid,case_id=c['case_id'],field=field,context=ctx,source=source,
                                    priority=priority,question=question,reason=reason,label=spec['label'],route=route,
                                    mode=spec['kind'],expected_revision=0,
                                    current_value=rules.field_value(c,field,sites) if spec['kind']!='evidence' else None))
        pending.sort(key=lambda r:(r['priority'],r['case_id'],r['field']))
        selected=pending[:limit]
        if any(q['source']=='phase2' for q in selected):
            for sql in SCHEMA:conn.execute(sql)
        for q in selected:
            if q['source']=='phase2':
                conn.execute('INSERT INTO phase2_requests VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING',
                    (q['request_id'],q['case_id'],q['field'],q['context'],q['route'],rules.FIELDS[q['field']]['priority'],
                     datetime.now(timezone.utc).isoformat(),rules.VERSION,q['reason']))
        return dict(questions=selected,blocked_cases=blocked,pending_count=len(pending),
                    note='사용자 진술은 공인 분석/법적 확정이 아닙니다. 이미 모름·자료 없음으로 답한 동일 맥락 질문은 반복하지 않습니다.')


def manual_question(db,cid,field,reason,sid=None,route=None):
    from db_store import connect,verify_connection
    if field not in rules.FIELDS or not reason.strip():raise ValueError('지원 필드와 명시적인 재확인 사유가 필요합니다.')
    if route and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',route):raise ValueError('경로 ID 형식 오류')
    if field=='separability' and not route:raise ValueError('분리 가능성은 선택한 route ID가 필요합니다.')
    with closing(connect(db,writable=True,create=False)) as conn,conn:
        conn.execute('BEGIN IMMEDIATE');report=verify_connection(conn,sid);sid=report['submission_id']
        b=snapshot(conn,sid,report);c=target_case(b,cid)
        if field not in ('site_address','name') and rules.identity_blocked(c,{s['site_id']:s for s in b['sites']}):
            raise ValueError('대상 현장/폐기물부터 확인해야 합니다.')
        context=rules.dependency(c,field,route);rid=rules.request_id(cid,field,context)
        for sql in SCHEMA:conn.execute(sql)
        conn.execute('INSERT INTO phase2_requests VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING',
            (rid,cid,field,context,route,rules.FIELDS[field]['priority'],datetime.now(timezone.utc).isoformat(),rules.VERSION,reason.strip()))
        result=get_request(conn,sid,rid,cid)
        result['current_value']=rules.field_value(c,field,{s['site_id']:s for s in b['sites']}) if rules.FIELDS[field]['kind']!='evidence' else None
        return result


def get_request(conn,sid,rid,cid):
    r=next((r for r in load_state(conn,sid)['requests'] if r['request_id']==rid),None)
    if not r or r['case_id']!=cid:raise ValueError('질문과 폐기물 건 연결이 일치하지 않습니다.')
    return r


def answer(db,rid,cid,raw,expected_revision,sid=None,*,correct=False,reason='사용자 최초 답변'):
    from db_store import connect,verify_connection
    if not reason.strip():raise ValueError('답변/정정 사유가 필요합니다.')
    with closing(connect(db,writable=True,create=False)) as conn,conn:
        conn.execute('BEGIN IMMEDIATE');report=verify_connection(conn,sid);sid=report['submission_id']
        r=get_request(conn,sid,rid,cid)
        if rules.FIELDS[r['field']]['kind']=='evidence':raise ValueError('증빙 항목은 documents 명령으로 자료 요청에 연결하세요. 번호/법적 판단을 직접 채우지 않습니다.')
        b=snapshot(conn,sid,report);case=target_case(b,cid);sites={s['site_id']:s for s in b['sites']}
        if r['context']!=rules.dependency(case,r['field'],r['route']):raise ValueError('질문 맥락이 바뀌었습니다. next로 현재 건을 다시 확인하세요.')
        if r['field'] not in ('site_address','name') and rules.identity_blocked(case,sites):raise ValueError('현장/폐기물 식별이 먼저 필요합니다.')
        raw=raw.strip();value=rules.normalize(r['field'],raw)
        if r['field']=='quantity.method' and value is not None:
            import copy
            proposed=copy.deepcopy(case);proposed['quantity']['method']['value']=value
            if rules.quantity_conflict(proposed):
                raise ValueError('측정 방법에 명시한 실제 측정량이 현재 처리량과 다릅니다. 처리량부터 명시적으로 재확인하세요.')
        if r['history'] and raw==r['history'][-1]['raw_value']:return False
        if expected_revision!=r['revision']:raise ValueError('REVISION_CONFLICT: 이력을 다시 읽고 현재 차수로 답하세요.')
        if correct and not r['history']:raise ValueError('최초 답변은 answer로 저장하세요.')
        if not correct and r['history']:raise ValueError('기존 답변 변경은 correct로 저장하세요.')
        if correct and reason=='사용자 최초 답변':raise ValueError('정정 사유를 명시하세요.')
        normalized=columns(value);rev=r['revision']+1;dt=datetime.now(timezone.utc).isoformat()
        data=dict(request_id=rid,revision=rev,raw_value=raw,status='unavailable' if value is None else 'answered',
                  **normalized,reason=reason.strip(),answered_at=dt)
        conn.execute(f"INSERT INTO phase2_answers ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",tuple(data.values()))
        if r['field']=='site_address' and value is not None:
            conn.execute('INSERT INTO phase2_sites VALUES (?,?,?,?,?,?)',
                (uid(f'{rid}:site:{rev}'),cid,case['company_id'],rid,rev,value))
        load_state(conn,sid)
        snapshot(conn,sid,verify_connection(conn,sid))
        return True


def documents(db,rid,cid,sid=None):
    from db_store import connect,verify_connection
    import db_guidance
    with closing(connect(db)) as conn:
        conn.execute('BEGIN');report=verify_connection(conn,sid);sid=report['submission_id']
        r=get_request(conn,sid,rid,cid);spec=rules.FIELDS[r['field']]
        if spec['kind']!='evidence':raise ValueError('이 질문은 사용자 답변형입니다.')
        b=snapshot(conn,sid,report);c=target_case(b,cid)
        if rules.identity_blocked(c,{s['site_id']:s for s in b['sites']}):raise ValueError('대상 현장/폐기물부터 확인해야 합니다.')
        if r['context']!=rules.dependency(c,r['field'],r['route']):raise ValueError('오래된 질문 맥락입니다.')
        original=target_case(initial_bundle(conn,sid,report),cid)
        existing=guidance_for(db_guidance.load_requests(conn,sid),cid,spec['topic'],r['context'],rules.dependency(original,r['field'],r['route']))
        if existing:return existing
    return db_guidance.create_request(db,cid,spec['topic'],'2단계 규칙: '+spec['label']+'의 현재 건 적용 자료 확인 [phase2-context:'+r['context']+']','classification',sid=sid)


def history(db,rid,cid,sid=None):
    from db_store import connect,verify_connection
    with closing(connect(db)) as conn:
        conn.execute('BEGIN');report=verify_connection(conn,sid)
        return get_request(conn,report['submission_id'],rid,cid)
