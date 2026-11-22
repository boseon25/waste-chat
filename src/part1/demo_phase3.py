"""Fictional end-to-end CLI run. Never sends an application or test sample."""
import argparse,json,subprocess,sys
from pathlib import Path
from contextlib import closing
from docx import Document
from db_store import connect,load_rows,verify
from external_fields import DEFAULT_NEEDS


def main():
    p=argparse.ArgumentParser();p.add_argument('--out-dir',default='output/phase3_demo');a=p.parse_args()
    out=Path(a.out_dir)
    if out.exists():raise SystemExit('새 --out-dir을 지정하세요. 기존 시연은 보존합니다.')
    out.mkdir(parents=True);logs=[]
    def run(script,*args,expect=0,parse=True):
        result=subprocess.run([sys.executable,script,*map(str,args)],capture_output=True,text=True)
        logs.append('$ '+script+' '+' '.join(map(str,args))+'\n'+result.stdout+result.stderr)
        if result.returncode!=expect:raise RuntimeError(logs[-1])
        return json.loads(result.stdout) if parse and expect==0 else result
    run('demo_phase2.py','--out-dir',out/'core',parse=False)
    db=out/'core/intake.sqlite3'
    with closing(connect(db)) as c:
        sid=c.execute('SELECT submission_id FROM submissions').fetchone()[0]
        first,second=[x['case_id'] for x in load_rows(c,sid)['case_candidates']]
    needs=DEFAULT_NEEDS+['analysis.heating_value','analysis.moisture','analysis.chlorine']
    purpose='가상 시연: 분류 근거와 선택 경로의 필요 시험항목 확인'
    planargs=['--db',db,'--case',first,'--purpose',purpose,'--sample','SAMPLE-A-1']
    for field in needs:planargs+=['--need',field]
    plan=run('phase3.py','plan',*planargs)
    assert len(plan['packets'])==2 and not plan['chat_fields']
    packets={p['kind']:p['packet_id'] for p in plan['packets']}
    for kind,pid in packets.items():run('phase3.py','draft','--db',db,'--case',first,'--packet',pid,'--out-dir',out/(kind+'_initial'))
    run('phase3.py','submitted','--db',db,'--case',first,'--packet',packets['authority'],'--reference','SYNTHETIC-RECEIPT-001 (실제 접수 아님)','--actor','가상 사용자')
    data={
        'authority':[('classification.reported_code','99-99-99',None,None,None),('classification.applicability','가상 A동의 PE 필름 자투리에 대한 조건부 검토 예시',None,None,None)],
        'lab':[('analysis.heating_value','18.2','MJ/kg','dry','합성 시험방법 A'),('analysis.moisture','11.0','%','wet','합성 시험방법 B')],
    }
    for kind,values in data.items():
        document=Document();document.add_heading('합성 테스트 자료 — 실제 회신/성적서 아님',0)
        document.add_paragraph('폐기물 건: '+first+' / 가상 A동 / PE 필름 자투리 / 시료 SAMPLE-A-1 / 2026-11-14')
        table=document.add_table(rows=1,cols=4);table.rows[0].cells[0].text='항목';table.rows[0].cells[1].text='값';table.rows[0].cells[2].text='단위';table.rows[0].cells[3].text='기준/방법'
        for field,value,unit,basis,method in values:
            cells=table.add_row().cells
            for cell,text in zip(cells,[field,value,unit or '-',(basis or '-')+' / '+(method or '-')]):cell.text=text
        source=out/(kind+'_partial_response.docx');document.save(source)
        result=run('phase3.py','attach','--db',db,'--case',first,'--packet',packets[kind],source,'--issuer','가상 기관 (테스트)','--date','2026-11-14','--sample','SAMPLE-A-1')
        did=result['document_id']
        before=run('phase3.py','show','--db',db,'--case',first,'--packet',packets[kind])
        assert len(before['still_missing'])==3 # Attachment alone never supplies values.
        run('phase3.py','extract','--db',db,'--case',second,'--packet',packets[kind],'--document',did,'--out',out/'must_not_exist.docx',expect=1,parse=False)
        for field,value,unit,basis,method in values:
            args=['--db',db,'--case',first,'--packet',packets[kind],'--document',did,'--field',field,'--value',value,
                  '--locator','결과표 '+field+' 행','--reviewer','합성 시연 검토자','--reason','가상 원문 및 현장·품목·시료·기간 대조 시연','--confirm-applicability']
            if unit:args+=['--unit',unit,'--basis',basis,'--method',method]
            run('phase3.py','review',*args)
    remaining=run('phase3.py','plan',*planargs)
    assert set(remaining['missing_fields'])=={'classification.conditions','analysis.chlorine'}
    for p in remaining['packets']:run('phase3.py','draft','--db',db,'--case',first,'--packet',p['packet_id'],'--out-dir',out/(p['kind']+'_remaining'))
    run('phase3.py','export','--db',db,'--case',first,'--out',out/'case_document_review.json')
    bundle=json.loads((out/'case_document_review.json').read_text())
    assert not bundle['published'] and bundle['case_version']==0 and bundle['case']['classification']['selected_code'] is None
    assert all(p['case_id']==first for p in bundle['document_packets'])
    assert not (out/'must_not_exist.docx').exists()
    verification=verify(db)
    (out/'cli-check.txt').write_text('\n\n'.join(logs),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(dict(status='PASS',case_id=first,other_case_id=second,missing_fields=remaining['missing_fields'],database_verification=verification,external_sent=False),ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: 부족값 6개 → 문서 2묶음 → 일부 값 4개 대조 → 남은 값 2개만 보완. 타 건 접근 거절/원문 검증/미발행 유지.')

if __name__=='__main__':main()
