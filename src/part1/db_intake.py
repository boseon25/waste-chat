"""Start here: DOCX -> SQLite -> inspect/verify -> optional JSON draft."""
import argparse
import json
import sqlite3
from pathlib import Path
from db_store import ingest, inspect, verify, create_measurement_question, record_measurement_answer, measurement_history
from db_followup import status
from db_export import export_json
from review_contract import DBReviewExport
import db_guidance
import initial_review
from guidance_catalog import CATALOG, card


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    p = commands.add_parser('ingest', help='DOCX 원본과 추출 행을 SQLite에 저장; JSON 파일 생성 안 함')
    p.add_argument('docx')
    p.add_argument('--db', required=True)
    p.add_argument('--synthetic', action='store_true', help='가상/시험 문서인 경우에만 지정')
    for name in ('inspect', 'verify', 'export'):
        p = commands.add_parser(name)
        p.add_argument('--db', required=True)
        p.add_argument('--submission')
        if name == 'export':
            p.add_argument('--out', required=True)
    p = commands.add_parser('question', help='폐기물 한 건의 실제 측정일 질문 생성 또는 조회')
    p.add_argument('--db', required=True)
    p.add_argument('--submission')
    p.add_argument('--case', required=True, help='inspect에서 표시되는 폐기물 ID')
    for name, help_text in (
        ('answer', '측정일 질문에 첫 답변 기록'),
        ('correct', '측정일 답변 정정 및 이전 답변 보존'),
        ('history', '측정일 답변 이력 조회'),
    ):
        p = commands.add_parser(name, help=help_text)
        p.add_argument('--db', required=True)
        p.add_argument('--submission')
        p.add_argument('--request', required=True)
        if name != 'history':
            p.add_argument('value', help='YYYY-MM-DD 또는 모름')
    p = commands.add_parser('template', help='기초 입력서 양식을 수정하지 않고 새 파일로 복사')
    p.add_argument('--out', required=True)
    p = commands.add_parser('initial-preview', help='한 폐기물 건의 DB 후보와 원문·미확인 비교')
    p.add_argument('--db', required=True)
    p.add_argument('--case', required=True)
    p.add_argument('--submission')
    p = commands.add_parser('initial-confirm', help='표시된 초기 입력 검토 결과를 명시적으로 기록')
    p.add_argument('--db', required=True)
    p.add_argument('--case', required=True)
    p.add_argument('--submission')
    p.add_argument('--reviewer', required=True, help='스스로 기재한 검토자 식별자; 인증 아님')
    p.add_argument('--review-hash', required=True, help='initial-preview에서 확인한 검토 해시')
    p.add_argument('--acknowledge-limitations', action='store_true')
    p = commands.add_parser('initial-history', help='입력 검토 확인 이력 조회')
    p.add_argument('--db', required=True)
    p.add_argument('--case', required=True)
    p.add_argument('--submission')
    p = commands.add_parser('guide', help='항목별 자료 확보 경로 조회 (DB 수정 없음)')
    p.add_argument('topic', nargs='?', choices=list(CATALOG))
    p = commands.add_parser('supply-question', help='선택한 폐기물·목적에 필요한 자료 보완 요청 생성')
    p.add_argument('--db', required=True)
    p.add_argument('--submission')
    p.add_argument('--case', required=True)
    p.add_argument('--topic', required=True, choices=list(CATALOG))
    p.add_argument('--purpose', required=True)
    p.add_argument('--scope', required=True)
    p.add_argument('--as-of')
    for name in ('supply-answer','supply-correct','supply-history','supply-list'):
        p = commands.add_parser(name)
        p.add_argument('--db', required=True)
        p.add_argument('--submission')
        if name == 'supply-list':
            p.add_argument('--case')
            p.add_argument('--all', action='store_true', help='응답/자료 없음 요청까지 포함')
        else:
            p.add_argument('--request', required=True)
        if name in ('supply-answer','supply-correct'):
            p.add_argument('value', help='답변 원문, 자료 없음 또는 모름')
            p.add_argument('--file', help='첨부 원본 저장. 자동 분석/확정은 하지 않음')
    p = commands.add_parser('validate-json', help='내보낸 JSON의 구조·내부 참조 검사. DB 대조를 대신하지 않음')
    p.add_argument('file')
    p = commands.add_parser('schema', help='DBReviewExport JSON Schema 출력')
    p.add_argument('--out', required=True)
    p = commands.add_parser('demo', help='포함된 가상 DOCX로 저장·재조회·검증 후 JSON 출력까지 실행')
    p.add_argument('--out-dir', default='output/demo')
    args = parser.parse_args(argv)
    try:
        if args.action == 'ingest':
            sid, created = ingest(args.docx, args.db, args.synthetic)
            print(f"DB COMMIT + REOPEN VERIFY PASS / {'created' if created else 'reused'} / {sid}")
            print('JSON 파일은 아직 만들지 않았습니다. inspect로 DB를 확인한 뒤 export를 실행하세요.')
        elif args.action == 'inspect':
            print(inspect(args.db, args.submission))
        elif args.action == 'verify':
            r = verify(args.db, args.submission)
            print(f"VERIFY PASS: {r['submission_id']} / {r['counts']}")
        elif args.action == 'question':
            request, question = create_measurement_question(args.db, args.case, args.submission)
            print(f"질문 ID: {request['request_id']} / 상태: {status(request)}")
            if status(request) == 'open':
                print(question)
            else:
                print(f"저장된 답변: {request['answer_text']}")
        elif args.action in ('answer', 'correct'):
            is_correction = args.action == 'correct'
            saved = record_measurement_answer(args.db, args.request, args.value, args.submission,
                                              correct=is_correction)
            if saved:
                print('정정 저장 완료' if is_correction else '답변 저장 완료')
            else:
                print('동일한 답변이 이미 저장되어 있습니다.')
        elif args.action == 'history':
            answers = measurement_history(args.db, args.request, args.submission)
            if not answers:
                print('아직 저장된 답변이 없습니다.')
            for answer in answers:
                label = '현재' if answer is answers[-1] else '이전'
                print(f"{answer['revision']}차 / {label} / {answer['answer_text']} / {answer['answered_at']}")
        elif args.action == 'template':
            target = initial_review.copy_template(args.out)
            print(f'양식 복사 완료: {target}. 회색 예시는 실제 값이 아닙니다. Word에서 작성 후 저장하세요.')
        elif args.action == 'initial-preview':
            print(initial_review.format_preview(initial_review.preview(args.db,args.case,args.submission)))
        elif args.action == 'initial-confirm':
            saved = initial_review.confirm(args.db,args.case,args.reviewer,args.review_hash,
                                           args.acknowledge_limitations,args.submission)
            print('초기 입력 검토 기록 저장' if saved else '동일한 검토 기록이 이미 있습니다.')
            print('법적 분류·자료 진위·발행 승인 아님. 미확인 사항은 그대로 유지됩니다.')
        elif args.action == 'initial-history':
            p, records = initial_review.history(args.db,args.case,args.submission)
            print(f"현재 검토 해시: {p['review_hash']} / 이력 {len(records)}건")
            for r in records:
                print(f"{r['review_id']} / {r['reviewer']} / {r['reviewed_at']} / 현재 초기 입력과 일치={r['matches_current']}")
        elif args.action == 'guide':
            value = card(args.topic) if args.topic else {k:v['title'] for k,v in CATALOG.items()}
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.action == 'supply-question':
            r = db_guidance.create_request(args.db,args.case,args.topic,args.purpose,args.scope,args.as_of,args.submission)
            print(f"질문 ID: {r['request_id']} / 상태: {r['status']}")
            if r['status'] == 'open':
                print(r['guidance']['question'])
                if r['periods']:
                    print('대상 연월: ' + ', '.join(r['periods']))
            else:
                print('기존 답변을 유지합니다. 같은 질문을 다시 요구하지 않습니다.')
            print(json.dumps(r['guidance'], ensure_ascii=False, indent=2))
        elif args.action in ('supply-answer','supply-correct'):
            saved = db_guidance.record_answer(args.db,args.request,args.value,args.submission,
                attachment=args.file,correct=args.action == 'supply-correct')
            print('저장 완료. 현재 건 적용성·내용 검토 전이며 사실값은 자동 확정되지 않습니다.' if saved else '동일 답변이 이미 저장되어 있습니다.')
            if args.value.strip() in ('모름','자료 없음'):
                r = next(r for r in db_guidance.requests(args.db,args.submission) if r['request_id']==args.request)
                print('대안: ' + r['guidance']['alternative'])
                print('보류 범위: ' + r['blocking_scope'])
                print('다음 단계: phase3.py plan --db <현재 DB> --case '+r['case_id']+' --purpose "필요한 판단 목적"')
                print('기본 묶음은 분류 근거 회신입니다. 시험값은 실제 필요한 --need 항목만 선택하세요. 모든 사용자에게 일괄 시험을 요구하지 않습니다.')
        elif args.action in ('supply-list','supply-history'):
            if args.action == 'supply-list':
                records = db_guidance.requests(args.db,args.submission,args.case,open_only=not args.all)
                for r in records[:3]:
                    print(f"{r['request_id']} / {r['case_id']} / {r['topic']} / {r['status']}")
                print(f'해당 요청 {len(records)}개 중 최대 3개 표시 (전체 이력은 supply-history).')
            else:
                r = next((r for r in db_guidance.requests(args.db,args.submission) if r['request_id']==args.request),None)
                if r is None:
                    raise ValueError('현재 제출에서 해당 질문을 찾을 수 없습니다.')
                print(json.dumps(r,ensure_ascii=False,indent=2))
        elif args.action == 'validate-json':
            DBReviewExport.model_validate_json(Path(args.file).read_text(encoding='utf-8'))
            print('JSON SHAPE + REFERENCES: PASS (실제 DB 대조/법적 검증 아님)')
        elif args.action == 'schema':
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            schema = DBReviewExport.model_json_schema()
            schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
            target.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'Schema exported: {target}')
        elif args.action == 'export':
            result = export_json(args.db, args.out, args.submission)
            print(f"DB verified -> {len(result['cases'])} draft cases -> {args.out}; published=false")
        else:
            out = Path(args.out_dir)
            sample = Path(__file__).parent / 'fixtures/intake_checkable_example.docx'
            db = out / 'intake.sqlite3'
            sid, _ = ingest(sample, db, synthetic=True)
            summary = inspect(db, sid)
            print(summary)
            out.mkdir(parents=True, exist_ok=True)
            (out / 'db_check.txt').write_text(summary + '\n', encoding='utf-8')
            result = export_json(db, out / 'db_export.json', sid)
            print(f"DB 확인 후 JSON 초안 {len(result['cases'])}건 출력: {out / 'db_export.json'}")
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'FAILED: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
