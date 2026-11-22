"""Case-scoped CLI for evidence questions, historical records, and inquiry drafts."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

import guided_review
import official_forms
import stage34
from external_fields import FIELDS


def parser():
    cli = argparse.ArgumentParser(description='폐기물 건별 자료 확인·외부 문의 준비')
    commands = cli.add_subparsers(dest='command', required=True)
    forms = commands.add_parser('forms', help='기관 배포 서식의 로컬 파일, 용도, 배포처 조회')
    forms.add_argument('--topic', choices=stage34.TOPICS)

    def command(name, help_text):
        entry = commands.add_parser(name, help=help_text)
        entry.add_argument('--db', required=True, type=Path)
        entry.add_argument('--case', required=True, help='폐기물 건 ID')
        return entry

    status = command('status', '입력서 기록, 문서 상태, 적용 가능한 문의 경로 보기')
    status.add_argument('--registry', type=Path)
    status.add_argument('--packet', help='같은 주제의 여러 묶음 가운데 조회할 묶음 ID')
    ask = command('ask-evidence', '같은 폐기물의 기존 자료 보유 여부 질문')
    answer = command('answer-evidence', '보유 여부 답변·정정 또는 자료 첨부')
    prepare = command('prepare', '현재 폐기물 건의 문의·의뢰 또는 문서 검토 묶음 준비')
    for entry in (ask, answer, prepare):
        entry.add_argument('--topic', required=True, choices=stage34.TOPICS)
    answer.add_argument('value', nargs='?', help='보유 여부 또는 첨부 설명')
    answer.add_argument('--file', type=Path)

    classification = command('classification', '별표 4 및 현재 자료의 대조 결과 기록')
    classification.add_argument('choice', choices=('supported', 'unresolved', 'later'))
    classification.add_argument('--code')
    classification.add_argument('--reference')
    classification.add_argument('--notes')
    prepare.add_argument('--need', action='append', choices=FIELDS)
    prepare.add_argument('--sample')
    prepare.add_argument('--reason')
    prepare.add_argument('--mode', choices=('existing', 'new'))

    history = command('attach-history', '기존 신고·인계·처리 기록 보관')
    history.add_argument('file', type=Path)
    history.add_argument('--kind', required=True, choices=guided_review.HISTORY_KINDS)
    link = command('link-history', '보관한 신고자료를 이 건의 분류 근거 질문에 연결')
    link.add_argument('--request', required=True)
    link.add_argument('--document', required=True)
    reuse = command('import-evidence', '질문에 첨부된 파일을 문서 검토 묶음에 등록')
    reuse.add_argument('--packet', required=True)
    reuse.add_argument('--sample')
    for entry in (history, reuse):
        entry.add_argument('--issuer', required=True)
        entry.add_argument('--date')

    no_values = command('no-values', '받은 문서에서 요청한 값을 찾을 수 없었던 근거 기록')
    no_values.add_argument('--packet', required=True)
    no_values.add_argument('--document', required=True)
    no_values.add_argument('--reason', required=True)
    draft = command('draft', '현재 필요한 값의 문의·시험 의뢰 준비 초안 작성')
    draft.add_argument('--packet', required=True)
    draft.add_argument('--out-dir', required=True, type=Path)
    draft.add_argument('--registry', type=Path)
    return cli


def run(args):
    if args.command == 'forms':
        return [
            {**entry, 'path': str(official_forms.DIRECTORY / entry['filename'])}
            for entry in official_forms.catalog(args.topic)
        ]
    db, cid = args.db, args.case
    if args.command == 'status':
        return stage34.workspace(db, cid, args.registry, args.packet)
    if args.command == 'ask-evidence':
        question = stage34.evidence_question(db, cid, args.topic)
        return {'question': stage34.QUESTIONS[args.topic], **question}
    if args.command == 'answer-evidence':
        return stage34.answer_evidence(db, cid, args.topic, args.value, args.file)
    if args.command == 'classification':
        stage34.classification_decision(db, cid, args.choice, args.notes, args.reference, args.code)
        return {'saved': True}
    if args.command == 'prepare':
        return stage34.prepare(db, cid, args.topic, args.need, args.sample, mode=args.mode, reason=args.reason)
    if args.command == 'attach-history':
        with args.file.open('rb') as source:
            content = source.read(20 * 1024 * 1024 + 1)
        document = guided_review.attach_history(
            db, cid, args.kind, args.file.name, content, args.issuer, args.date,
        )
        return {'document_id': document}
    if args.command == 'link-history':
        guided_review.link_declaration(db, cid, args.document, args.request)
        return {'linked': True}
    if args.command == 'import-evidence':
        document = stage34.import_prior_file(db, cid, args.packet, args.issuer, args.date or '', args.sample)
        return {'document_id': document}
    if args.command == 'no-values':
        stage34.mark_no_values(db, cid, args.packet, args.document, args.reason)
        return {'status': 'document_checked_no_values'}
    return stage34.draft(db, cid, args.packet, args.out_dir, args.registry)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
