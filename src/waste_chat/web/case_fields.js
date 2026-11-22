// Accepted values belong to a single document. Only other documents' committed
// fields with the same catalog key can become reuse suggestions.
const record = () => Object.create(null);
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const text = (value, max = 5000) => typeof value === 'string' ? value.slice(0, max) : '';

export class CaseFields {
  constructor() {
    this.entries = record();
    this.legacy = record();
  }
  at(id, key) { return this.entries[id]?.[key]; }
  edit(id, key) {
    const doc = this.entries[id] ??= record();
    return doc[key] ??= {value:'', source:'', deferred:false, skipped:record()};
  }
  value(id, key) { return this.at(id,key)?.value || ''; }
  set(id, key, value, source, intakeTrace = null) {
    const item = this.edit(id,key);
    item.value = value;
    item.source = value ? source : '';
    if (value && intakeTrace) item.intakeTrace = intakeTrace;
    else delete item.intakeTrace;
    if (value.trim()) item.deferred = false;
  }
  pending(id, key) { return !!this.at(id,key)?.deferred; }
  setPending(id, key, flag) { this.edit(id,key).deferred = flag; }
  offers(docs, id, key) {
    if (this.value(id,key).trim()) return [];
    const skipped = this.at(id,key)?.skipped;
    const matches = [];
    function offer(origin, value, source, intakeTrace) {
      if (!value?.trim() || skipped?.[origin] === value) return;
      matches.push({id:JSON.stringify([origin,key,value]), origin, value, source, intakeTrace, paired:intakeTrace?.paired});
    }
    for (const [origin, rows] of Object.entries(this.entries)) {
      if (origin === id || !rows[key]?.value) continue;
      const sourceDoc = docs.get(origin);
      const field = sourceDoc?.fields.find(f => f.key === key);
      if (field) {
        const trace = rows[key].intakeTrace;
        const label = sourceDoc.title + ' · ' + field.label + (trace ? ' ← ' + trace.source : '');
        offer(origin,rows[key].value,label,trace);
      }
    }
    if (this.legacy[key] && !matches.some(o => o.value === this.legacy[key])) {
      offer('_legacy',this.legacy[key],'이전 작업 파일 · 공통 입력값');
    }
    return matches;
  }
  dismiss(id, key, offer) { this.edit(id,key).skipped[offer.origin] = offer.value; }
  exportState() { return {entries:this.entries, legacy:this.legacy}; }

  static fromFile(v, docs) {
    const state = new CaseFields();
    const owners = new Map();
    for (const d of docs.values()) for (const f of d.fields) {
      if (!owners.has(f.key)) owners.set(f.key,[]);
      owners.get(f.key).push(d.id);
    }
    if (v.version === 1) {
      if (!object(v.values)) throw Error('기존 작업 파일의 입력값을 읽을 수 없습니다.');
      for (const [key, raw] of Object.entries(v.values)) {
        const ids = owners.get(key), value = text(raw);
        if (!ids || !value.trim()) continue;
        // V1 stored no reliable per-field history. Restore the visible snapshot
        // or its unique owner; unassigned shared values require a fresh approval.
        const id = ids.includes(v.doc) ? v.doc : ids.length === 1 ? ids[0] : null;
        if (id) state.set(id,key,value,'이전 작업 파일');
        else state.legacy[key] = value;
      }
      return state;
    }
    const data = v.fieldState;
    if (v.version !== 2 || !object(data?.entries)) throw Error('작업 파일의 서식별 입력값을 읽을 수 없습니다.');
    for (const [key, raw] of Object.entries(object(data.legacy) ? data.legacy : {})) {
      if (owners.has(key) && text(raw).trim()) state.legacy[key] = text(raw);
    }
    for (const [id, rawFields] of Object.entries(data.entries)) {
      const d = docs.get(id);
      if (!d || !object(rawFields)) continue;
      for (const f of [...d.fields,...(d.original_cells || [])]) {
        const raw = rawFields[f.key];
        if (!object(raw)) continue;
        const value = text(raw.value);
        const candidate = raw.intakeTrace;
        let trace = null;
        if (object(candidate) && typeof candidate.source === 'string' && typeof candidate.origin === 'string' && candidate.origin.startsWith('intake:')) {
          trace = {source:text(candidate.source), origin:text(candidate.origin)};
          const pair = candidate.paired;
          if (object(pair) && d.fields.some(field=>field.key===pair.key) && typeof pair.value === 'string') trace.paired = {key:pair.key, value:text(pair.value)};
        }
        state.set(id,f.key,d.fields.includes(f) ? value : '',text(raw.source),trace);
        const entry = state.at(id,f.key);
        entry.deferred = !value.trim() && raw.deferred === true;
        for (const [origin, val] of Object.entries(object(raw.skipped) ? raw.skipped : {})) {
          if ((docs.has(origin) || origin === '_legacy' || origin.startsWith('intake:')) && typeof val === 'string') entry.skipped[origin] = text(val);
        }
      }
    }
    return state;
  }
}
