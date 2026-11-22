"""CLI end-to-end demo with fictional input. Includes intentional rejected answers."""
import argparse
import subprocess
import sys
import json
from pathlib import Path
from contextlib import closing
from db_store import connect,load_rows
from phase2_store import load_state,next_questions
import db_guidance


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out-dir',default='output/phase2_demo')
    a=p.parse_args();root=Path(__file__).resolve().parent;out=Path(a.out_dir).resolve()
    db=out/'intake.sqlite3'
    if db.exists():raise SystemExit('기존 DB를 덮어쓰지 않습니다. 새로운 --out-dir을 지정하세요.')
    out.mkdir(parents=True,exist_ok=True);logs=[]
    def run(script,*args,expect=0):
        cmd=[sys.executable,str(root/script),*map(str,args)]
        r=subprocess.run(cmd,capture_output=True,text=True)
        text='$ '+' '.join(map(str,[script,*args]))+'\n'+r.stdout+r.stderr
        logs.append(text);print(text)
        if r.returncode!=expect:raise RuntimeError('Unexpected CLI result: '+text)
    run('db_intake.py','ingest',root/'fixtures/intake_checkable_example.docx','--db',db,'--synthetic')
    with closing(connect(db)) as conn:
        sid=conn.execute('SELECT submission_id FROM submissions').fetchone()[0]
        cases=load_rows(conn,sid)['case_candidates']
    first,second=[c['case_id'] for c in cases]
    run('db_intake.py','inspect','--db',db)
    run('phase2.py','next','--db',db,'--case',first)
    with closing(connect(db)) as conn:requests=load_state(conn,sid)['requests']
    date_request=next(r for r in requests if r['case_id']==first and r['field']=='quantity.measured_at')['request_id']
    documents_request=next(r for r in requests if r['case_id']==first and r['field']=='classification_evidence')['request_id']
    run('phase2.py','answer','--db',db,'--case',first,'--request',date_request,'--revision',0,'2999-01-01',expect=1)
    run('phase2.py','answer','--db',db,'--case',second,'--request',date_request,'--revision',0,'2026-11-10',expect=1)
    run('phase2.py','answer','--db',db,'--case',first,'--request',date_request,'--revision',0,'2026-11-10')
    run('phase2.py','correct','--db',db,'--case',first,'--request',date_request,'--revision',0,'2026-11-09','--reason','가상 기록 재확인',expect=1)
    run('phase2.py','correct','--db',db,'--case',first,'--request',date_request,'--revision',1,'2026-11-09','--reason','가상 기록 재확인')
    run('phase2.py','history','--db',db,'--case',first,'--request',date_request)
    run('phase2.py','documents','--db',db,'--case',first,'--request',documents_request)
    g=next(r for r in db_guidance.requests(db) if r['case_id']==first and r['topic']=='classification')
    run('db_intake.py','supply-answer','--db',db,'--request',g['request_id'],'자료 없음')
    run('phase2.py','next','--db',db,'--case',first)
    assert next_questions(db,case_id=first)['questions']==[]
    run('phase2.py','next','--db',db,'--case',second)
    with closing(connect(db)) as conn:requests=load_state(conn,sid)['requests']
    second_date=next(r for r in requests if r['case_id']==second and r['field']=='quantity.measured_at')['request_id']
    run('phase2.py','answer','--db',db,'--case',second,'--request',second_date,'--revision',0,'잘 모르겠어요')
    run('phase2.py','next','--db',db,'--case',second)
    assert 'quantity.measured_at' not in [q['field'] for q in next_questions(db,case_id=second)['questions']]
    run('db_intake.py','verify','--db',db)
    run('db_intake.py','export','--db',db,'--out',out/'db_export.json')
    run('db_intake.py','validate-json',out/'db_export.json')
    bundle=json.loads((out/'db_export.json').read_text())
    ca,cb=bundle['cases']
    assert ca['quantity']['measured_at']['value']=='2026-11-09'
    assert cb['quantity']['measured_at']['value'] is None
    assert (ca['draft_revision'],cb['draft_revision'])==(3,1)
    assert all(c['classification']['selected_code'] is None for c in bundle['cases'])
    (out/'cli-check.txt').write_text('\n'.join(logs),encoding='utf-8')
    print('PASS: 가상 2건의 질문·입력오류 거절·정정·자료 없음·DB 재조회·JSON 검증 완료.',out)


if __name__=='__main__':main()
