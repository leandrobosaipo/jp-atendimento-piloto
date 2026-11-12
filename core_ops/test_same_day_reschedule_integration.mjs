import assert from 'node:assert/strict';
import test from 'node:test';
import {wrapReplyPlanning, wrapSameDayReschedule} from './same_day_reschedule_integration.mjs';

const at = Date.parse('2030-11-14T12:00:00-04:00');
const visit = {appointment_id: '901', client_code: '51', destination_e164: '+5565999999999', professional_code: '16897547', professional: 'Pedro', service_code: '845365', service: 'Manutenção', appointment_date_iso: '2030-11-15', appointment_time: '10:00'};
function fixture(text) {
  let state = {version: 1, jp_reply_tasks: [{task_id: 'task-1', source_message_id: 'in-1', source_text: text, destination_e164: visit.destination_e164, client_code: '51', kind: 'reschedule', status: 'prepared', context: {appointments: [visit]}}]};
  let scopes = 0, reads = 0, slots = 0, human = 0;
  const deps = {load: () => structuredClone(state), save: s => { state = structuredClone(s); }, now: () => at,
    onHuman: () => { human++; },
    readConfirmation: async () => { reads++; return {appointment_id: '901', client_code: '51', professional_code: '16897547', service_code: '845365', status: '1', start: '2030-11-15T10:00:00-04:00', end: '2030-11-15T10:45:00-04:00'}; },
    slots: async () => { slots++; return [{date: '2030-11-15', time: '15:30', professional_code: '16897547', service_code: '845365', duration_minutes: 45, policy_revision: 'fixture'}]; }};
  const native = {plan: async () => { const t = state.jp_reply_tasks[0]; t.status = 'planned'; t.plan = {kind: 'offer'}; return {ok: true, task_id: t.task_id, token: 'token', action: 'offer', executable: true}; }};
  const engine = wrapReplyPlanning(wrapSameDayReschedule(native, deps), deps, async (task, load, fn) => { scopes++; assert.equal(task.task_id, 'task-1'); return fn(); });
  return {engine, state: () => state, counts: () => ({scopes, reads, slots, human})};
}

test('scoped planner transforms only exact same-day request into existing move plan', async () => {
  const f = fixture('Pedro, pode mudar para 15:30?');
  const out = await f.engine.plan({task_id: 'task-1'});
  assert.equal(out.action, 'move');
  assert.deepEqual(f.counts(), {scopes: 1, reads: 1, slots: 1, human: 0});
  const plan = f.state().jp_reply_tasks[0].plan;
  assert.equal(plan.kind, 'move'); assert.equal(plan.move_appointment_id, '901'); assert.equal(plan.option.time, '15:30');
  assert.equal(plan.autonomous_same_day, true);
});

test('date change is triaged without availability read or sending', async () => {
  const f = fixture('Pedro, pode mudar amanhã para 15:30?');
  const out = await f.engine.plan({task_id: 'task-1'});
  assert.deepEqual({action: out.action, reason: out.reason}, {action: 'human', reason: 'date_change_requires_human'});
  assert.deepEqual(f.counts(), {scopes: 1, reads: 0, slots: 0, human: 1});
  assert.equal(f.state().jp_reply_tasks[0].plan.kind, 'human');
});

test('unavailable official slot is triaged and never creates a move plan', async () => {
  const f = fixture('Pedro, pode mudar para 15:30?');
  // The fixture remains a real engine wrapper; only the official availability is empty.
  const original = f.engine.plan;
  // Rebuild deliberately with no slots to make the dependency explicit.
  let state = {version: 1, jp_reply_tasks: [{task_id: 't', source_message_id: 'i', source_text: 'Pedro, pode mudar para 15:30?', destination_e164: visit.destination_e164, client_code: '51', kind: 'reschedule', status: 'prepared', context: {appointments: [visit]}}]};
  const deps = {load: () => structuredClone(state), save: s => { state = structuredClone(s); }, now: () => at, onHuman: () => {},
    readConfirmation: async () => ({appointment_id: '901', client_code: '51', professional_code: '16897547', service_code: '845365', status: '1', start: '2030-11-15T10:00:00-04:00', end: '2030-11-15T10:45:00-04:00'}), slots: async () => []};
  const native = {plan: async () => { state.jp_reply_tasks[0].status = 'planned'; state.jp_reply_tasks[0].plan = {kind: 'offer'}; return {ok: true, task_id: 't', action: 'offer'}; }};
  const e = wrapReplyPlanning(wrapSameDayReschedule(native, deps), deps, async (_t, _l, fn) => fn());
  const out = await e.plan({task_id: 't'});
  assert.equal(out.reason, 'official_slot_not_unique'); assert.equal(state.jp_reply_tasks[0].plan.kind, 'human');
  assert.equal(typeof original, 'function');
});
