-- 읽기 전용 조회 예시. 개인정보 포함 DB의 결과를 외부 공유하지 마세요.
-- case_candidates는 DOCX 원문에서 나온 초기 후보이므로 새 답변으로 덮어쓰지 않습니다.
-- 현재 검토 후보는 phase2.py status 또는 db_intake.py export로 확인합니다.

-- 질문의 대상/필드/맥락과 사유
SELECT request_id, case_id, field, context, priority, asked_reason, asked_at
FROM phase2_requests ORDER BY case_id, priority, asked_at;

-- 최초 답변부터 정정까지: 원문과 typed 열, 미확인 상태
SELECT r.case_id, r.field, a.request_id, a.revision, a.raw_value, a.status,
       a.normalized_text, a.normalized_bool,
       a.quantity_value, a.quantity_unit, a.quantity_dimension, a.conversion_rule,
       a.reason, a.answered_at
FROM phase2_answers a JOIN phase2_requests r USING (request_id)
ORDER BY r.case_id, r.field, a.request_id, a.revision;

-- 질문별 마지막 답변: unavailable도 삭제하지 않음
SELECT r.case_id, r.field, a.*
FROM phase2_requests r JOIN phase2_answers a USING (request_id)
WHERE a.revision=(SELECT MAX(b.revision) FROM phase2_answers b WHERE b.request_id=a.request_id)
ORDER BY r.case_id, r.field;

-- 추가 현장 후보/답변 연결. 과거 정정 후보도 남아 있음
SELECT * FROM phase2_sites ORDER BY case_id, request_id, revision;

-- 증빙 질문은 별도 테이블에서 보존됨
SELECT r.request_id, r.case_id, r.topic, r.purpose, r.blocking_scope,
       a.revision, a.status, a.answer_text, a.file_id
FROM guidance_requests r LEFT JOIN guidance_answers a USING (request_id)
ORDER BY r.case_id, r.request_id, a.revision;
