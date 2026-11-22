"""Exercise the document workflow directly against packaged synthetic inputs."""
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document

from db_guidance import requests
from db_store import connect, ingest, load_rows
import external_documents
import external_store
import guided_review
import phase2_store
import stage34


FIXTURES = Path(__file__).parent / 'fixtures'


class DocumentWorkflowTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.open_fixture('intake_checkable_example.docx')

    def open_fixture(self, filename):
        self.db = self.root / (Path(filename).stem + '.sqlite3')
        sid, _ = ingest(FIXTURES / filename, self.db, synthetic=True)
        with closing(connect(self.db)) as conn:
            self.case, self.other_case = [c['case_id'] for c in load_rows(conn, sid)['case_candidates']]

    def record_date(self):
        question = phase2_store.next_questions(self.db, case_id=self.case, limit=1)['questions'][0]
        self.assertEqual(question['field'], 'quantity.measured_at')
        phase2_store.answer(self.db, question['request_id'], self.case, '2026-11-09', question['expected_revision'])

    def group(self, kind):
        return stage34.workspace(self.db, self.case)['groups'][kind]

    def packet(self, kind):
        return self.group(kind)['packet']['packet_id']

    def answer(self, kind, value, path=None):
        return stage34.answer_evidence(self.db, self.case, kind, value, path)

    def draft_text(self, packet):
        sequence = len(list(self.root.glob('draft-*')))
        files = stage34.draft(self.db, self.case, packet, self.root / f'draft-{sequence}')
        return '\n'.join(p.text for p in Document(files['request.docx']).paragraphs)

    def attach(self, packet, filename, sample=None):
        return external_store.attach(self.db, self.case, packet, FIXTURES / filename, '가상 발급기관',
                                     '2026-11-14', sample)

    def review(self, packet, doc, field, value, **details):
        return external_store.record_value(
            self.db, self.case, packet, doc, field, value, '1쪽 결과표', '가상 검토자',
            '가상 현장·공정·시료·날짜 원문 대조', confirm=True, **details,
        )

    def test_only_missing_fields_in_new_drafts(self):
        self.record_date()
        stage34.evidence_question(self.db, self.case, 'authority')
        self.assertNotIn('route', self.group('authority'))
        with self.assertRaises(ValueError):
            stage34.prepare(self.db, self.case, 'authority')
        self.answer('authority', '없어요.')
        stage34.prepare(self.db, self.case, 'authority')
        authority = self.packet('authority')
        with self.assertRaises(ValueError):
            self.draft_text(authority)
        stage34.classification_decision(self.db, self.case, 'unresolved', None)
        initial = self.draft_text(authority)
        for text in ('제품 포장용 PE 필름 자투리', '2026-11-09', '[classification.reported_code]',
                     '[classification.applicability]', '[classification.conditions]'):
            self.assertIn(text, initial)
        external_store.submitted(self.db, self.case, authority, '가상 접수번호', '가상 사용자')
        reply = self.attach(authority, 'authority_partial_response.docx')
        with self.assertRaises(ValueError):
            stage34.draft(self.db, self.other_case, authority, self.root / 'wrong-case')
        self.assertIn('[classification.reported_code]', self.draft_text(authority))
        self.review(authority, reply, 'classification.reported_code', '99-99-99')
        self.review(authority, reply, 'classification.applicability', '가상 현장·품목과 회신 원문 조건 대조')
        remaining = self.draft_text(authority)
        self.assertNotIn('[classification.reported_code]', remaining)
        self.assertNotIn('[classification.applicability]', remaining)
        self.assertIn('[classification.conditions]', remaining)

        stage34.evidence_question(self.db, self.case, 'lab')
        self.answer('lab', '모름')
        sample = 'SAMPLE-A-1'
        stage34.prepare(self.db, self.case, 'lab',
                        ['analysis.heating_value_low', 'analysis.moisture', 'analysis.chlorine'],
                        sample, reason='가상 연료 경로 성적서 요구')
        lab = self.packet('lab')
        self.assertIn('[analysis.heating_value_low]', self.draft_text(lab))
        report = self.attach(lab, 'lab_partial_response.docx', sample)
        self.review(lab, report, 'analysis.heating_value_low', '18.2', unit='MJ/kg', basis='dry', method='합성 시험방법 A')
        self.review(lab, report, 'analysis.moisture', '11.0', unit='%', basis='wet', method='합성 시험방법 B')
        remaining = self.draft_text(lab)
        self.assertNotIn('[analysis.heating_value_low]', remaining)
        self.assertNotIn('[analysis.moisture]', remaining)
        self.assertIn('[analysis.chlorine]', remaining)
        export = external_documents.export_case(self.db, self.case)
        self.assertFalse(export['published'])
        self.assertIsNone(export['case']['classification']['selected_code'])
        self.assertEqual({p['packet_id'] for p in export['document_packets']}, {authority, lab})

    def test_original_is_reused_for_review(self):
        stage34.evidence_question(self.db, self.case, 'authority')
        fixture = FIXTURES / 'authority_partial_response.docx'
        self.answer('authority', '기존 자료가 있습니다.', fixture)
        stage34.prepare(self.db, self.case, 'authority')
        group = self.group('authority')
        self.assertNotIn('route', group)
        self.assertEqual(group['prior_file']['original_name'], fixture.name)
        packet = group['packet']['packet_id']
        doc = stage34.import_prior_file(self.db, self.case, packet, '가상 발급기관', '2026-11-14')
        self.assertNotIn('route', self.group('authority'))
        self.review(packet, doc, 'classification.reported_code', '99-99-99')
        stage34.classification_decision(self.db, self.case, 'unresolved', '회신에 적용 조건 부족')
        self.assertNotIn('[classification.reported_code]', self.draft_text(packet))

    def test_enriched_intake_and_candidate_stays_unpublished(self):
        self.assertEqual(len(Document(FIXTURES / 'wastecase_intake_050.docx').tables), 3)
        self.open_fixture('intake_records_example.docx')
        data = stage34.workspace(self.db, self.case)
        records = data['intake']
        self.assertEqual(records['raw_materials']['value'], '상품 포장용 필름 PF-01')
        self.assertEqual([x['percent'] for x in records['components']], ['98', '2'])
        self.assertEqual([x['ton'] for x in records['monthly_generation']], ['0.18', '0.205', '0.2'])
        self.assertEqual(data['groups']['authority']['status'], 'new_or_no_history')
        self.record_date()
        stage34.classification_decision(self.db, self.case, 'supported', None, '가상 별표 4 항목', '99-99-99')
        self.assertNotIn('route', self.group('authority'))
        export = external_documents.export_case(self.db, self.case)
        self.assertIsNone(export['case']['classification']['selected_code'])
        self.assertEqual(export['guidance_decisions'][-1]['code'], '99-99-99')
        stage34.classification_decision(self.db, self.case, 'unresolved', None)
        stage34.prepare(self.db, self.case, 'authority')
        text = self.draft_text(self.packet('authority'))
        self.assertIn('PF-01', text)
        self.assertIn('폐기물 선별 계량표 A동', text)
        self.assertNotIn('CL-01', text)
        other = stage34.workspace(self.db, self.other_case)['intake']
        self.assertEqual(other['chemicals']['value'], '세척제 CL-01')
        self.assertIsNone(other['monthly_generation'][0]['ton'])

    def test_history_hazard_and_circular_evidence(self):
        previous = guided_review.attach_history(self.db, self.case, 'declaration', '신고증명서.txt',
                                                b'synthetic case file', '가상 발급기관', '2026-11-14')
        guide = stage34.evidence_question(self.db, self.case, 'authority')
        with self.assertRaises(ValueError):
            guided_review.link_declaration(self.db, self.other_case, previous, guide['request_id'])
        guided_review.link_declaration(self.db, self.case, previous, guide['request_id'])
        stage34.prepare(self.db, self.case, 'authority')
        self.assertEqual(self.group('authority')['prior_file']['original_name'], '신고증명서.txt')
        self.assertEqual(self.group('authority')['prior_history']['issuer'], '가상 발급기관')
        stage34.prepare(self.db, self.case, 'hazard', sample='H1', mode='new', reason='가상 용출 시험')
        hazard = self.packet('hazard')
        self.assertIn('폐기물 분석결과서', self.draft_text(hazard))
        document = self.attach(hazard, 'lab_partial_response.docx', 'H1')
        with self.assertRaises(ValueError):
            self.review(hazard, document, 'hazard.analysis_results', '납 | 0.1')
        self.review(hazard, document, 'hazard.analysis_results', '납 | 불검출 | mg/L', method='가상 용출시험')
        stage34.prepare(self.db, self.case, 'circular_registration', mode='new', reason='가상 등록 대상 확인')
        self.assertIn('지정고시 등록확인서', self.draft_text(self.packet('circular_registration')))
        stage34.prepare(self.db, self.case, 'circular_recognition', mode='existing', reason='가상 인정서 적용 검토')
        self.assertNotIn('route', self.group('circular_recognition'))
        self.attach(self.packet('circular_recognition'), 'authority_partial_response.docx')
        export = external_documents.export_case(self.db, self.case)
        result = next(p for p in export['document_packets'] if p['kind'] == 'hazard')
        self.assertEqual(json.loads(result['reviews'][0]['normalized_value']),
                         [{'analyte': '납', 'result': '불검출', 'unit': 'mg/L'}])
        self.assertEqual(len(export['history_documents']), 1)
        self.assertEqual(stage34.workspace(self.db, self.other_case)['history'], [])

    def test_classification_choices_require_reference_for_candidate(self):
        stage34.evidence_question(self.db, self.case, 'authority')
        stage34.classification_decision(self.db, self.case, 'unresolved', None)
        stage34.prepare(self.db, self.case, 'authority')
        self.assertIn('www.epeople.go.kr', self.group('authority')['route']['submission_url'])
        self.assertIn('관할기관의 서면 회신서', self.draft_text(self.packet('authority')))
        with self.assertRaises(ValueError):
            stage34.classification_decision(self.db, self.case, 'supported', None, code='99-99-99')
        stage34.classification_decision(self.db, self.case, 'supported', None, '가상 별표 4 품목', '99-99-99')
        self.assertNotIn('route', self.group('authority'))
        self.assertEqual(self.group('authority')['decision']['code'], '99-99-99')

    def test_topic_independence_and_answer_history(self):
        self.open_fixture('intake_records_example.docx')
        self.record_date()
        with self.assertRaises(ValueError):
            self.answer('authority', '자료 있음')
        guide = stage34.evidence_question(self.db, self.case, 'authority')
        same = stage34.evidence_question(self.db, self.case, 'authority')
        self.assertEqual(same['request_id'], guide['request_id'])
        self.answer('authority', '자료 있음')
        self.assertIsNone(self.group('authority')['prior_file'])
        prior = FIXTURES / 'authority_partial_response.docx'
        self.answer('authority', None, prior)
        self.assertEqual(self.group('authority')['prior_file']['original_name'], prior.name)
        self.answer('authority', '모르겠어요')
        self.assertEqual(self.group('authority')['status'], 'unavailable')
        self.assertNotIn('route', self.group('authority'))
        for kind in ('hazard', 'lab', 'circular_registration', 'circular_recognition'):
            question = stage34.evidence_question(self.db, self.case, kind)
            self.assertEqual(question['request_id'], self.group(kind)['request_id'])
            self.assertEqual(self.group(kind)['status'], 'open')
            self.answer(kind, '없어요')
            self.assertEqual(self.group(kind)['status'], 'unavailable')
        history = next(r['history'] for r in requests(self.db, case_id=self.case)
                       if r['request_id'] == guide['request_id'])
        self.assertEqual([row['status'] for row in history], ['answered', 'answered', 'unavailable'])
        self.assertEqual(history[1]['file']['original_name'], prior.name)
