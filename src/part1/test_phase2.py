import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from datetime import date
from docx import Document
from docx.oxml.ns import qn
from db_store import connect,ingest,load_rows,verify,create_measurement_question,record_measurement_answer
from db_export import export_json
import db_guidance
import phase2_store as store
import phase2_rules as rules
from phase2_export import quantity_key

ROOT=Path(__file__).parent
FIXTURE=ROOT/'fixtures/intake_checkable_example.docx'


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT)
        self.base=Path(self.temp.name);self.db=self.base/'test.db'
        self.sid,_=ingest(FIXTURE,self.db,True)
        with closing(connect(self.db)) as c:self.rows=load_rows(c,self.sid)
        self.cid=self.rows['case_candidates'][0]['case_id']
        self.other=self.rows['case_candidates'][1]['case_id']

    def tearDown(self):self.temp.cleanup()

    def ask(self,field,cid=None,route=None):
        return store.manual_question(self.db,cid or self.cid,field,'가상 테스트: 새 정보로 재확인',route=route)

    def put(self,r,text,rev=0,correct=False):
        return store.answer(self.db,r['request_id'],r['case_id'],text,rev,correct=correct,reason='가상 테스트 정정' if correct else '사용자 최초 답변')

    def bundle(self):return export_json(self.db,self.base/'result.json')
    def case(self):return self.bundle()['cases'][0]

    def changed_fixture(self,changes,checkboxes=None):
        doc=Document(FIXTURE)
        for sdt in doc.element.xpath('//w:sdt'):
            tags=sdt.findall('./'+qn('w:sdtPr')+'/'+qn('w:tag'))
            if not tags:continue
            tag=tags[0].get(qn('w:val'))
            if tag in changes:
                for i,t in enumerate(sdt.iter(qn('w:t'))):t.text=changes[tag] if i==0 else ''
            if checkboxes and tag in checkboxes:
                for node in sdt.iter():
                    if node.tag.endswith('}checked'):node.set('{http://schemas.microsoft.com/office/word/2010/wordml}val','1' if checkboxes[tag] else '0')
        path=self.base/'variant.docx';doc.save(path)
        self.db=self.base/'variant.db';self.sid,_=ingest(path,self.db,True)
        with closing(connect(self.db)) as c:self.rows=load_rows(c,self.sid)
        self.cid=self.rows['case_candidates'][0]['case_id'];self.other=self.rows['case_candidates'][1]['case_id']

    def test_next_at_most_three_case_scoped(self):
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertLessEqual(len(result['questions']),3)
        self.assertTrue(all(q['case_id']==self.cid for q in result['questions']))

    def test_next_request_idempotent_and_no_draft_change(self):
        a=store.next_questions(self.db,case_id=self.cid)
        b=store.next_questions(self.db,case_id=self.cid)
        self.assertEqual(a,b)
        self.assertEqual(self.case()['draft_revision'],0)

    def test_missing_target_precedes_all_other_fields(self):
        self.changed_fixture({'site_address':'','waste_1_name':''})
        r=store.next_questions(self.db,case_id=self.cid)
        self.assertEqual({q['field'] for q in r['questions']},{'site_address','name'})
        self.assertEqual(len(r['blocked_cases']),1)

    def test_unknown_target_not_reasked_and_blocks_downstream(self):
        self.changed_fixture({'site_address':''})
        r=store.next_questions(self.db,case_id=self.cid)['questions'][0]
        self.put(r,'모름')
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertEqual(result['questions'],[])
        self.assertTrue(result['blocked_cases'])

    def test_target_confirmation_only_this_case(self):
        self.changed_fixture({'site_address':''})
        r=store.next_questions(self.db,case_id=self.cid)['questions'][0]
        self.put(r,'전주시 가상로 12 A동')
        b=self.bundle();a,other=b['cases']
        self.assertIsNotNone(a['site_id'])
        self.assertNotEqual(a['site_id'],other['site_id'])
        self.assertEqual(other['draft_revision'],0)
        self.assertEqual(b['database_verification']['counts']['phase2_sites'],1)
        self.assertEqual(len(b['sites']),2)

    def test_target_correction_preserves_both_site_records(self):
        r=self.ask('site_address');self.put(r,'전주시 가상로 12 A동')
        self.put(r,'전주시 가상로 12 B동',1,True)
        b=self.bundle()
        self.assertEqual(len(b['sites']),4)
        site=next(s for s in b['sites'] if s['site_id']==b['cases'][0]['site_id'])
        self.assertEqual(site['address']['value'],'전주시 가상로 12 B동')
        self.assertEqual(len(store.history(self.db,r['request_id'],self.cid)['history']),2)

    def test_process_missing_question(self):
        self.changed_fixture({'waste_1_process':''})
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertEqual(qs[0]['field'],'process_description')
        self.put(qs[0],'PE 포장재 절단 후 회수')
        self.assertEqual(self.case()['process_description']['value'],'PE 포장재 절단 후 회수')

    def test_multiple_hazard_checkbox_is_conflict_question(self):
        self.changed_fixture({}, {'waste_1_hazard_0':True,'waste_1_hazard_2':True})
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertIn('hazard_declaration',[q['field'] for q in qs])

    def test_quantity_conflict_question_and_tonne_conversion(self):
        self.changed_fixture({'waste_1_quantity':'200kg 또는 100L'})
        qs=store.next_questions(self.db,case_id=self.cid)['questions'];r=next(q for q in qs if q['field']=='quantity')
        self.put(r,'0.2t')
        q=self.case()['quantity']
        self.assertEqual(quantity_key(q),('2E+2','kg','mass'))
        self.assertEqual(q['raw_value'],'0.2t')
        self.assertIn('t->kg',q['conversion_rule'])

    def test_volume_never_mass_conversion(self):
        r=self.ask('quantity');self.put(r,'100L')
        q=self.case()['quantity'];self.assertEqual(q['dimension'],'volume');self.assertEqual(q['unit'],'L')

    def test_invalid_quantity_no_write(self):
        r=self.ask('quantity')
        for bad in ('0kg','-1kg','100','200L드럼 1개','포대 1.5개','1e8kg','200kg/월'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.put(r,bad)
        self.assertEqual(store.history(self.db,r['request_id'],self.cid)['revision'],0)

    def test_quantity_unknown_clears_quantity_and_related_facts(self):
        r=self.ask('quantity');self.put(r,'자료 없음')
        q=self.case()['quantity']
        self.assertIsNone(q['value']);self.assertIsNone(q['unit'])
        self.assertIsNone(q['method']['value'])
        self.assertIsNone(q['measured_at']['value'])

    def test_measurement_date_typed_and_other_case_unchanged(self):
        r=self.ask('quantity.measured_at');self.put(r,'2026-11-10')
        a,b=self.bundle()['cases']
        self.assertEqual(a['quantity']['measured_at']['value'],'2026-11-10')
        self.assertIsNone(b['quantity']['measured_at']['value'])
        self.assertEqual(a['draft_revision'],1);self.assertEqual(b['draft_revision'],0)

    def test_future_and_invalid_dates_rejected(self):
        r=self.ask('quantity.measured_at')
        for bad in ('20261110','2026-2-1','2026-02-30','2999-01-01','어제'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.put(r,bad)

    def test_date_unknown_and_synonyms_suppress_repeat(self):
        r=self.ask('quantity.measured_at');self.put(r,'잘 모르겠어요')
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertNotIn('quantity.measured_at',[q['field'] for q in qs])
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])
        self.assertEqual(store.history(self.db,r['request_id'],self.cid)['status'],'unavailable')

    def test_date_correction_append_only(self):
        r=self.ask('quantity.measured_at');self.put(r,'2026-11-01');self.put(r,'2026-11-02',1,True)
        hist=store.history(self.db,r['request_id'],self.cid)['history']
        self.assertEqual([a['raw_value'] for a in hist],['2026-11-01','2026-11-02'])
        self.assertEqual(self.case()['draft_revision'],2)

    def test_duplicate_answer_does_not_increment(self):
        r=self.ask('quantity.measured_at');self.put(r,'모름')
        self.assertFalse(self.put(r,'모름'))
        self.assertEqual(self.case()['draft_revision'],1)

    def test_stale_revision_rejected(self):
        r=self.ask('physical_state');self.put(r,'마른 조각')
        with self.assertRaisesRegex(ValueError,'REVISION_CONFLICT'):self.put(r,'액체 상태',0,True)
        self.assertEqual(self.case()['physical_state']['value'],'마른 조각')

    def test_first_vs_correction_commands(self):
        r=self.ask('physical_state')
        with self.assertRaises(ValueError):self.put(r,'마른 조각',0,True)
        self.put(r,'마른 조각')
        with self.assertRaises(ValueError):self.put(r,'액체 상태',1)

    def test_correction_reason_required(self):
        r=self.ask('physical_state');self.put(r,'마른 조각')
        with self.assertRaises(ValueError):store.answer(self.db,r['request_id'],self.cid,'액체 상태',1,correct=True,reason=' ')

    def test_cross_case_answer_rejected(self):
        r=self.ask('physical_state')
        with self.assertRaises(ValueError):store.answer(self.db,r['request_id'],self.other,'마른 조각',0)
        self.assertEqual(self.case()['draft_revision'],0)

    def test_null_vs_false_hazard(self):
        r=self.ask('hazard_declaration');self.put(r,'없음')
        c=self.case();self.assertIs(c['hazard_declaration']['value'],False)
        self.assertEqual(c['hazard_declaration']['verification'],'unverified')
        self.assertEqual(c['analysis_results'],[])
        self.put(r,'모름',1,True)
        self.assertIsNone(self.case()['hazard_declaration']['value'])

    def test_ambiguous_hazard_answer_not_mapped_false(self):
        r=self.ask('hazard_declaration')
        with self.assertRaises(ValueError):self.put(r,'없어요')
        self.assertEqual(self.case()['draft_revision'],0)

    def test_mixture_enum_validation(self):
        r=self.ask('mixture_type')
        with self.assertRaises(ValueError):self.put(r,'그냥 섞인듯')
        self.put(r,'붙어 있음')
        self.assertEqual(self.case()['mixture_type']['value'],'composite')

    def test_components_no_ratio_inference(self):
        r=self.ask('components');self.put(r,'PE (포장재 라벨의 문구)')
        c=self.case()['components'][0]
        self.assertEqual(c['name']['value'],'PE (포장재 라벨의 문구)')
        self.assertIsNone(c['proportion_percent']['value'])

    def test_separability_requires_selected_route(self):
        with self.assertRaises(ValueError):self.ask('separability')
        r=self.ask('separability',route='R1');self.put(r,'불가능')
        self.assertIs(self.case()['separability']['value'],False)

    def test_classification_requires_evidence_not_code_answer(self):
        r=self.ask('classification_evidence')
        with self.assertRaises(ValueError):self.put(r,'99-99-99')
        self.assertIsNone(self.case()['classification']['selected_code'])
        self.assertIsNone(self.case()['classification']['reported_code']['value'])

    def test_document_bridge_idempotent_and_missing_no_reask(self):
        r=self.ask('classification_evidence')
        g=store.documents(self.db,r['request_id'],self.cid)
        self.assertEqual(g['request_id'],store.documents(self.db,r['request_id'],self.cid)['request_id'])
        db_guidance.record_answer(self.db,g['request_id'],'자료 없음')
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertNotIn('classification_evidence',[q['field'] for q in qs])
        c=self.case();self.assertEqual(c['draft_revision'],1)
        infos=[i for i in c['information_requests'] if i['guidance_id']]
        self.assertEqual(len(infos),1);self.assertEqual(infos[0]['status'],'unavailable')

    def test_existing_guidance_request_reused(self):
        g=db_guidance.create_request(self.db,self.cid,'classification','기존 보완 목적','classification')
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        q=next(q for q in qs if q['field']=='classification_evidence')
        self.assertEqual(q['source'],'guidance');self.assertEqual(q['request_id'],g['request_id'])

    def test_attachment_not_auto_accepted(self):
        r=self.ask('classification_evidence');g=store.documents(self.db,r['request_id'],self.cid)
        f=self.base/'가상회신.txt';f.write_text('이 내용은 시연용. 다른 현장 자료일 수 있음.',encoding='utf-8')
        db_guidance.record_answer(self.db,g['request_id'],'참고자료',attachment=f)
        c=self.case();self.assertIsNone(c['classification']['selected_code'])
        attached=[e for e in c['evidence'] if e['source_locator']['kind']=='file']
        self.assertEqual(attached[0]['applicability'],'unknown')

    def test_old_measurement_open_question_reused(self):
        old,_=create_measurement_question(self.db,self.cid)
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        r=next(q for q in qs if q['field']=='quantity.measured_at')
        self.assertEqual(r['source'],'measurement');self.assertEqual(r['request_id'],old['request_id'])

    def test_old_measurement_unknown_not_reasked(self):
        old,_=create_measurement_question(self.db,self.cid)
        record_measurement_answer(self.db,old['request_id'],'모름')
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertNotIn('quantity.measured_at',[q['field'] for q in qs])

    def test_quantity_change_invalidates_old_date_and_new_context(self):
        old,_=create_measurement_question(self.db,self.cid)
        record_measurement_answer(self.db,old['request_id'],'2026-11-01')
        q=self.ask('quantity');self.put(q,'300kg')
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertIn('quantity.measured_at',[q['field'] for q in qs])

    def test_quantity_correction_after_phase2_date_requires_reconfirmation(self):
        q=self.ask('quantity');self.put(q,'300kg')
        d=self.ask('quantity.measured_at');self.put(d,'2026-11-10')
        self.put(q,'400kg',1,True)
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])
        with self.assertRaises(ValueError):self.put(d,'2026-11-09',1,True)
        new=self.ask('quantity.measured_at')
        self.assertNotEqual(d['request_id'],new['request_id'])

    def test_newer_legacy_date_cannot_assert_new_quantity_context(self):
        q=self.ask('quantity');self.put(q,'300kg')
        old,_=create_measurement_question(self.db,self.cid)
        record_measurement_answer(self.db,old['request_id'],'2026-11-10')
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])

    def test_target_change_rejects_stale_pending_question(self):
        p=self.ask('process_description')
        s=self.ask('site_address');self.put(s,'전주시 새현장 B동')
        with self.assertRaises(ValueError):self.put(p,'절단 후 수거')

    def test_db_raw_rows_not_overwritten(self):
        r=self.ask('quantity');self.put(r,'300kg')
        with closing(connect(self.db)) as c:after=load_rows(c,self.sid)
        self.assertEqual(self.rows,after)
        self.assertEqual(verify(self.db)['status'],'PASS')

    def test_corrupted_normalization_blocks_export(self):
        r=self.ask('quantity');self.put(r,'300kg')
        with closing(connect(self.db,writable=True,create=False)) as c,c:
            c.execute("UPDATE phase2_answers SET quantity_value='999'")
        with self.assertRaises(ValueError):self.bundle()

    def test_changed_site_row_detected(self):
        r=self.ask('site_address');self.put(r,'전주시 가상 현장')
        with closing(connect(self.db,writable=True,create=False)) as c,c:
            c.execute("UPDATE phase2_sites SET address='변조된 주소'")
        with self.assertRaises(ValueError):self.bundle()

    def test_no_bulk_answer_for_multiple_cases(self):
        r=self.ask('physical_state')
        for text in ('둘 다','위와 같음','모두'):
            with self.assertRaises(ValueError):self.put(r,text)

    def test_document_bridge_rejects_wrong_case(self):
        r=self.ask('classification_evidence')
        with self.assertRaises(ValueError):store.documents(self.db,r['request_id'],self.other)

    def test_guide_view_and_request_not_revision(self):
        r=self.ask('classification_evidence');store.documents(self.db,r['request_id'],self.cid)
        self.assertEqual(self.case()['draft_revision'],0)

    def test_publication_and_readiness_unchanged(self):
        r=self.ask('physical_state');self.put(r,'마른 비닐 조각')
        b=self.bundle();self.assertFalse(b['published']);self.assertEqual(b['cases'][0]['case_version'],0)
        self.assertTrue(b['cases'][0]['classification']['review_required'])

    def test_mixture_change_invalidates_separability(self):
        m=self.ask('mixture_type');self.put(m,'따로 섞임')
        s=self.ask('separability',route='R1');self.put(s,'가능')
        self.put(m,'붙어 있음',1,True)
        self.assertIsNone(self.case()['separability']['value'])
        with self.assertRaises(ValueError):self.put(s,'불가능',1,True)
        new=self.ask('separability',route='R1')
        self.assertNotEqual(s['request_id'],new['request_id'])

    def test_mixture_roundtrip_does_not_revive_old_answer(self):
        m=self.ask('mixture_type');self.put(m,'따로 섞임')
        s=self.ask('separability',route='R1');self.put(s,'가능')
        self.put(m,'붙어 있음',1,True);self.put(m,'따로 섞임',2,True)
        self.assertIsNone(self.case()['separability']['value'])

    def test_quantity_roundtrip_does_not_revive_old_date(self):
        q=self.ask('quantity');self.put(q,'300kg')
        d=self.ask('quantity.measured_at');self.put(d,'2026-11-10')
        self.put(q,'400kg',1,True);self.put(q,'300kg',2,True)
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])

    def test_context_change_does_not_suppress_needed_document_question(self):
        r=self.ask('classification_evidence');g=store.documents(self.db,r['request_id'],self.cid)
        db_guidance.record_answer(self.db,g['request_id'],'자료 없음')
        s=self.ask('site_address');self.put(s,'전주시 새 현장 B동')
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertGreater(result['pending_count'],0)
        new=self.ask('classification_evidence')
        newg=store.documents(self.db,new['request_id'],self.cid)
        self.assertNotEqual(g['request_id'],newg['request_id'])
        c=self.case()
        self.assertIn('STALE_GUIDANCE_CONTEXT:'+g['request_id'],c['limitations'])
        self.assertNotIn(g['request_id'],[i['request_id'] for i in c['information_requests']])

    def test_process_specific_substance_triggers_evidence_not_designation(self):
        p=self.ask('process_description');self.put(p,'유기용제를 사용한 세척 작업')
        qs=store.next_questions(self.db,case_id=self.cid)['questions']
        self.assertIn('hazard_evidence',[q['field'] for q in qs])
        self.assertIsNone(self.case()['hazard_declaration']['value'])

    def test_quantity_and_explicit_measurement_disagreement(self):
        self.changed_fixture({'waste_1_quantity_evidence':'실제 측정량: 100kg'})
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertEqual(result['questions'][0]['field'],'quantity')
        self.assertEqual(result['questions'][0]['priority'],5)
        self.assertNotIn('quantity.measured_at',[q['field'] for q in result['questions']])

    def test_equivalent_quantity_units_are_not_conflict(self):
        self.changed_fixture({'waste_1_quantity':'1m3','waste_1_quantity_evidence':'실제 측정량: 1000L'})
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertNotIn('quantity',[q['field'] for q in result['questions']])

    def test_ambiguous_target_triggers_first_question(self):
        self.changed_fixture({'site_address':'A동 또는 B동'})
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertEqual([q['field'] for q in result['questions']],['site_address'])
        with self.assertRaises(ValueError):self.put(result['questions'][0],'A동 또는 C동')

    def test_unknown_mixture_in_original_not_reasked(self):
        self.changed_fixture({}, {'waste_1_mixture_0':False,'waste_1_mixture_1':False,'waste_1_mixture_2':True})
        result=store.next_questions(self.db,case_id=self.cid)
        self.assertNotIn('mixture_type',[q['field'] for q in result['questions']])

    def test_date_after_site_change_uses_new_question(self):
        old,_=create_measurement_question(self.db,self.cid)
        record_measurement_answer(self.db,old['request_id'],'2026-11-10')
        s=self.ask('site_address');self.put(s,'전주시 변경된 현장')
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])
        q=self.ask('quantity.measured_at');self.put(q,'2026-11-09')
        self.assertEqual(self.case()['quantity']['measured_at']['value'],'2026-11-09')

    def test_date_corrected_to_unknown_does_not_resurrect_original(self):
        old,_=create_measurement_question(self.db,self.cid)
        record_measurement_answer(self.db,old['request_id'],'2026-11-10')
        q=self.ask('quantity.measured_at');self.put(q,'2026-11-09');self.put(q,'모름',1,True)
        self.assertIsNone(self.case()['quantity']['measured_at']['value'])

    def test_new_method_cannot_contradict_recorded_quantity(self):
        r=self.ask('quantity.method')
        with self.assertRaisesRegex(ValueError,'처리량과 다릅니다'):self.put(r,'실제 측정량: 100kg')
        self.assertEqual(store.history(self.db,r['request_id'],self.cid)['revision'],0)

    def test_unknown_field_route_limit_validation(self):
        with self.assertRaises(ValueError):self.ask('selected_code')
        with self.assertRaises(ValueError):store.next_questions(self.db,limit=4)
        with self.assertRaises(ValueError):store.next_questions(self.db,route='R 1')
        with self.assertRaises(ValueError):store.next_questions(self.db,case_id='wrong-case')


if __name__=='__main__':unittest.main(verbosity=2)
