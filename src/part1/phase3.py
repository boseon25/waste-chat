"""Stage 3/4 CLI; accepts flags and files, never requires user-authored JSON."""
import argparse,json,sys
from pathlib import Path
from contextlib import closing
from db_store import connect,verify_connection,load_rows
import external_store as store
import external_documents as documents
from external_fields import FIELDS
from external_routes import status
from phase2_rules import FIELDS as CHAT_FIELDS


def main(argv=None):
    p=argparse.ArgumentParser(description='부족값 묶음 → 문의/의뢰 초안 → 같은 건 첨부 → 값 대조')
    sub=p.add_subparsers(dest='command',required=True)
    commands={}
    for name in ('cases','plan','show','draft','attach','review','submitted','export','extract'):
        a=sub.add_parser(name);commands[name]=a;a.add_argument('--db',required=True);a.add_argument('--submission')
        if name!='cases':a.add_argument('--case',required=True)
        if name not in ('cases','plan','export'):a.add_argument('--packet',required=True)
    a=commands['plan'];a.add_argument('--purpose',required=True);a.add_argument('--need',action='append',choices=list(FIELDS));a.add_argument('--sample')
    for name in ('show','draft','plan'):commands[name].add_argument('--registry')
    commands['draft'].add_argument('--out-dir',required=True)
    a=commands['attach'];a.add_argument('file');a.add_argument('--issuer',default='미확인');a.add_argument('--date',default='');a.add_argument('--sample')
    a=commands['review'];a.add_argument('--document',required=True);a.add_argument('--field',choices=list(FIELDS),required=True);a.add_argument('--value',required=True)
    a.add_argument('--unit');a.add_argument('--basis',choices=['dry','wet','as_received']);a.add_argument('--method')
    a.add_argument('--locator',required=True);a.add_argument('--reviewer',required=True);a.add_argument('--reason',required=True);a.add_argument('--expected-review',type=int,default=0)
    a.add_argument('--confirm-applicability',action='store_true',help='원문 값과 현장·품목·시료·기간의 적용성을 사람이 대조했다는 기록. 법적 확정 아님.')
    a=commands['submitted'];a.add_argument('--reference',required=True);a.add_argument('--actor',required=True)
    commands['export'].add_argument('--out',required=True)
    a=commands['extract'];a.add_argument('--document',required=True);a.add_argument('--out',required=True)
    a=p.parse_args(argv)
    try:
        if a.command=='cases':
            with closing(connect(a.db)) as c:
                report=verify_connection(c,a.submission);out=load_rows(c,report['submission_id'])['case_candidates']
        elif a.command=='plan':
            out=store.plan(a.db,a.case,a.purpose,a.need,a.sample,a.submission)
            for packet in out['packets']:packet['route']=status(packet['kind'],a.registry)
            out['chat_questions']=[CHAT_FIELDS[f]['question'] for f in out['chat_fields']]
            out['chat_action']='현장값 질문·답변은 기존 phase2.py next/answer로 처리합니다. 이미 모름으로 답한 항목은 반복하지 않습니다.'
        elif a.command=='show':
            out=store.get(a.db,a.case,a.packet,a.submission);out['route']=status(out['kind'],a.registry)
        elif a.command=='draft':out=documents.draft(a.db,a.case,a.packet,a.out_dir,a.registry,a.submission)
        elif a.command=='attach':out={'document_id':store.attach(a.db,a.case,a.packet,a.file,a.issuer,a.date,a.sample,a.submission),'status':'received_unreviewed'}
        elif a.command=='review':
            out={'review_id':store.record_value(a.db,a.case,a.packet,a.document,a.field,a.value,a.locator,a.reviewer,a.reason,a.expected_review,a.unit,a.basis,a.method,a.confirm_applicability,a.submission),'note':'문서값 대조 목록만 갱신. 분류번호/법적 판단 자동 확정 없음.'}
        elif a.command=='submitted':
            store.submitted(a.db,a.case,a.packet,a.reference,a.actor,a.submission);out={'status':'user_reported_submission','sent_by_system':False}
        elif a.command=='export':
            out=documents.export_case(a.db,a.case,a.submission)
            path=Path(a.out);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x',encoding='utf-8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
            out={'output':str(path),'published':False}
        else:
            # Case/request matching is checked before returning any attachment bytes.
            with closing(connect(a.db)) as c:
                c.execute('BEGIN');_,packet,_=store.current_packet(c,a.case,a.packet,a.submission,True)
                if a.document not in {d['document_id'] for d in packet['documents']}:raise ValueError('다른 건/묶음의 첨부입니다.')
                blob=c.execute('SELECT content FROM external_documents WHERE document_id=?',(a.document,)).fetchone()[0]
                path=Path(a.out);path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as f:f.write(blob)
                out={'output':str(path),'sha256':store.sha(blob)}
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError) as e:print('ERROR: '+str(e),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
