"""Run stage-one CLI demo. Uses only included fictional input; never overwrites a DB."""
import argparse
import subprocess
import sys
from pathlib import Path
from contextlib import closing
from db_store import connect,load_rows
from initial_review import preview


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out-dir',default='output/initial_review_demo')
    args=p.parse_args()
    root=Path(__file__).resolve().parent
    out=Path(args.out_dir).resolve()
    if (out/'demo.sqlite3').exists() or (out/'blank.docx').exists():
        raise SystemExit('기존 파일이 있습니다. 새 --out-dir을 지정하세요.')
    out.mkdir(parents=True,exist_ok=True)
    db=out/'demo.sqlite3'
    logs=[]
    def run(*argv):
        cmd=[sys.executable,str(root/'db_intake.py'),*map(str,argv)]
        r=subprocess.run(cmd,capture_output=True,text=True,check=True)
        logs.append('$ '+' '.join(map(str,argv))+'\n'+r.stdout)
        print(r.stdout)
    run('template','--out',out/'blank.docx')
    run('ingest',root/'fixtures/intake_checkable_example.docx','--db',db,'--synthetic')
    run('inspect','--db',db)
    with closing(connect(db)) as c:
        sid=c.execute('SELECT submission_id FROM submissions').fetchone()[0]
        ids=[r['case_id'] for r in load_rows(c,sid)['case_candidates']]
    for case in ids:
        run('initial-preview','--db',db,'--case',case)
        review_hash=preview(db,case)['review_hash']
        # Demo reviewer is synthetic, not an actual user approval.
        run('initial-confirm','--db',db,'--case',case,'--reviewer','가상 시연 검토자',
            '--review-hash',review_hash,'--acknowledge-limitations')
        run('initial-history','--db',db,'--case',case)
    run('verify','--db',db)
    run('export','--db',db,'--out',out/'db_export.json')
    (out/'run-log.txt').write_text('\n'.join(logs),encoding='utf-8')
    print('가상 시연 완료 (실제 사용자 승인 아님):',out)


if __name__=='__main__':main()
