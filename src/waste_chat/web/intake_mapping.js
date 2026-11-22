// Integration schema 1.0 → fields with the same meaning. Every result is a proposal for one case.
const names = new Set(['intake', 'rfq', 'material', 'energy', 'disposal', 'contract', 'settlement']);
const intakeAndQuote = new Set(['intake', 'rfq']);
const approvedCodeStatuses = new Set(['verified', 'reviewed', 'confirmed', 'mock_reviewed']);
const periodPattern = /^\d{4}-(?:0[1-9]|1[0-2])$/;

const cleanString = value => typeof value === 'string' ? value.trim() : '';
const definedNumber = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const withIndices = path => path.replace(/\[(\d+)\]/g, '.$1');
const wildcardIndices = path => path.replace(/\[\d+\]/g, '[]');

function under(path, parent) {
  return path === parent || path.startsWith(parent + '.') || path.startsWith(parent + '[');
}

function unavailable(record, path) {
  return [record.missing, record.not_applicable].some(items => Array.isArray(items) && items.some(item => {
    const field = typeof item === 'string' ? item : cleanString(item?.field);
    return field && (under(path, field) || under(wildcardIndices(path), field));
  }));
}

function get(record, path) {
  if (unavailable(record, path)) return undefined;
  let value = record;
  for (const part of withIndices(path).split('.')) {
    if (value == null || typeof value !== 'object' || !Object.hasOwn(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

function evidenceFor(record, path, requiredId) {
  if (!Array.isArray(record.evidence) || unavailable(record, 'evidence')) return null;
  const matching = record.evidence.filter(e => {
    const field = cleanString(e?.field);
    return field && (under(path, field) || under(wildcardIndices(path), field))
      && (!requiredId || e.evidence_id === requiredId);
  });
  return matching.reduce((best, e) => !best || e.field.length > best.field.length ? e : best, null);
}

// Move the decimal point in the source number's decimal representation, avoiding 1.009 t → 1008.999… kg.
function decimal(value, shift = 0) {
  if (!definedNumber(value)) return '';
  const [coefficient, exponent = '0'] = String(value).toLowerCase().split('e');
  const [whole, fraction = ''] = coefficient.split('.');
  const digits = whole + fraction;
  const point = whole.length + Number(exponent) + shift;
  const integer = (point > 0 ? digits.slice(0, point).padEnd(point, '0') : '0').replace(/^0+(?=\d)/, '');
  const rest = (point < 0 ? '0'.repeat(-point) + digits : digits.slice(point)).replace(/0+$/, '');
  return rest ? integer + '.' + rest : integer;
}

function monthlyOffers(record, emit) {
  const weights = get(record, 'quantity.monthly_ton');
  const periods = get(record, 'quantity.monthly_periods');
  const quantityUnit = cleanString(get(record, 'quantity.unit'));
  if (!Array.isArray(weights) || !Array.isArray(periods) || weights.length !== periods.length
      || (quantityUnit && !['ton', 'tonne', 't', '톤'].includes(quantityUnit.toLowerCase()))
      || !['confirmed','verified','measured','recorded'].includes(get(record, 'quantity.status'))) return;
  const seen = new Set(), duplicates = new Set();
  for (const period of periods) {
    if (seen.has(period)) duplicates.add(period);
    seen.add(period);
  }
  periods.forEach((period, index) => {
    const weightPath = `quantity.monthly_ton[${index}]`;
    const datePath = `quantity.monthly_periods[${index}]`;
    if (!periodPattern.test(cleanString(get(record,datePath))) || duplicates.has(period)) return;
    const weight = get(record, weightPath);
    const kg = decimal(weight, 3);
    if (kg) emit(weightPath, kg, {note:`${period} · ${decimal(weight)} t → ${kg} kg/월`, tag:period});
  });
}

function classificationOffer(record, emit) {
  const path = 'classification.selected_code';
  const code = cleanString(get(record, path));
  const status = get(record, 'classification.status');
  const evidenceId = cleanString(get(record, 'classification.evidence_id'));
  if (!/^\d{2}-\d{2}-\d{2}$/.test(code) || !approvedCodeStatuses.has(status)
      || !evidenceId || !evidenceFor(record, path, evidenceId)) return;
  const reported = cleanString(get(record, 'classification.reported_code'));
  const note = (status === 'mock_reviewed' ? '가상 검토값' : '입력 자료상 검토값')
    + (reported && reported !== code ? ' · 신고 기재값 ' + reported : '');
  emit(path, code, {note, evidenceId, mock:status === 'mock_reviewed'});
}

function componentsText(record) {
  const parts = get(record, 'components');
  if (!Array.isArray(parts) || !parts.length) return '';
  const lines = [];
  for (const [i] of parts.entries()) {
    const prefix = `components[${i}]`;
    const material = cleanString(get(record, prefix + '.material'));
    if (!material) return '';
    const ratio = get(record, prefix + '.share_pct');
    const confirmed = ['confirmed', 'verified'].includes(get(record, prefix + '.status'));
    lines.push(confirmed && definedNumber(ratio) && ratio <= 100 ? material + ' ' + decimal(ratio) + '%' : material);
  }
  return lines.join(', ');
}

function contaminationText(record) {
  const level = cleanString(get(record, 'contamination.level'));
  const values = get(record, 'contamination.substances');
  const substances = Array.isArray(values) ? values.map((_,i) => cleanString(get(record, `contamination.substances[${i}]`))).filter(Boolean) : [];
  return [level, ...substances].filter(Boolean).join(' · ');
}

function testSummary(record) {
  const sample = cleanString(get(record, 'analysis.sample_id'));
  if (!sample) return '';
  const parts = [];
  const heating = decimal(get(record, 'analysis.heating_value_kcal'));
  const unit = cleanString(get(record, 'analysis.heating_value_unit'));
  if (heating && unit) parts.push('발열량 ' + heating + ' ' + unit);
  for (const [key,label] of [['moisture_pct','수분'],['chlorine_pct','염소']]) {
    const value = get(record, 'analysis.' + key);
    if (definedNumber(value) && value <= 100) parts.push(label + ' ' + decimal(value) + '%');
  }
  return parts.length ? '시료 ' + sample + ' · ' + parts.join(', ') : '';
}

export function intakeOffers(source, doc, key) {
  const record = source?.record;
  if (!record || !doc || typeof key !== 'string') return [];
  const field = doc.fields?.find(f => f.key === key) || doc.original_cells?.find(f => f.key === key);
  if (!field) return [];
  const offers = [];
  function emit(path, value, {note = '', tag = '', mock = false, evidenceId = '', paired} = {}) {
    if (get(record, path) == null || typeof value !== 'string' || !value.trim() || value.length > (doc.fields?.length ? 5000 : 500)) return;
    const e = evidenceFor(record, path, evidenceId);
    const demo = source.synthetic === true || record.synthetic === true || e?.synthetic === true || mock;
    const prefix = demo ? '예시·가상 기초정보' : '기초정보';
    const identity = record.case_id + ' v' + record.case_version;
    const evidence = e ? [e.source, e.evidence_id, e.checked_at].map(cleanString).filter(Boolean).join(' · ') : '별도 근거 기록 없음';
    const label = [prefix, identity, note, path, evidence].filter(Boolean).join(' | ');
    const origin = 'intake:' + JSON.stringify([record.case_id, record.case_version, path, tag]);
    offers.push({id: JSON.stringify([origin, doc.id, key, value]), origin, value, note, source:label, ...(paired ? {paired} : {})});
  }
  const text = path => cleanString(get(record, path));
  const id = doc.id;
  if (key === 'waste_name' && names.has(id)) {
    emit('name', text('name'), {note:'현장에서 부르는 폐기물명'});
  } else if ((key === 'generation_process' && ['intake','rfq','material'].includes(id)) || (id === 'hazard' && key === 'process')) {
    emit('process_description', text('process_description'), {note:'폐기물이 발생하는 공정'});
  } else if (intakeAndQuote.has(id) && ['quantity_value','quantity_unit'].includes(key)) {
    const value = decimal(get(record, 'quantity.current_lot.value'));
    const unit = text('quantity.current_lot.unit');
    if (value && unit) {
      const amount = key === 'quantity_value';
      emit('quantity.current_lot.' + (amount ? 'value' : 'unit'), amount ? value : unit, {
        note:'이번 로트 ' + value + ' ' + unit,
        paired:{key:amount ? 'quantity_unit' : 'quantity_value', value:amount ? unit : value}
      });
    }
  } else if ((['intake','rfq','contract'].includes(id) && key === 'waste_state') || (id === 'analysis' && key === 'row1_state')) {
    const form = text('material_form');
    const normalized = id === 'intake' ? ({'액상':'액체','고상':'고체'}[form] ?? form) : form;
    emit('material_form', normalized, {note: normalized === form ? '현장 기록 성상' : `현장 성상 ${form} → ${normalized}`});
  } else if (id === 'intake' && key === 'mixture_state') {
    const mixed = get(record,'mixed_with_other');
    if (mixed === true) emit('mixed_with_other','여러 물질 혼합됨');
    if (mixed === false && get(record,'components')?.length === 1 && get(record,'components[0].share_pct') === 100
        && get(record,'components[0].status') === 'confirmed') emit('mixed_with_other','단일 물질',{note:'혼합 없음 · 기록된 성분 1종 100%'});
  } else if (intakeAndQuote.has(id) && key === 'contamination_detail') {
    emit('contamination',contaminationText(record));
  } else if (id === 'intake' && key === 'additional_notes') {
    emit('storage_state',text('storage_state'),{note:'보관 상태'});
  } else if (id === 'material' && key === 'material_resin') {
    emit('components',componentsText(record));
  } else if (id === 'energy' && key === 'energy_test') {
    emit('analysis',testSummary(record),{note:'참고용 시료 기록'});
  } else if (id === 'analysis' && key === 'row1_quantity') {
    monthlyOffers(record, emit);
  } else if ((['form5_b','form5_c','form6'].includes(id) && key === 'waste_classification') ||
             (id === 'analysis' && key === 'row1_code') ||
             (id === 'dplan' && key === 'designated_waste_code' && text('designated_waste_status') === 'designated')) {
    classificationOffer(record, emit);
  }
  return offers;
}
