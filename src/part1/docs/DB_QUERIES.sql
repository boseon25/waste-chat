-- SQLite 읽기 전용 확인용. 원본/후보를 UPDATE하면 대조 검사에서 실패합니다.
-- 제출과 저장된 원본 크기
SELECT submission_id, original_name, sha256, parser_version, synthetic,
       length(original_docx) AS original_bytes FROM submissions;

-- 업체
SELECT submission_id, name, business_number, contact_name, contact_phone FROM companies;

-- 폐기물별 수량과 연결된 현장
SELECT c.submission_id, c.table_index, c.name, c.quantity_raw,
       c.quantity_value, c.quantity_unit, c.quantity_dimension,
       c.hazard_raw, c.hazard_declaration, c.site_inherited,
       s.address, c.warnings
FROM case_candidates c LEFT JOIN sites s ON s.site_id = c.site_id
ORDER BY c.submission_id, c.table_index;

-- 원문 체크박스 상태: 1=선택, 0=미선택, NULL=상태 미확인
SELECT submission_id, ordinal, tag, raw_value, checked, table_index, row_index, cell_index
FROM controls WHERE is_checkbox = 1 ORDER BY submission_id, ordinal;

-- 작성 기준일과 자료 안내 원문
SELECT submission_id, tag, raw_value, paragraph_index
FROM controls WHERE tag IN ('source_form_date', 'supporting_documents_note');

-- DB 무결성 / 외래키 검사
PRAGMA integrity_check;
PRAGMA foreign_key_check;
