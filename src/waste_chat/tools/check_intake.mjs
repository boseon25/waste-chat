// Run from any directory with Node 22+ and the project's Python dependencies installed.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {parseIntakeFile, restoreIntakeSource} from '../web/intake_source.js';
import {intakeOffers} from '../web/intake_mapping.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const raw = readFileSync(new URL('../cases/examples/part2-intake-sample.json', import.meta.url), 'utf8');
const [film, solvent] = parseIntakeFile(raw);
const catalog = JSON.parse(execFileSync(process.env.PYTHON || 'python3', [
  '-c', 'import json; from services.catalog import DOCS; print(json.dumps(DOCS))',
], {cwd:root, encoding:'utf8'}));
const offers = (src,doc,key) => intakeOffers(src, catalog[doc], key);
const values = (src,doc,key) => offers(src,doc,key).map(row => row.value);
assert.deepEqual(restoreIntakeSource(JSON.parse(JSON.stringify(solvent))), solvent);
assert.equal(film.record.evidence.length, 18);
assert.equal(solvent.record.evidence.length, 18);
assert.deepEqual(values(film, 'rfq', 'waste_name'), ['제품 포장용 PE 필름 자투리']);
assert.deepEqual(values(solvent, 'rfq', 'quantity_value'), ['100']);
assert.deepEqual(values(solvent, 'rfq', 'quantity_unit'), ['L']);
assert.deepEqual(values(film, 'analysis', 'row1_quantity'), ['1150','1270','1210']);
const months = offers(solvent, 'analysis', 'row1_quantity');
assert.deepEqual(months.map(row => row.value), ['320','280','310']);
assert(months[0].source.includes('2026-08') && months[0].source.includes('DEMO-2-MONTHLY'));
assert(months[0].source.includes('가상'));
for (const [doc,key] of [['contract','contract_quantity'],['settlement','measured_tons'],
                           ['analysis','row2_quantity'],['analysis','row1_concentration'],['form6','waste_legal_name']]) {
  assert.deepEqual(offers(solvent, doc, key), []);
}
const incomplete = structuredClone(solvent);
incomplete.record.missing = ['quantity.current_lot.unit','quantity.monthly_ton[1]'];
assert.deepEqual(offers(incomplete, 'rfq', 'quantity_value'), []);
assert.deepEqual(values(incomplete, 'analysis', 'row1_quantity'), ['320','310']);
console.log('기초정보 두 건의 값·단위·기간·출처와 문서별 제외 항목을 확인했습니다.');
