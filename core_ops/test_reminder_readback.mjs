#!/usr/bin/env node
// Isolated fixture test for the Dell reminder gate. Does not start the API or send.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const source = process.argv[2];
if (!source || process.argv.length > 4) throw Error('usage: node test_reminder_readback.mjs <verified-runtime.mjs> [prepared-runtime.mjs]');
const here = path.dirname(fileURLToPath(import.meta.url));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'jp-reminder-readback-'));
const patched = path.join(temp, 'runtime.mjs');
try {
  if (!process.argv[3]) {
    const install = spawnSync('python3', [path.join(here, 'install_reminder_readback.py'), source, patched], { encoding: 'utf8' });
    if (install.status !== 0) throw Error(`patch_failed:${install.status}`);
  }
  const runtime = fs.readFileSync(process.argv[3] || patched, 'utf8');
  const start = runtime.indexOf('function reminderReadiness(candidate,state){');
  const end = runtime.indexOf('\nfunction delaySupportConfig()', start);
  if (start < 0 || end < 0) throw Error('reminder_gate_not_found');
  const context = {
    clarificationFor: () => null,
    supportCaseFor: (state, id) => state.support?.has(id) ? { appointment_id: id } : null,
    visitFeedback: (candidate, state) => state.holds?.has(candidate.appointment_id) ? { reminder_hold: true } : null,
    responseRecord: (state, id) => state.responses?.get(id) || null,
    sameDestination: (a, b) => String(a || '').replace(/\D/g, '') === String(b || '').replace(/\D/g, ''),
    contactBlocked: (state, destination) => state.blocked?.has(destination) || false,
    sameVisit: (a, b) => !!a && !!b && a.group_sha256 === b.group_sha256,
    dispatchMembers: ({ visit_group }) => visit_group?._members || [],
  };
  vm.createContext(context);
  vm.runInContext(`${runtime.slice(start, end)}\nthis.reminderReadiness=reminderReadiness;`, context, { timeout: 1000 });
  const readiness = context.reminderReadiness;

  const candidate = (id, overrides = {}) => ({
    appointment_id: String(id), client_code: `C${id}`, professional_code: `P${id}`,
    service_code: `S${id}`, appointment_date_iso: '2026-11-20', appointment_time: id === '2' ? '11:00' : '10:00',
    destination_e164: '+5565992000000', source_active: true, ...overrides,
  });
  const proof = (c, overrides = {}) => ({
    appointment_id: c.appointment_id, client_code: c.client_code, professional_code: c.professional_code,
    service_code: c.service_code, start: `${c.appointment_date_iso}T${c.appointment_time}:00-04:00`,
    confirmed: true, status: '1', checked_at: '2026-11-12T10:00:00-04:00', ...overrides,
  });
  const record = (c, message = 'out-1', overrides = {}) => ({
    appointment_id: c.appointment_id, intent: 'confirmed', outbound_message_id: message,
    destination_e164: c.destination_e164, official_update: { status: 'confirmed', readback: proof(c) }, ...overrides,
  });
  const stateFor = (rows, records) => ({
    individual_dispatches: rows.map(([c, message = 'out-1', destination = c.destination_e164]) => ({ appointment_id: c.appointment_id, message_id: message, destination_e164: destination })),
    reminders: [], responses: new Map(records.map(r => [r.appointment_id, r])), support: new Set(), holds: new Set(), blocked: new Set(),
  });
  const expectBlocked = (label, c, state) => assert.equal(readiness(c, state).available, false, label);

  { const c = candidate('1'); const r = record(c); r.official_update.readback.confirmed = false; expectBlocked('proof.confirmed=false', c, stateFor([[c]], [r])); }
  { const c = candidate('1'); const r = record(c); r.official_update.readback.status = 'inactive'; expectBlocked('inactive proof status', c, stateFor([[c]], [r])); }
  for (const [field, value] of [['appointment_id', '999'], ['client_code', 'other'], ['professional_code', 'other'], ['service_code', 'other'], ['start', '2026-11-20T09:00:00-04:00']]) {
    const c = candidate('1'); const r = record(c); r.official_update.readback[field] = value;
    expectBlocked(`proof ${field} swapped`, c, stateFor([[c]], [r]));
  }
  { const c = candidate('1'); const r = record(c, 'out-1', { destination_e164: '+5565992999999' }); expectBlocked('record destination swapped', c, stateFor([[c]], [r])); }
  { const c = candidate('1'); expectBlocked('dispatch destination swapped', c, stateFor([[c, 'out-1', '+5565992999999']], [record(c)])); }
  { const c = candidate('1'); const s = stateFor([[c]], [record(c)]); s.support.add(c.appointment_id); expectBlocked('human pause', c, s); }
  { const c1 = candidate('1'), c2 = candidate('2'); const group = { group_sha256: 'g', appointment_ids: [c1.appointment_id, c2.appointment_id], _members: [c1, c2] };
    const r1 = record(c1); r1.visit_group = group; const r2 = record(c2); r2.visit_group = group; r2.official_update.readback.confirmed = false;
    expectBlocked('second service pending', c1, stateFor([[c1], [c2]], [r1, r2])); }
  { const c1 = candidate('1'), c2 = candidate('2'); const group = { group_sha256: 'g', appointment_ids: [c1.appointment_id, c2.appointment_id], _members: [c1, c2] };
    const r1 = record(c1); r1.visit_group = group; const r2 = record(c2); r2.visit_group = group;
    assert.equal(readiness(c1, stateFor([[c1], [c2]], [r1, r2])).available, true, 'complete group is ready'); }
  { const c = candidate('1'); assert.equal(readiness(c, stateFor([[c]], [record(c)])).available, true, 'single appointment is ready'); }
  { const c = candidate('1'); const state = stateFor([[c]], [record(c)]); const before = JSON.stringify({ individual_dispatches: state.individual_dispatches, reminders: state.reminders, responses: [...state.responses] }); readiness(c, state); assert.equal(JSON.stringify({ individual_dispatches: state.individual_dispatches, reminders: state.reminders, responses: [...state.responses] }), before, 'readback gate writes nothing'); }
  console.log('reminderReadiness readback fixtures: ok');
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
