import json
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from db_store import connect, ingest, load_rows, verify
from db_export import export_json
from initial_review import copy_template, preview, confirm, history, format_preview
from db_followup import table_exists

ROOT=Path(__file__).parent


class InitialReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.base=Path(self.tmp.name)
        self.db=self.base/'review.db'
        self.sid,_=ingest(ROOT/'fixtures/intake_checkable_example.docx', self.db, True)
        with closing(connect(self.db)) as c:
            self.cases=load_rows(c,self.sid)['case_candidates']
        self.case=self.cases[0]['case_id']

    def tearDown(self):self.tmp.cleanup()

    def test_copy_blank_template_and_no_overwrite(self):
        out=self.base/'입력서.docx'
        copy_template(out)
        self.assertEqual(out.read_bytes(),(ROOT/'fixtures/blank_intake_template.docx').read_bytes())
        with self.assertRaises(FileExistsError):copy_template(out)

    def test_only_docx_output(self):
        with self.assertRaises(ValueError):copy_template(self.base/'template.txt')

    def test_blank_template_zero_cases(self):
        sid,_=ingest(ROOT/'fixtures/blank_intake_template.docx',self.base/'blank.db',True)
        self.assertEqual(verify(self.base/'blank.db',sid)['counts']['case_candidates'],0)

    def test_sample_preview_db_and_source(self):
        p=preview(self.db,self.case)
        self.assertEqual(p['case']['quantity_value'],'200')
        self.assertEqual(p['case']['quantity_unit'],'kg')
        self.assertEqual(p['case']['hazard_raw'],'모름')
        self.assertTrue(any(c['tag']=='waste_1_quantity' and c['raw_value']=='200kg' for c in p['source_controls']))
        self.assertIn('원문 컨트롤',format_preview(p))
        self.assertTrue(any('사진' in w for w in p['warnings']))

    def test_second_case_not_crosslinked(self):
        a=preview(self.db,self.case);b=preview(self.db,self.cases[1]['case_id'])
        self.assertNotEqual(a['review_hash'],b['review_hash'])
        self.assertEqual(b['case']['quantity_value'],'100')
        self.assertIn('B동',b['site']['address'])

    def test_preview_is_read_only(self):
        preview(self.db,self.case)
        with closing(connect(self.db)) as c:self.assertFalse(table_exists(c,'initial_reviews'))

    def test_confirm_needs_review_hash_and_ack(self):
        p=preview(self.db,self.case)
        with self.assertRaises(ValueError):confirm(self.db,self.case,'검토자','wrong',True)
        with self.assertRaises(ValueError):confirm(self.db,self.case,'검토자',p['review_hash'],False)
        self.assertEqual(history(self.db,self.case)[1],[])

    def test_confirm_requires_reviewer(self):
        p=preview(self.db,self.case)
        with self.assertRaises(ValueError):confirm(self.db,self.case,' ',p['review_hash'],True)

    def test_confirm_record_reopen_and_no_duplicate(self):
        p=preview(self.db,self.case)
        self.assertTrue(confirm(self.db,self.case,'가상 검토자',p['review_hash'],True))
        self.assertFalse(confirm(self.db,self.case,'가상 검토자',p['review_hash'],True))
        p,records=history(self.db,self.case)
        self.assertEqual(len(records),1)
        self.assertTrue(records[0]['matches_current'])
        self.assertEqual(verify(self.db)['status'],'PASS')

    def test_review_not_legal_fact_or_version(self):
        p=preview(self.db,self.case)
        confirm(self.db,self.case,'가상 검토자',p['review_hash'],True)
        b=export_json(self.db,self.base/'review.json')
        c=b['cases'][0]
        self.assertEqual(c['case_version'],0)
        self.assertEqual(c['draft_revision'],0)
        self.assertIsNone(c['classification']['selected_code'])
        self.assertTrue(c['classification']['review_required'])
        self.assertEqual(c['name']['verification'],'unverified')
        self.assertIsNone(c['hazard_declaration']['value'])
        self.assertFalse(b['published'])

    def test_preview_wrong_case_rejected(self):
        with self.assertRaises(ValueError):preview(self.db,'missing')

    def test_tampered_input_prevents_confirm(self):
        p=preview(self.db,self.case)
        with closing(connect(self.db,writable=True,create=False)) as c,c:
            c.execute("UPDATE case_candidates SET quantity_value='999' WHERE case_id=?",(self.case,))
        with self.assertRaises(ValueError):confirm(self.db,self.case,'검토자',p['review_hash'],True)

    def test_review_case_independent(self):
        p=preview(self.db,self.case)
        confirm(self.db,self.case,'검토자',p['review_hash'],True)
        self.assertEqual(history(self.db,self.cases[1]['case_id'])[1],[])


if __name__=='__main__':unittest.main(verbosity=2)
