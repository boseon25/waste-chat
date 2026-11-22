import json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from docx import Document
from db_store import connect,ingest,load_rows,verify
import external_store as s
import external_documents as d
import phase2_store as p2
from external_fields import normalize,DEFAULT_NEEDS
import route_monitor as rm
from contact_routes import ROUTES

ROOT=Path(__file__).parent

class ExternalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.root=Path(self.temp.name);self.db=self.root/'test.sqlite3'
        self.sid,_=ingest(ROOT/'fixtures/intake_checkable_example.docx',self.db,True)
        with closing(connect(self.db)) as c:self.cid,self.other=[r['case_id'] for r in load_rows(c,self.sid)['case_candidates']]
        self.file=self.root/'reply.txt';self.file.write_text('합성 자료. 실제 회신 아님.',encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def plan(self,needs=None,sample=None,cid=None,purpose='가상 목적'):
        return s.plan(self.db,cid or self.cid,purpose,needs,sample)
    def packet(self,needs=None,sample=None):return self.plan(needs,sample)['packets'][0]['packet_id']
    def attach(self,pid,sample=None,issuer='가상 발급기관',day='2026-11-14'):
        return s.attach(self.db,self.cid,pid,self.file,issuer,day,sample)
    def review(self,pid,did,field='classification.reported_code',value='99-99-99',**kw):
        return s.record_value(self.db,self.cid,pid,did,field,value,'표 1 해당 항목 행','가상 검토자','원문/현장/품목/기간 대조 시연',**kw)
    def change(self,field='process_description',value='가상 신규 공정'):
        q=p2.manual_question(self.db,self.cid,field,'가상 맥락 변경')
        p2.answer(self.db,q['request_id'],self.cid,value,0,reason='가상 변경')
    def test_default_only_administrative(self):
        a=self.plan();self.assertEqual(len(a['packets']),1);self.assertEqual(a['packets'][0]['kind'],'authority')
    def test_idempotent_packets(self):self.assertEqual(self.plan()['packets'],self.plan()['packets'])
    def test_two_document_groups(self):
        a=self.plan(DEFAULT_NEEDS+['analysis.moisture','analysis.chlorine'],'S1');self.assertEqual(len(a['packets']),2)
    def test_unsupported_field_rejected(self):
        with self.assertRaises(ValueError):self.plan(['all_tests'])
    def test_empty_purpose_rejected(self):
        with self.assertRaises(ValueError):self.plan(purpose=' ')
    def test_identity_gate(self):
        with patch('phase2_rules.identity_blocked',return_value=True):
            a=self.plan();self.assertTrue(a['blocked']);self.assertEqual(a['packets'],[])
    def test_wrong_case(self):
        pid=self.packet()
        with self.assertRaises(ValueError):s.get(self.db,self.other,pid)
    def test_attachment_not_coverage(self):
        pid=self.packet();self.attach(pid);self.assertEqual(len(self.plan()['missing_fields']),3)
    def test_held_not_coverage(self):
        pid=self.packet();did=self.attach(pid);self.review(pid,did);self.assertEqual(len(self.plan()['missing_fields']),3)
    def test_partial_response_only_remaining(self):
        pid=self.packet();did=self.attach(pid);self.review(pid,did,confirm=True)
        a=self.plan();self.assertEqual(set(a['missing_fields']),set(DEFAULT_NEEDS)-{'classification.reported_code'})
    def test_new_packet_can_reuse_current_checked_values(self):
        pid=self.packet();did=self.attach(pid);self.review(pid,did,confirm=True)
        fresh=self.plan()['packets'][0]['packet_id'];self.assertNotEqual(pid,fresh)
        self.assertNotIn('classification.reported_code',s.get(self.db,self.cid,fresh)['fields'])
    def test_all_values_covered_no_new_packet(self):
        pid=self.packet();did=self.attach(pid)
        self.review(pid,did,confirm=True)
        for f in ('classification.applicability','classification.conditions'):self.review(pid,did,field=f,value='가상 회신 조건 확인',confirm=True)
        self.assertEqual(self.plan()['packets'],[])
        with self.assertRaises(ValueError):d.draft(self.db,self.cid,pid,self.root/'new')
    def test_unknown_not_reasked(self):
        pid=self.packet(['classification.reported_code']);did=self.attach(pid)
        self.review(pid,did,value='자료 없음',confirm=True)
        a=self.plan(['classification.reported_code']);self.assertEqual(a['packets'],[]);self.assertEqual(len(a['unavailable_fields']),1)
        with self.assertRaises(ValueError):d.draft(self.db,self.cid,pid,self.root/'new')
    def test_nd_is_not_zero(self):
        pid=self.packet(['analysis.chlorine'],'S1');did=self.attach(pid,'S1')
        self.review(pid,did,field='analysis.chlorine',value='불검출',unit='mg/kg',confirm=True)
        v=s.get(self.db,self.cid,pid)['reviews'][0];self.assertIsNone(v['normalized_value']);self.assertEqual(v['raw_value'],'불검출');self.assertEqual(v['status'],'unknown')
    def test_zero_is_measured_not_missing(self):
        pid=self.packet(['analysis.moisture'],'S1');did=self.attach(pid,'S1')
        self.review(pid,did,field='analysis.moisture',value='0',unit='%',basis='wet',method='가상법',confirm=True)
        self.assertEqual(self.plan(['analysis.moisture'],'S1')['packets'],[])
    def test_lab_sample_mismatch(self):
        pid=self.packet(['analysis.moisture'],'S1');did=self.attach(pid,'S2')
        with self.assertRaises(ValueError):self.review(pid,did,field='analysis.moisture',value='10',unit='%',basis='wet',method='가상법',confirm=True)
    def test_lab_missing_basis_method(self):
        pid=self.packet(['analysis.moisture'],'S1');did=self.attach(pid,'S1')
        with self.assertRaises(ValueError):self.review(pid,did,field='analysis.moisture',value='10',unit='%',confirm=True)
    def test_lab_missing_sample(self):
        pid=self.packet(['analysis.moisture']);did=self.attach(pid)
        with self.assertRaises(ValueError):self.review(pid,did,field='analysis.moisture',value='10',unit='%',basis='wet',method='가상法',confirm=True)
    def test_units_range_and_nonfinite(self):
        for v,u in [('101','%'),('-1','%'),('NaN','%'),('Infinity','%'),('1','L')]:
            with self.subTest(v=v,u=u),self.assertRaises(ValueError):normalize('analysis.moisture',v,u)
    def test_unknown_document_metadata_hold(self):
        pid=self.packet();did=self.attach(pid,issuer='미확인',day='')
        with self.assertRaises(ValueError):self.review(pid,did,confirm=True)
    def test_metadata_correction_append_only(self):
        pid=self.packet();a=self.attach(pid,issuer='미확인',day='');b=self.attach(pid)
        self.assertNotEqual(a,b);self.assertEqual(len(s.get(self.db,self.cid,pid)['documents']),2)
    def test_duplicate_attachment_idempotent(self):
        pid=self.packet();self.assertEqual(self.attach(pid),self.attach(pid));self.assertEqual(len(s.get(self.db,self.cid,pid)['documents']),1)
    def test_future_date_rejected(self):
        pid=self.packet()
        with self.assertRaises(ValueError):self.attach(pid,day='2999-01-01')
    def test_empty_file_rejected(self):
        pid=self.packet();self.file.write_bytes(b'')
        with self.assertRaises(ValueError):self.attach(pid)
    def test_bad_extension_rejected(self):
        pid=self.packet();self.file=self.root/'evil.exe';self.file.write_bytes(b'x')
        with self.assertRaises(ValueError):self.attach(pid)
    def test_review_other_packet_rejected(self):
        pid=self.packet();did=self.attach(pid);other=self.plan(['analysis.moisture'],'S1')['packets'][0]['packet_id']
        with self.assertRaises(ValueError):self.review(other,did,field='analysis.moisture',value='1',unit='%')
    def test_review_revision_conflict(self):
        pid=self.packet();did=self.attach(pid);self.review(pid,did,confirm=True)
        with self.assertRaisesRegex(ValueError,'REVIEW_CONFLICT'):self.review(pid,did,value='99-99-98',confirm=True)
    def test_correction_append_only(self):
        pid=self.packet();did=self.attach(pid);rid=self.review(pid,did,confirm=True)
        self.review(pid,did,value='99-99-98',confirm=True,expected_review=rid)
        values=s.get(self.db,self.cid,pid)['reviews'];self.assertEqual([v['raw_value'] for v in values],['99-99-99','99-99-98'])
    def test_context_change_invalidates_coverage(self):
        pid=self.packet();did=self.attach(pid);self.review(pid,did,confirm=True);self.change()
        self.assertTrue(s.get(self.db,self.cid,pid)['stale']);self.assertEqual(len(self.plan()['missing_fields']),3)
        with self.assertRaises(ValueError):d.draft(self.db,self.cid,pid,self.root/'old')
    def test_stale_reply_can_be_stored_but_not_accepted(self):
        pid=self.packet();self.change();did=self.attach(pid)
        with self.assertRaises(ValueError):self.review(pid,did,confirm=True)
    def test_other_case_change_does_not_stale(self):
        pid=self.packet();q=p2.manual_question(self.db,self.other,'process_description','가상 다른 건 변경');p2.answer(self.db,q['request_id'],self.other,'다른 가상 공정',0)
        self.assertFalse(s.get(self.db,self.cid,pid)['stale'])
    def test_docx_round_trip_and_no_other_case(self):
        pid=self.packet();d.draft(self.db,self.cid,pid,self.root/'draft')
        text='\n'.join(p.text for p in Document(self.root/'draft/request.docx').paragraphs)
        self.assertIn('PE 필름',text);self.assertNotIn('B동 세척실',text);self.assertIn('실제 접수 서비스 URL',text);self.assertIn('미제출',text)
    def test_draft_not_overwritten(self):
        pid=self.packet();d.draft(self.db,self.cid,pid,self.root/'draft')
        with self.assertRaises(ValueError):d.draft(self.db,self.cid,pid,self.root/'draft')
    def test_submit_requires_draft(self):
        pid=self.packet()
        with self.assertRaises(ValueError):s.submitted(self.db,self.cid,pid,'가상 접수번호','가상 사용자')
    def test_submit_is_manual_record(self):
        pid=self.packet();d.draft(self.db,self.cid,pid,self.root/'draft');s.submitted(self.db,self.cid,pid,'합성-001','가상 사용자')
        self.assertEqual(s.get(self.db,self.cid,pid)['events'][-1]['kind'],'user_reported_submission')
    def test_no_publication_no_code_assignment(self):
        pid=self.packet();did=self.attach(pid);self.review(pid,did,confirm=True)
        b=d.export_case(self.db,self.cid);self.assertFalse(b['published']);self.assertEqual(b['case_version'],0);self.assertIsNone(b['case']['classification']['selected_code']);self.assertEqual(b['draft_revision'],0)
        self.assertEqual(verify(self.db)['status'],'PASS')
    def test_blob_tamper_caught_in_native_verification(self):
        pid=self.packet();self.attach(pid)
        with closing(sqlite3.connect(self.db)) as c,c:c.execute("UPDATE external_documents SET content=X'00'")
        with self.assertRaises(ValueError):verify(self.db)
    def test_cross_case_no_export_leak(self):
        pid=self.packet();self.attach(pid);other=self.plan(cid=self.other)
        b=d.export_case(self.db,self.cid);self.assertTrue(all(x['case_id']==self.cid for x in b['document_packets']))
        self.assertNotIn(other['packets'][0]['packet_id'],json.dumps(b))
    def test_context_sample_purpose_isolated(self):
        pid=self.packet(['analysis.moisture'],'S1');did=self.attach(pid,'S1')
        self.review(pid,did,field='analysis.moisture',value='12',unit='%',basis='wet',method='가상법',confirm=True)
        self.assertEqual(len(self.plan(['analysis.moisture'],'S2')['missing_fields']),1)
        self.assertEqual(len(self.plan(['analysis.moisture'],'S1',purpose='다른 판단 목적')['missing_fields']),1)

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.db=Path(self.temp.name)/'routes.db'
    def tearDown(self):self.temp.cleanup()
    def observation(self,**kw):
        data=dict(route_id='authority',checked_at=rm.stamp(),catalog_hash=rm.digest(ROUTES['authority']),result='login_required',path_hash='path1',procedure_hash='procedure1')
        data.update(kw);return data
    def approved(self):
        x=rm.record(self.db,self.observation());rm.approve(self.db,x['check_id'],'테스트 검토자');return x
    def test_first_is_not_automatically_approved(self):self.assertEqual(rm.record(self.db,self.observation())['decision'],'first_observation_needs_review')
    def test_unchanged_updates_observation_not_version(self):
        self.approved();rm.record(self.db,self.observation());a=rm.status('authority',self.db);self.assertEqual(a['route_version'],1);self.assertEqual(a['monitor_decision'],'unchanged');self.assertIsNotNone(a['usable_submission_url'])
    def test_change_needs_review_and_keeps_old_definition(self):
        self.approved();old=ROUTES['authority']['submission_url'];x=rm.record(self.db,self.observation(path_hash='new'))
        self.assertEqual(x['decision'],'route_changed_needs_review');self.assertIsNone(rm.status('authority',self.db)['usable_submission_url']);self.assertEqual(ROUTES['authority']['submission_url'],old)
    def test_procedure_changed_not_unchanged(self):
        self.approved();self.assertEqual(rm.record(self.db,self.observation(procedure_hash='new'))['decision'],'procedure_changed_needs_review')
    def test_failure_never_means_unchanged(self):
        self.approved();self.assertEqual(rm.record(self.db,self.observation(result='unreachable'))['decision'],'verification_failed')
        self.assertIsNone(rm.status('authority',self.db)['usable_submission_url'])
    def test_cannot_approve_failed_check(self):
        x=rm.record(self.db,self.observation(result='unreachable'))
        with self.assertRaises(ValueError):rm.approve(self.db,x['check_id'],'검토자')
    def test_cannot_approve_old_check(self):
        x=rm.record(self.db,self.observation());rm.record(self.db,self.observation())
        with self.assertRaises(ValueError):rm.approve(self.db,x['check_id'],'검토자')
    def test_expired_route_not_enabled(self):
        now=datetime(2026,12,1,tzinfo=timezone.utc);self.assertTrue(rm.status('authority',now=now)['stale']);self.assertIsNone(rm.status('authority',now=now)['usable_submission_url'])
    def test_maintenance_not_submission_available(self):
        self.assertIsNone(rm.status('lab',now=datetime(2026,11,15,tzinfo=timezone.utc))['usable_submission_url'])
    def test_token_not_stored(self):
        u=rm.clean_url('https://www.epeople.go.kr/nep/crtf/userLogn.npaid?tx=secret&token=sensitive#x');self.assertNotIn('secret',u);self.assertNotIn('?',u)
    def test_unapproved_hosts_rejected(self):
        for u in ['http://www.epeople.go.kr','https://localhost/','https://www.epeople.go.kr.evil.test/','https://u:p@www.epeople.go.kr/','https://www.epeople.go.kr:22/']:
            with self.subTest(u=u),self.assertRaises(ValueError):rm.validate_url(u)
    def test_config_change_invalidates_cache(self):
        self.approved();rm.record(self.db,self.observation())
        with patch.dict(ROUTES['authority'],submission_url='https://www.epeople.go.kr/new'):
            self.assertIsNone(rm.status('authority',self.db)['usable_submission_url'])
    def test_monitor_http200_maintenance(self):
        r=ROUTES['lab']
        def fake(url):
            body=('<a href="https://cs.ktr.or.kr">온라인 접수</a>' if url==r['source_url'] else '임시 점검' if url==r['submission_url'] else '의뢰(택배)접수 절차 접수 신청서 시험 성적서 신청서 양식 다운로드')
            return dict(url=url,status=200,html=body)
        self.assertEqual(rm.observe('lab',fake)['result'],'maintenance')
    def test_monitor_missing_link_not_success(self):
        def fake(url):return dict(url=url,status=200,html='의뢰(택배)접수 절차 접수 신청서 양식 다운로드')
        self.assertEqual(rm.observe('lab',fake)['result'],'route_needs_review')
    def test_monitor_missing_procedure_not_success(self):
        def fake(url):return dict(url=url,status=200,html='<a href="https://cs.ktr.or.kr">접수</a> 임시 점검')
        self.assertEqual(rm.observe('lab',fake)['result'],'procedure_needs_review')
    def test_error_does_not_leak_secret(self):
        def fake(url):raise ValueError('token=secret')
        self.assertNotIn('secret',json.dumps(rm.observe('lab',fake)))

if __name__=='__main__':unittest.main()
