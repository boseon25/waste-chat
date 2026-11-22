"""DB-first integration tests with the provided explicitly fictional DOCX."""
import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from db_store import ingest, verify, connect, load_rows, inspect, insert_rows
from db_store import create_measurement_question, record_measurement_answer, measurement_history
from db_export import export_json
from intake_parser import parse_document, NS, W, W14
from models import WasteCase, Company, Site
from review_contract import DBReviewExport

SAMPLE = Path(__file__).parent / 'fixtures/intake_checkable_example.docx'


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base = Path(self.tmp.name)
        self.db = self.base / 'test.sqlite3'
        self.sid, _ = ingest(SAMPLE, self.db, synthetic=True)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with closing(connect(self.db)) as c:
            return load_rows(c, self.sid)

    def mutate(self, sql, params=()):
        with closing(connect(self.db, True)) as c:
            c.execute(sql, params)
            c.commit()

    def export(self):
        return export_json(self.db, self.base / 'result.json', self.sid)

    def changed_doc(self, tag=None, text=None, checks=None, duplicate_table=False, placeholders=False):
        out = self.base / 'modified.docx'
        with ZipFile(SAMPLE) as z:
            xml = ET.fromstring(z.read('word/document.xml'))
            for s in xml.findall('.//w:sdt', NS):
                t = s.find('./w:sdtPr/w:tag', NS)
                name = t.get(W + 'val') if t is not None else ''
                if tag == name:
                    nodes = s.findall('./w:sdtContent//w:t', NS)
                    for i, node in enumerate(nodes):
                        node.text = text if i == 0 else ''
                if checks and name in checks:
                    s.find('./w:sdtPr/w14:checkbox/w14:checked', NS).set(W14 + 'val', checks[name])
                if placeholders and name.startswith('waste_'):
                    checked = s.find('./w:sdtPr/w14:checkbox/w14:checked', NS)
                    if checked is not None:
                        checked.set(W14 + 'val', '0')
                    else:
                        ET.SubElement(s.find('./w:sdtPr', NS), W + 'showingPlcHdr')
            if duplicate_table:
                body = xml.find('./w:body', NS)
                body.append(copy.deepcopy(body.findall('./w:tbl', NS)[1]))
            with ZipFile(out, 'w') as dest:
                for info in z.infolist():
                    dest.writestr(info, ET.tostring(xml, encoding='utf-8', xml_declaration=True)
                                  if info.filename == 'word/document.xml' else z.read(info.filename))
        return out

    def test_measurement_answer_is_scoped_and_preserves_verified_source(self):
        before = self.rows()
        first, second = before['case_candidates']
        request, _ = create_measurement_question(self.db, first['case_id'])
        rid = request['request_id']
        self.assertEqual(create_measurement_question(self.db, first['case_id'])[0], request)
        self.assertEqual(self.export()['cases'][0]['information_requests'][0]['status'], 'open')
        self.assertTrue(record_measurement_answer(self.db, rid, '2026-11-09'))
        self.assertFalse(record_measurement_answer(self.db, rid, '2026-11-09'))
        with self.assertRaises(ValueError):
            record_measurement_answer(self.db, rid, '2026-11-08')
        self.assertEqual(self.rows(), before)
        self.assertEqual(verify(self.db)['status'], 'PASS')
        bundle = self.export()
        a, b = bundle['cases']
        self.assertEqual(b['case_id'], second['case_id'])
        self.assertIsNone(b['quantity']['measured_at']['value'])
        self.assertEqual(b['information_requests'], [])
        self.assertEqual(a['source_form_date']['value'], '2026-11-10')
        self.assertEqual(a['quantity']['measured_at']['value'], '2026-11-09')
        self.assertEqual(a['quantity']['measured_at']['verification'], 'unverified')
        self.assertNotIn('quantity.measured_at', a['missing_fields'])
        self.assertEqual(a['information_requests'][0]['status'], 'answered')
        eid, = a['information_requests'][0]['answer_evidence_ids']
        self.assertEqual(a['quantity']['measured_at']['evidence_ids'], [eid])
        self.assertNotIn(eid, [e['evidence_id'] for e in b['evidence']])
        self.assertEqual((a['draft_revision'], a['case_version'], bundle['published']), (1, 0, False))

    def test_invalid_and_unknown_measurement_answers(self):
        case = self.rows()['case_candidates'][0]
        request, _ = create_measurement_question(self.db, case['case_id'])
        rid = request['request_id']
        with self.assertRaises(ValueError):
            record_measurement_answer(self.db, rid, '2026-02-30')
        self.assertEqual(self.export()['cases'][0]['information_requests'][0]['status'], 'open')
        record_measurement_answer(self.db, rid, '모름')
        first = self.export()['cases'][0]
        self.assertEqual(first['information_requests'][0]['status'], 'unavailable')
        self.assertIn('quantity.measured_at', first['missing_fields'])
        self.assertIsNone(first['quantity']['measured_at']['value'])

    def test_measurement_answer_cannot_target_another_submission(self):
        first = self.rows()['case_candidates'][0]
        request, _ = create_measurement_question(self.db, first['case_id'])
        second_sid, _ = ingest(self.changed_doc('waste_1_quantity', '350kg'), self.db, synthetic=True)
        with self.assertRaises(ValueError):
            record_measurement_answer(self.db, request['request_id'], '2026-11-09', second_sid)
        with self.assertRaises(ValueError):
            create_measurement_question(self.db, first['case_id'], second_sid)

    def test_correction_keeps_previous_answer_and_updates_only_target_case(self):
        first, second = self.rows()['case_candidates']
        rid = create_measurement_question(self.db, first['case_id'])[0]['request_id']
        record_measurement_answer(self.db, rid, '2026-11-09')
        original = self.export()['cases'][0]
        self.assertTrue(record_measurement_answer(self.db, rid, '2026-11-08', correct=True))
        self.assertFalse(record_measurement_answer(self.db, rid, '2026-11-08', correct=True))
        history = measurement_history(self.db, rid)
        self.assertEqual([(a['revision'], a['measured_at']) for a in history],
                         [(1, '2026-11-09'), (2, '2026-11-08')])
        self.assertEqual(create_measurement_question(self.db, first['case_id'])[0]['revision'], 2)
        self.assertEqual(verify(self.db)['status'], 'PASS')
        bundle = self.export()
        a, b = bundle['cases']
        self.assertEqual(b['case_id'], second['case_id'])
        self.assertIsNone(b['quantity']['measured_at']['value'])
        date_fact = a['quantity']['measured_at']
        self.assertEqual(date_fact['value'], '2026-11-08')
        self.assertNotEqual(date_fact['evidence_ids'], original['quantity']['measured_at']['evidence_ids'])
        self.assertEqual(a['information_requests'][0]['answer_evidence_ids'], date_fact['evidence_ids'])
        self.assertEqual(a['source_form_date']['value'], '2026-11-10')
        self.assertEqual((a['draft_revision'], b['draft_revision'], bundle['published']), (2, 0, False))

    def test_correction_requires_first_answer_and_can_record_unknown(self):
        first = self.rows()['case_candidates'][0]
        rid = create_measurement_question(self.db, first['case_id'])[0]['request_id']
        with self.assertRaises(ValueError):
            record_measurement_answer(self.db, rid, '2026-11-08', correct=True)
        self.assertEqual(measurement_history(self.db, rid), [])
        record_measurement_answer(self.db, rid, '2026-11-09')
        record_measurement_answer(self.db, rid, '모름', correct=True)
        case = self.export()['cases'][0]
        self.assertIsNone(case['quantity']['measured_at']['value'])
        self.assertIn('quantity.measured_at', case['missing_fields'])
        self.assertEqual(case['information_requests'][0]['status'], 'unavailable')

    def test_correction_history_gap_blocks_export(self):
        first = self.rows()['case_candidates'][0]
        rid = create_measurement_question(self.db, first['case_id'])[0]['request_id']
        record_measurement_answer(self.db, rid, '2026-11-09')
        record_measurement_answer(self.db, rid, '2026-11-08', correct=True)
        self.mutate('UPDATE measurement_date_corrections SET revision=3')
        with self.assertRaisesRegex(ValueError, '이력'):
            self.export()

    def test_ingest_requires_no_json_and_creates_none(self):
        self.assertTrue(self.db.exists())
        self.assertEqual(list(self.base.glob('*.json')), [])

    def test_original_blob_byte_identical(self):
        with closing(connect(self.db)) as c:
            blob = c.execute('SELECT original_docx FROM submissions').fetchone()[0]
        self.assertEqual(blob, SAMPLE.read_bytes())

    def test_reopen_and_verify_counts(self):
        r = verify(self.db)
        self.assertEqual(r['status'], 'PASS')
        self.assertEqual(r['counts']['controls'], 51)
        self.assertEqual(r['counts']['case_candidates'], 2)
        self.assertEqual(r['counts']['companies'], 1)
        self.assertEqual(r['counts']['sites'], 2)
        self.assertEqual((r['checked'], r['unchecked']), (6, 14))

    def test_company_values_from_db(self):
        co = self.rows()['companies'][0]
        self.assertEqual(co['name'], '가상순환산업 (가상 업체)')
        self.assertEqual(co['contact_name'], '김예시 (가상)')
        self.assertEqual(co['business_number'], '000-00-00000 (테스트용)')
        self.assertEqual(co['contact_phone'], '010-0000-0000 (테스트용)')

    def test_quantities_from_db(self):
        a, b = self.rows()['case_candidates']
        self.assertEqual((a['quantity_value'], a['quantity_unit'], a['quantity_dimension']), ('200', 'kg', 'mass'))
        self.assertEqual((b['quantity_value'], b['quantity_unit'], b['quantity_dimension']), ('100', 'L', 'volume'))

    def test_sites_inherited_and_override(self):
        rows = self.rows()
        a, b = rows['case_candidates']
        sites = {s['site_id']: s for s in rows['sites']}
        self.assertEqual(a['site_inherited'], 1)
        self.assertEqual(b['site_inherited'], 0)
        self.assertIn('A동 자재창고', sites[a['site_id']]['address'])
        self.assertIn('B동 세척실', sites[b['site_id']]['address'])

    def test_hazard_unknown_not_false(self):
        self.assertTrue(all(c['hazard_declaration'] is None for c in self.rows()['case_candidates']))
        self.assertTrue(all(c['hazard_raw'] == '모름' for c in self.rows()['case_candidates']))

    def test_mixture_and_contamination_checks(self):
        a, b = self.rows()['case_candidates']
        self.assertEqual(a['mixture_type'], 'single')
        self.assertIsNone(b['mixture_type'])
        self.assertEqual(a['visible_contamination'], 'not_visible')
        self.assertEqual(b['visible_contamination'], 'partial')

    def test_duplicate_import_same_ids_no_extra_rows(self):
        sid, created = ingest(SAMPLE, self.db, synthetic=True)
        self.assertFalse(created)
        self.assertEqual(sid, self.sid)
        self.assertEqual(verify(self.db)['counts']['case_candidates'], 2)

    def test_duplicate_synthetic_flag_conflict(self):
        with self.assertRaises(ValueError):
            ingest(SAMPLE, self.db, synthetic=False)

    def test_concurrent_duplicate_import(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            result = list(pool.map(lambda _: ingest(SAMPLE, self.db, synthetic=True), range(3)))
        self.assertTrue(all(sid == self.sid and not created for sid, created in result))
        self.assertEqual(verify(self.db)['counts']['case_candidates'], 2)

    def test_transaction_rollback_on_insert_failure(self):
        fresh_db = self.base / 'failed.sqlite3'
        def interrupted(conn, table, sid, rows):
            if table == 'case_candidates':
                raise RuntimeError('simulated crash before commit')
            return insert_rows(conn, table, sid, rows)
        with patch('db_store.insert_rows', side_effect=interrupted), self.assertRaises(RuntimeError):
            ingest(SAMPLE, fresh_db, True)
        with closing(connect(fresh_db)) as c:
            for table in ('submissions', 'controls', 'companies', 'sites', 'case_candidates'):
                self.assertEqual(c.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_modified_control_blocks_export(self):
        self.mutate("UPDATE controls SET raw_value='CORRUPTED' WHERE tag='waste_1_quantity'")
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse((self.base / 'result.json').exists())

    def test_modified_normalized_row_blocks_export(self):
        self.mutate("UPDATE case_candidates SET quantity_value='999'")
        with self.assertRaises(ValueError):
            self.export()

    def test_original_blob_hash_failure(self):
        self.mutate('UPDATE submissions SET original_docx=?', (b'broken',))
        with self.assertRaises(ValueError):
            verify(self.db)

    def test_failed_export_keeps_existing_file(self):
        target = self.base / 'result.json'
        target.write_text('previous valid export', encoding='utf-8')
        self.mutate("UPDATE companies SET name='tampered'")
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(target.read_text(encoding='utf-8'), 'previous valid export')

    def test_foreign_key_constraint(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.mutate("UPDATE case_candidates SET site_id='nonexistent'")

    def test_export_models_validate(self):
        result = self.export()
        Company.model_validate(result['company'])
        for s in result['sites']:
            Site.model_validate(s)
        for c in result['cases']:
            WasteCase.model_validate(c)
        self.assertEqual(json.loads((self.base / 'result.json').read_text())['published'], False)

    def test_export_draft_not_legal_publication(self):
        result = self.export()
        for c in result['cases']:
            self.assertEqual(c['case_version'], 0)
            self.assertTrue(c['synthetic'])
            self.assertIsNone(c['classification']['selected_code'])
            self.assertEqual(c['readiness']['partner_matching']['status'], 'not_assessed')

    def test_form_date_not_measurement_date(self):
        for c in self.export()['cases']:
            self.assertEqual(c['source_form_date']['value'], '2026-11-10')
            self.assertIsNone(c['quantity']['measured_at']['value'])

    def test_measurement_statement_not_verified_report(self):
        a, b = self.export()['cases']
        self.assertIn('저울', a['quantity']['method']['value'])
        self.assertIs(a['quantity']['net_weight']['value'], True)
        self.assertEqual(a['quantity']['method']['verification'], 'unverified')
        self.assertIn('눈금', b['quantity']['method']['value'])
        self.assertIsNone(b['quantity']['net_weight']['value'])

    def test_no_attached_photos_or_analyses_invented(self):
        result = self.export()
        self.assertTrue(all(e['kind'] == 'input_document' for e in result['evidence']))
        for c in result['cases']:
            self.assertIn('photos.full', c['missing_fields'])
            self.assertEqual(c['analysis_results'], [])

    def test_multi_submission_requires_selection(self):
        p = self.changed_doc(tag='waste_1_quantity', text='0.2t')
        sid, _ = ingest(p, self.db, True)
        with self.assertRaises(ValueError):
            verify(self.db)
        self.assertEqual(verify(self.db, sid)['status'], 'PASS')
        self.assertEqual(verify(self.db, self.sid)['status'], 'PASS')

    def test_bad_quantity_preserved_for_review(self):
        p = self.changed_doc(tag='waste_1_quantity', text='200kg 또는 100L')
        sid, _ = ingest(p, self.db, True)
        result = export_json(self.db, self.base / 'bad-quantity.json', sid)
        q = result['cases'][0]['quantity']
        self.assertIsNone(q['value'])
        self.assertEqual(q['raw_value'], '200kg 또는 100L')
        self.assertIn('QUANTITY_NEEDS_REVIEW', result['cases'][0]['limitations'])

    def test_multiple_hazard_checks_not_accepted(self):
        p = self.changed_doc(checks={'waste_1_hazard_1': '1'})
        sid, _ = ingest(p, self.db, True)
        c = export_json(self.db, self.base / 'multi.json', sid)['cases'][0]
        self.assertIsNone(c['hazard_declaration']['value'])
        self.assertIn('HAZARD_UNCHECKED_OR_MULTIPLE', c['limitations'])

    def test_copied_table_tags_create_independent_cases(self):
        p = self.changed_doc(duplicate_table=True)
        sid, _ = ingest(p, self.db, True)
        r = export_json(self.db, self.base / 'copied.json', sid)
        self.assertEqual(len(r['cases']), 3)
        self.assertEqual(len({c['case_id'] for c in r['cases']}), 3)

    def test_placeholder_tables_zero_cases(self):
        p = self.changed_doc(placeholders=True)
        sid, _ = ingest(p, self.db, True)
        self.assertEqual(verify(self.db, sid)['counts']['case_candidates'], 0)

    def test_export_cannot_overwrite_db(self):
        with self.assertRaises(ValueError):
            export_json(self.db, self.db, self.sid)
        self.assertEqual(verify(self.db)['status'], 'PASS')

    def test_read_only_missing_db_not_created(self):
        missing = self.base / 'missing.db'
        with self.assertRaises(sqlite3.Error):
            verify(missing)
        self.assertFalse(missing.exists())

    def test_database_has_no_json_payload_columns(self):
        with closing(connect(self.db)) as c:
            for table in ('submissions', 'controls', 'case_candidates', 'companies', 'sites'):
                columns = [r['name'] for r in c.execute(f'PRAGMA table_info({table})')]
                self.assertFalse(any('json' in name or 'payload' in name for name in columns))

    def test_bundle_schema_and_model(self):
        result = self.export()
        DBReviewExport.model_validate(result)
        self.assertIn('cases', DBReviewExport.model_json_schema()['properties'])

    def test_bundle_wrong_site_rejected(self):
        result = self.export()
        result['cases'][0]['site_id'] = 'missing-site'
        with self.assertRaises(ValueError):
            DBReviewExport.model_validate(result)

    def test_bundle_published_true_rejected(self):
        result = self.export()
        result['published'] = True
        with self.assertRaises(ValueError):
            DBReviewExport.model_validate(result)

    def test_export_extension_guard(self):
        with self.assertRaises(ValueError):
            export_json(self.db, self.base / 'original.docx')
        self.assertFalse((self.base / 'original.docx').exists())

    def test_inspect_displays_db_values(self):
        result = inspect(self.db)
        self.assertIn('200 kg', result)
        self.assertIn('100 L', result)
        self.assertIn('DB VERIFY: PASS', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
