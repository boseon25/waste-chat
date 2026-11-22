"""Overlay validated, case-scoped user assertions. Original DOCX/DB rows are immutable."""
import copy
import json
from datetime import datetime
from decimal import Decimal
from intake_parser import uid
from models import Evidence,Locator
from review_contract import DBReviewExport
import phase2_rules as rules


def eid(rid,rev):return uid(f'{rid}:answer:{rev}')


def fact(value,a,r):
    return dict(value=value,raw_value=a['raw_value'],basis='user_report',
        status='conditional' if value is not None else 'needs_info',verification='unverified',
        evidence_ids=[eid(r['request_id'],a['revision'])],applicability='matched',updated_at=a['answered_at'])


def quantity_key(q):
    val=q.get('value')
    return (str(Decimal(val).normalize()) if val is not None else None,q.get('unit'),q.get('dimension'))


def apply_phase2(bundle,state,guides):
    from phase2_store import guidance_for
    b=copy.deepcopy(bundle);bycase={c['case_id']:c for c in b['cases']}
    original={c['case_id']:c for c in bundle['cases']}
    reqs=state['requests']
    for r in reqs:
        c=bycase[r['case_id']]
        c['draft_revision']+=r['revision']
        for a in r['history']:
            key=eid(r['request_id'],a['revision'])
            e=Evidence(evidence_id=key,kind='user_message',message_id=key,
                source_locator=Locator(kind='message',message_id=key),raw_excerpt=a['raw_value'],
                applicable_case_ids=[c['case_id']],applicability='matched').model_dump(mode='json')
            c['evidence'].append(e);b['evidence'].append(e)
            c['assertions'].append(dict(assertion_id=uid(key+':assertion'),field_path=r['field'],raw_value=a['raw_value'],
                normalized_value_json=json.dumps(a['value'],ensure_ascii=False),evidence_ids=[key],
                applicability='matched',disposition='proposed',reason=a['reason']+'; 형식 검증을 통과한 사용자 진술이며 사실/법적 확정 아님'))
    reqmap={r['request_id']:r for r in reqs}
    for s in state['sites']:
        r=reqmap[s['request_id']];a=r['history'][s['revision']-1]
        b['sites'].append(dict(site_id=s['site_id'],company_id=s['company_id'],address=fact(s['address'],a,r)))
    b['database_verification']['counts']['phase2_sites']=len(state['sites'])
    for r in sorted(reqs,key=lambda r:(r['priority'],r['asked_at'],r['request_id'])):
        c=bycase[r['case_id']];field=r['field'];spec=rules.FIELDS[field]
        if r['context']!=rules.dependency(c,field,r['route']):
            c['limitations'].append('STALE_PHASE2_CONTEXT:'+r['request_id'])
            continue
        if spec['kind']=='evidence' and guidance_for(guides,c['case_id'],spec['topic'],r['context'],rules.dependency(original[c['case_id']],field,r['route'])):continue
        sources=[eid(r['request_id'],r['revision'])] if r['history'] else []
        scope='route:'+r['route'] if spec['scope']=='route' else spec['scope']
        c['information_requests'].append(dict(request_id=r['request_id'],case_id=c['case_id'],based_on_case_version=0,
            requested_by='part1',fields=[field],trigger_rule_id=rules.VERSION+':'+field,
            reason=r['asked_reason']+'. 사용자 진술과 증빙이 필요한 항목을 구분합니다.',
            question=spec['question'],answer_type='text',acceptable_evidence=['증빙자료'] if spec['kind']=='evidence' else ['사용자 답변'],
            blocking_scope=scope,status=r['status'],answer_evidence_ids=sources))
        if not r['history']:continue
        a=r['history'][-1];value=a['value']
        if field=='site_address':
            c['site_id']=uid(f"{r['request_id']}:site:{a['revision']}") if value is not None else None
            c['site_inherited']=False;c['site_source_evidence_ids']=sources
            if value is None:
                if 'site_id' not in c['missing_fields']:c['missing_fields'].append('site_id')
            elif 'site_id' in c['missing_fields']:c['missing_fields'].remove('site_id')
        elif field=='components':
            previous=c['components']
            c['components']=[] if value is None else [dict(name=fact(value,a,r),proportion_percent={})]
            if previous:c['limitations'].append('USER_UPDATED_COMPONENT_NAME')
        elif field=='quantity':
            q=c['quantity'];old=copy.deepcopy(q)
            normalized=value or dict(value=None,unit=None,dimension=None,conversion_rule=None)
            q.update(fact(normalized['value'],a,r));q.update({k:normalized[k] for k in ('unit','dimension','conversion_rule')})
            if quantity_key(old)!=quantity_key(q) or old['evidence_ids']!=q['evidence_ids']:
                cleared=[]
                for subfield in ('method','measured_at','net_weight'):
                    before=old[subfield]
                    cleared.append(subfield)
                    if before.get('evidence_ids'):
                        c['assertions'].append(dict(assertion_id=uid(r['request_id']+':old:'+subfield),field_path='quantity.'+subfield,
                            raw_value=before.get('raw_value') or '',evidence_ids=before['evidence_ids'],
                            disposition='held',applicability='unknown',reason='처리량 변경으로 측정 속성 재확인 필요'))
                    q[subfield]={}
                c['limitations'].append('MEASUREMENT_DETAILS_STALE_AFTER_QUANTITY_CHANGE')
                for f in ('quantity.'+x for x in cleared if x in ('method','measured_at')):
                    if f not in c['missing_fields']:c['missing_fields'].append(f)
            if 'quantity.value' in c['missing_fields'] and value is not None:c['missing_fields'].remove('quantity.value')
            if value is None and 'quantity.value' not in c['missing_fields']:c['missing_fields'].append('quantity.value')
        else:
            obj=c;parts=field.split('.')
            for key in parts[:-1]:obj=obj[key]
            before=obj[parts[-1]]
            if before.get('evidence_ids'):
                c['assertions'].append(dict(assertion_id=uid(r['request_id']+':original'),field_path=field,
                    raw_value=before.get('raw_value') or '',evidence_ids=before['evidence_ids'],disposition='held',
                    applicability='matched',reason='이전 원문/진술 보존. 현재 답변으로 검토용 후보 갱신'))
            obj[parts[-1]]=fact(value,a,r)
            if field=='mixture_type':
                c['separability']={}
                if before.get('value')!=value:
                    for i,component in enumerate(c['components']):
                        if component['name']['evidence_ids']:
                            c['assertions'].append(dict(assertion_id=uid(r['request_id']+':old-component:'+str(i)),field_path='components',
                                raw_value=component['name'].get('raw_value') or '',evidence_ids=component['name']['evidence_ids'],
                                disposition='held',applicability='unknown',reason='혼합 형태 변경으로 구성 재확인 필요'))
                    c['components']=[]
                c['limitations'].append('COMPOSITION_AND_SEPARATION_REVIEW_AFTER_MIXTURE_UPDATE')
        if field not in ('site_address','quantity'):
            if value is None:
                if field not in c['missing_fields']:c['missing_fields'].append(field)
            elif field in c['missing_fields']:c['missing_fields'].remove(field)
        c['limitations'].append('PHASE2_USER_REPORT_NOT_LEGAL_VERIFICATION:'+field)
    for c in b['cases']:
        for field in ('quantity.method','quantity.measured_at'):
            if rules.dependency(c,field)!=rules.dependency(original[c['case_id']],field):
                sub=field.split('.')[1];current=c['quantity'][sub]
                current_ids=current.get('evidence_ids',[])
                accepted=any(r['case_id']==c['case_id'] and r['field']==field and r['history']
                    and r['context']==rules.dependency(c,field,r['route'])
                    and current_ids==[eid(r['request_id'],r['revision'])] for r in reqs)
                if not accepted:
                    c['quantity'][sub]={}
                    if field not in c['missing_fields']:c['missing_fields'].append(field)
        for g in guides:
            if g['case_id']!=c['case_id'] or g['topic'] not in ('classification','hazard'):continue
            field='classification_evidence' if g['topic']=='classification' else 'hazard_evidence'
            match=guidance_for([g],c['case_id'],g['topic'],rules.dependency(c,field),rules.dependency(original[c['case_id']],field))
            if match is None:
                c['limitations'].append('STALE_GUIDANCE_CONTEXT:'+g['request_id'])
                c['information_requests']=[q for q in c['information_requests'] if q['request_id']!=g['request_id']]
    return DBReviewExport.model_validate(b).model_dump(mode='json')
