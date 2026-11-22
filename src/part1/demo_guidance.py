"""Offline CLI smoke demo with fictional data. Never overwrites an existing demo DB."""
import argparse
import subprocess
import sys
from pathlib import Path
from contextlib import closing
from db_store import connect,load_rows
from db_guidance import request_key


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out-dir',default='output/guidance_demo')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    out=Path(args.out_dir).resolve()
    db=out/'intake.sqlite3'
    if db.exists():
        raise SystemExit('기존 DB는 덮어쓰지 않습니다. --out-dir로 새로운 폴더를 지정하세요.')
    out.mkdir(parents=True,exist_ok=True)
    logs=[]
    def run(*argv):
        cmd=[sys.executable,str(root/'db_intake.py'),*map(str,argv)]
        result=subprocess.run(cmd,capture_output=True,text=True,check=True)
        logs.append('$ '+' '.join(map(str,argv))+'\n'+result.stdout)
        print(result.stdout)
    run('ingest',root/'fixtures/intake_checkable_example.docx','--db',db,'--synthetic')
    with closing(connect(db)) as conn:
        sid=conn.execute('SELECT submission_id FROM submissions').fetchone()[0]
        cid=load_rows(conn,sid)['case_candidates'][0]['case_id']
    purpose='가상 시연: 기존 신고자료와 현재 건 적용 조건 확인'
    run('guide','classification')
    run('supply-question','--db',db,'--case',cid,'--topic','classification','--purpose',purpose,'--scope','classification')
    rid=request_key(cid,'classification',purpose,'classification',None)
    run('supply-answer','--db',db,'--request',rid,'자료 없음')
    run('supply-list','--db',db)
    f=out/'synthetic-note.txt'
    f.write_text('가상 테스트 첨부입니다. 공식 회신/시험성적서가 아닙니다. 법적 분류와 적용성은 확인되지 않았습니다.',encoding='utf-8')
    run('supply-correct','--db',db,'--request',rid,'가상 참고자료를 찾았습니다. 내용과 적용성 검토가 필요합니다.','--file',f)
    run('supply-history','--db',db,'--request',rid)
    run('supply-question','--db',db,'--case',cid,'--topic','monthly_generation','--purpose','가상 매칭의 월별 발생량 확인',
        '--scope','matching:DEMO','--as-of','2026-11-11')
    run('verify','--db',db)
    run('export','--db',db,'--out',out/'db_export.json')
    run('validate-json',out/'db_export.json')
    (out/'cli-check.txt').write_text('\n'.join(logs),encoding='utf-8')
    print('가상 시연 완료:',out)


if __name__=='__main__':main()
