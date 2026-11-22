const SCHEMA_VERSION = '1.0';
const MAX_BYTES = 16 * 1024 * 1024;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const unsafeKeys = new Set(['__proto__', 'prototype', 'constructor']);

function readJson(text) {
  if (typeof text !== 'string' || text.length > MAX_BYTES || new TextEncoder().encode(text).length > MAX_BYTES)
    throw Error('16 MiB 이하의 기초정보 JSON 또는 Markdown 파일을 선택하세요.');
  let body = text.trim();
  const fence = body.match(/^```(?:json)?[^\S\r\n]*\r?\n([\s\S]*?)\r?\n```$/i);
  if (fence) body = fence[1];
  try {
    return JSON.parse(body);
  } catch {
    // Leave every complete quoted JSON string (including escaped quotes) untouched.
    const normalized = body.replace(/"(?:\\[\s\S]|[^"\\])*"|\u00a0|\*\*(?:true|false|null)\*\*/g,
      token => token[0] === '"' ? token : token === '\u00a0' ? ' ' : token.slice(2, -2));
    try {
      return JSON.parse(normalized);
    } catch {
      throw Error('기초정보 JSON 문법을 확인하세요. Markdown 파일에는 JSON 한 개를 담아 주세요.');
    }
  }
}

function safeTree(value, depth = 0) {
  if (depth > 64) throw Error('기초정보 자료의 중첩 깊이를 확인하세요.');
  if (typeof value === 'number' && !Number.isFinite(value)) throw Error('기초정보의 숫자 범위를 확인하세요.');
  if (!value || typeof value !== 'object') return;
  if (Array.isArray(value)) {
    for (const item of value) safeTree(item, depth + 1);
  } else {
    const proto = Object.getPrototypeOf(value);
    if (proto !== Object.prototype && proto !== null) throw Error('기초정보 객체 형식을 확인하세요.');
    for (const key of Object.keys(value)) {
      if (unsafeKeys.has(key)) throw Error('기초정보에 지원하지 않는 속성 이름이 있습니다.');
      safeTree(value[key], depth + 1);
    }
  }
}

function validateOptionalFlags(value, label) {
  for (const key of ['synthetic', 'published']) {
    if (value[key] != null && typeof value[key] !== 'boolean')
      throw Error(`${label}의 ${key}은 true 또는 false로 표시하세요.`);
  }
  for (const key of ['sample_notice', 'export_kind']) {
    if (value[key] != null && typeof value[key] !== 'string')
      throw Error(`${label}의 ${key}은 문자열이어야 합니다.`);
  }
}

function makeSource(record, envelope) {
  if (!object(record) || !object(envelope)) throw Error('기초정보 사례 객체를 확인하세요.');
  const version = Object.hasOwn(record, 'schema_version') ? record.schema_version : envelope.schema_version;
  if (version !== SCHEMA_VERSION || ('schema_version' in envelope && envelope.schema_version !== SCHEMA_VERSION))
    throw Error('지원하는 기초정보 schema_version은 "1.0"입니다.');
  if (typeof record.case_id !== 'string' || !record.case_id.trim() || record.case_id !== record.case_id.trim())
    throw Error('각 기초정보 사례에는 고유한 case_id가 필요합니다.');
  if (!Number.isSafeInteger(record.case_version) || record.case_version < 1)
    throw Error(`${record.case_id}의 case_version을 1 이상의 정수로 표시하세요.`);
  if (typeof record.name !== 'string' || !record.name.trim())
    throw Error(`${record.case_id}에 폐기물 이름(name)이 필요합니다.`);
  validateOptionalFlags(record, record.case_id);
  validateOptionalFlags(envelope, '기초정보 파일');

  for (const key of ['quantity', 'classification', 'region', 'analysis', 'contamination', 'existing_declaration', 'foreign_matter', 'item_specific']) {
    if (record[key] != null && !object(record[key])) throw Error(`${record.case_id}의 ${key} 형식을 확인하세요.`);
  }
  for (const key of ['components', 'evidence']) {
    if (record[key] != null && (!Array.isArray(record[key]) || record[key].some(row => !object(row))))
      throw Error(`${record.case_id}의 ${key} 목록 형식을 확인하세요.`);
  }
  for (const key of ['missing', 'not_applicable']) {
    if (record[key] != null && (!Array.isArray(record[key]) || record[key].some(path => typeof path !== 'string')))
      throw Error(`${record.case_id}의 ${key} 항목 경로를 확인하세요.`);
  }
  const evidenceIds = new Set();
  for (const row of record.evidence || []) {
    validateOptionalFlags(row, '근거 자료');
    for (const key of ['evidence_id', 'source', 'field', 'checked_at']) {
      if (row[key] != null && typeof row[key] !== 'string') throw Error(`근거 자료의 ${key} 형식을 확인하세요.`);
    }
    if (row.evidence_id) {
      if (evidenceIds.has(row.evidence_id)) throw Error(`${record.case_id}의 evidence_id가 중복되었습니다.`);
      evidenceIds.add(row.evidence_id);
    }
  }

  const published = [envelope.published, record.published].filter(flag => typeof flag === 'boolean');
  return {
    schema_version: SCHEMA_VERSION,
    synthetic: envelope.synthetic === true || record.synthetic === true,
    published: published.length ? published.every(Boolean) : null,
    sample_notice: envelope.sample_notice || record.sample_notice || '',
    export_kind: envelope.export_kind || record.export_kind || '',
    record,
  };
}

export function parseIntakeFile(text) {
  const value = readJson(text);
  safeTree(value);
  const hasEnvelope = object(value) && Object.hasOwn(value, 'cases');
  const records = hasEnvelope ? value.cases : Array.isArray(value) ? value : [value];
  if (!Array.isArray(records) || !records.length) throw Error('cases에는 선택할 기초정보 사례가 하나 이상 필요합니다.');
  const sources = records.map(record => makeSource(record, hasEnvelope ? value : {}));
  const ids = new Set();
  for (const {record} of sources) {
    if (ids.has(record.case_id)) throw Error(`파일 안에서 case_id가 중복되었습니다: ${record.case_id}`);
    ids.add(record.case_id);
  }
  return sources;
}

export function restoreIntakeSource(raw) {
  if (raw == null) return null;
  safeTree(raw);
  if (!object(raw) || raw.schema_version !== SCHEMA_VERSION || !object(raw.record))
    throw Error('작업 파일의 기초정보 출처를 읽을 수 없습니다.');
  return makeSource(raw.record, raw);
}
