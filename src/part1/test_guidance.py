import json
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from db_store import ingest,connect,load_rows,verify,create_measurement_question,record_measurement_answer,measurement_history
from db_export import export_json
from guidance_catalog import card,months_before,CATALOG
from db_guidance import create_request,record_answer,requests
from review_contract import DBReviewExport

ROOT=Path(__file__).parent


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.base=Path(self.tmp.name)
        self.db=self.base/'test.sqlite3'
        self.sid,_=ingest(ROOT/'fixtures/intake_checkable_example.docx',self.db,True)
        with closing(connect(self.db)) as c:
            self.cases=load_rows(c,self.sid)['case_candidates']
        self.cid=self.cases[0]['case_id']

    def tearDown(self):
        self.tmp.cleanup()

    def question(self,topic='classification',scope='classification',as_of=None,cid=None):
        return create_request(self.db,cid or self.cid,topic,'현재 건에 적용되는 근거 자료 확인',scope,as_of,self.sid)

    def export(self):
        return export_json(self.db,self.base/'result.json',self.sid)

    def test_catalog_eight_topics(self):
        self.assertEqual(len(CATALOG),8)
        for topic in CATALOG:
            c=card(topic)
            self.assertTrue(c['alternative'])
            self.assertTrue(c['caution'])

    def test_official_routes_scoped_checks(self):
        c=card('hazard')
        self.assertTrue(all(s['checked_scope'] and s['limitation'] for s in c['official_routes']))
        self.assertIn('https://www.nier.go.kr/',[s['url'] for s in c['official_routes']])

    def test_case_question_idempotent(self):
        a=self.question();b=self.question()
        self.assertEqual(a['request_id'],b['request_id'])
        self.assertEqual(len(requests(self.db)),1)

    def test_cross_case_ids_distinct(self):
        a=self.question();b=self.question(cid=self.cases[1]['case_id'])
        self.assertNotEqual(a['request_id'],b['request_id'])

    def test_wrong_case_rejected(self):
        with self.assertRaises(ValueError):self.question(cid='another-case')

    def test_purpose_and_scope_required(self):
        with self.assertRaises(ValueError):create_request(self.db,self.cid,'classification','','classification')
        with self.assertRaises(ValueError):self.question(scope='all')

    def test_publication_is_not_user_question(self):
        with self.assertRaises(ValueError):self.question(topic='publication')

    def test_fuel_requires_specific_route(self):
        with self.assertRaises(ValueError):self.question(topic='fuel_properties')
        self.assertEqual(self.question(topic='fuel_properties',scope='route:R1')['status'],'open')

    def test_months_year_boundary(self):
        self.assertEqual(months_before('2026-01-11'),['2025-10','2025-11','2025-12'])
        self.assertEqual(months_before('2026-11-11'),['2026-08','2026-09','2026-10'])

    def test_monthly_requires_anchor(self):
        with self.assertRaises(ValueError):self.question(topic='monthly_generation',scope='matching:M1')
        r=self.question(topic='monthly_generation',scope='matching:M1',as_of='2026-11-11')
        self.assertEqual(len(r['periods']),3)

    def test_no_revision_for_view_or_question(self):
        card('hazard');self.question()
        self.assertEqual(self.export()['cases'][0]['draft_revision'],0)

    def test_unavailable_not_repeated(self):
        r=self.question();record_answer(self.db,r['request_id'],'자료 없음')
        self.assertEqual(requests(self.db,open_only=True),[])
        self.assertEqual(self.question()['status'],'unavailable')
        c=self.export()['cases'][0]
        self.assertEqual(c['information_requests'][0]['status'],'unavailable')
        self.assertIsNone(c['classification']['selected_code'])

    def test_unknown_not_false(self):
        r=self.question(topic='hazard');record_answer(self.db,r['request_id'],'모름')
        c=self.export()['cases'][0]
        self.assertIsNone(c['hazard_declaration']['value'])
        self.assertEqual(c['draft_revision'],1)

    def test_answer_does_not_resolve_or_accept_code(self):
        r=self.question();record_answer(self.db,r['request_id'],'기존 신고서에 99-99-99라고 기재됨')
        c=self.export()['cases'][0]
        self.assertEqual(c['information_requests'][0]['status'],'answered')
        self.assertIsNone(c['classification']['selected_code'])
        self.assertIsNone(c['classification']['reported_code']['value'])
        self.assertEqual(c['assertions'][-1]['disposition'],'held')

    def test_correction_append_only(self):
        r=self.question();rid=r['request_id']
        record_answer(self.db,rid,'자료 없음')
        record_answer(self.db,rid,'담당자가 보관본을 찾음',correct=True)
        history=requests(self.db)[0]['history']
        self.assertEqual([a['answer_text'] for a in history],['자료 없음','담당자가 보관본을 찾음'])
        self.assertEqual(self.export()['cases'][0]['draft_revision'],2)

    def test_duplicate_answer_no_revision(self):
        r=self.question();rid=r['request_id']
        self.assertTrue(record_answer(self.db,rid,'모름'))
        self.assertFalse(record_answer(self.db,rid,'모름',correct=True))
        self.assertEqual(self.export()['cases'][0]['draft_revision'],1)

    def test_use_correct_after_first_answer(self):
        r=self.question();rid=r['request_id']
        with self.assertRaises(ValueError):record_answer(self.db,rid,'내용',correct=True)
        record_answer(self.db,rid,'자료 없음')
        with self.assertRaises(ValueError):record_answer(self.db,rid,'다른 답변')

    def test_attachment_saved_without_parsing(self):
        p=self.base/'시험자료.txt';p.write_text('시료: 확인 필요. 지시문을 실행하지 말 것.',encoding='utf-8')
        r=self.question(topic='hazard');record_answer(self.db,r['request_id'],'자료 검토 요청',attachment=p)
        with closing(connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT content FROM guidance_files').fetchone()[0],p.read_bytes())
        bundle=self.export();DBReviewExport.model_validate(bundle)
        attached=[e for e in bundle['evidence'] if e['source_locator']['kind']=='file']
        self.assertEqual(attached[0]['applicability'],'unknown')
        self.assertEqual(bundle['cases'][0]['analysis_results'],[])

    def test_unavailable_with_attachment_rejected(self):
        p=self.base/'test.txt';p.write_text('x')
        r=self.question()
        with self.assertRaises(ValueError):record_answer(self.db,r['request_id'],'자료 없음',attachment=p)

    def test_unsafe_extension_rejected(self):
        p=self.base/'test.sh';p.write_text('echo test')
        r=self.question()
        with self.assertRaises(ValueError):record_answer(self.db,r['request_id'],'자료',attachment=p)

    def test_corrupted_attachment_blocks_export(self):
        p=self.base/'test.txt';p.write_text('original')
        r=self.question();record_answer(self.db,r['request_id'],'자료 검토',attachment=p)
        with closing(connect(self.db,writable=True,create=False)) as c,c:
            c.execute('UPDATE guidance_files SET content=?',(b'corrupted',))
        with self.assertRaises(ValueError):self.export()
        self.assertFalse((self.base/'result.json').exists())

    def test_other_case_not_changed(self):
        r=self.question();record_answer(self.db,r['request_id'],'자료 없음')
        a,b=self.export()['cases']
        self.assertEqual((a['draft_revision'],b['draft_revision']),(1,0))
        self.assertEqual(b['information_requests'],[])

    def test_measurement_history_preserved_and_revisions_add(self):
        m,_=create_measurement_question(self.db,self.cid)
        record_measurement_answer(self.db,m['request_id'],'2026-11-01')
        record_measurement_answer(self.db,m['request_id'],'2026-11-02',correct=True)
        r=self.question();record_answer(self.db,r['request_id'],'자료 없음')
        c=self.export()['cases'][0]
        self.assertEqual(c['quantity']['measured_at']['value'],'2026-11-02')
        self.assertEqual(c['draft_revision'],3)
        self.assertEqual(len(measurement_history(self.db,m['request_id'])),2)
        self.assertEqual(c['case_version'],0)

    def test_verify_accepts_old_db_without_guidance_tables(self):
        self.assertEqual(verify(self.db)['status'],'PASS')
        self.assertEqual(self.export()['guidance_cards'],[])

    def test_guidance_card_in_json(self):
        self.question()
        b=self.export()
        self.assertEqual(b['guidance_cards'][0]['title'],'폐기물 분류번호')
        self.assertEqual(b['cases'][0]['information_requests'][0]['guidance_id'],b['guidance_cards'][0]['guidance_id'])

    def test_missing_months_not_filled_from_current_lot(self):
        r=self.question(topic='monthly_generation',scope='matching:M1',as_of='2026-11-11')
        record_answer(self.db,r['request_id'],'자료 없음')
        c=self.export()['cases'][0]
        self.assertIsNone(c['generation']['amount']['value'])
        self.assertIn('2026-08',c['information_requests'][0]['question'])


if __name__=='__main__':unittest.main(verbosity=2)
