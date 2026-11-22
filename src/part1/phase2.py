"""Stage-two CLI: DB in, case-scoped questions/answers out. No JSON input required."""
import argparse
import json
import sqlite3
from contextlib import closing
import phase2_store as store
import phase2_rules as rules
from db_store import connect,verify_connection


def main():
    parser=argparse.ArgumentParser(description='2단계: 우선순위 질문 → 형식/대상 검증 → 답변·정정 저장')
    commands=parser.add_subparsers(dest='action',required=True)
    for name in ('next','ask','answer','correct','history','documents','status'):
        p=commands.add_parser(name)
        p.add_argument('--db',required=True)
        p.add_argument('--submission')
        p.add_argument('--case',required=name!='next')
        if name in ('next','ask'):p.add_argument('--route')
        if name=='next':p.add_argument('--limit',type=int,default=3)
        if name=='ask':
            p.add_argument('--field',required=True,choices=list(rules.FIELDS))
            p.add_argument('--reason',required=True,help='사용자가 요청한 명시적 재확인 사유')
        if name in ('answer','correct','history','documents'):p.add_argument('--request',required=True)
        if name in ('answer','correct'):
            p.add_argument('--revision',required=True,type=int,help='현재 질문 차수. 첫 답변은 0')
            p.add_argument('value')
        if name=='correct':p.add_argument('--reason',required=True)
    a=parser.parse_args()
    try:
        if a.action=='next':
            result=store.next_questions(a.db,a.submission,a.case,a.route,a.limit)
            for q in result['questions']:
                print(f"[{q['priority']}] 건 {q['case_id']} / {q['field']} / {q['source']} / 질문 {q['request_id']}")
                print(q['question'])
                print('현재 후보값:',q['current_value'])
                if q['source']=='measurement':print('기존 측정일 질문 재사용: db_intake.py answer --db DB --request QUESTION_ID "YYYY-MM-DD"')
                elif q['source']=='guidance':print('기존 자료 요청 재사용: db_intake.py supply-answer --db DB --request QUESTION_ID "자료 없음 또는 답변"')
                elif q['mode']=='evidence':print('값 직접 입력 대신 phase2.py documents --db DB --case CASE_ID --request QUESTION_ID')
                else:print('phase2.py answer --db DB --case CASE_ID --request QUESTION_ID --revision 0 "답변"')
            for b in result['blocked_cases']:print('보류:',b['case_id'],b['reason'])
            print(f"현재 질문 {len(result['questions'])}개 / 대기 {result['pending_count']}개")
            print(result['note'])
        elif a.action=='ask':
            r=store.manual_question(a.db,a.case,a.field,a.reason,a.submission,a.route)
            print(f"질문: {r['request_id']} / 상태: {r['status']} / 현재 차수: {r['revision']}")
            print(rules.FIELDS[r['field']]['question'])
            print('현재 후보값:',r['current_value'])
            if r['revision']:print('기존 기록이 있으므로 새 최초 답변이 아니라 correct를 사용하세요.')
        elif a.action in ('answer','correct'):
            changed=store.answer(a.db,a.request,a.case,a.value,a.revision,a.submission,
                                 correct=a.action=='correct',reason=getattr(a,'reason','사용자 최초 답변'))
            print('답변 저장·재검증 완료: 해당 건의 사용자 진술 초안만 갱신했습니다.' if changed else '동일 답변이 이미 저장되어 있습니다.')
            print('미확인은 null, 근거 없는 분류번호·분석값·법적 판단은 확정하지 않습니다.')
        elif a.action=='history':print(json.dumps(store.history(a.db,a.request,a.case,a.submission),ensure_ascii=False,indent=2))
        elif a.action=='documents':
            r=store.documents(a.db,a.request,a.case,a.submission)
            print(f"자료 요청 ID: {r['request_id']} / 건: {r['case_id']} / 상태: {r['status']}")
            print(json.dumps(r['guidance'],ensure_ascii=False,indent=2))
            print('자료 응답: db_intake.py supply-answer --db DB --request GUIDANCE_ID "자료 없음 또는 설명" [--file FILE]')
            print('실제 신청·자동 발급·자료 적용성 확정은 하지 않습니다.')
        else:
            with closing(connect(a.db)) as conn:
                conn.execute('BEGIN');report=verify_connection(conn,a.submission)
                b=store.snapshot(conn,report['submission_id'],report)
                c=store.target_case(b,a.case)
                print(json.dumps(dict(case_id=c['case_id'],draft_revision=c['draft_revision'],site_id=c['site_id'],
                    name=c['name'],process_description=c['process_description'],quantity=c['quantity'],
                    hazard_declaration=c['hazard_declaration'],classification=c['classification'],
                    missing_fields=c['missing_fields'],limitations=c['limitations']),ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,sqlite3.Error) as e:
        print('ERROR:',e);return 1


if __name__=='__main__':raise SystemExit(main())
