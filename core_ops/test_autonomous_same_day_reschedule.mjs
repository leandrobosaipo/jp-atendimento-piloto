import assert from 'node:assert/strict';
import test from 'node:test';
import {executeSameDayMove, selectAutonomousSameDayMove} from './autonomous_same_day_reschedule.mjs';

const pedro = '16897547';
const visit = {appointment_id: '901', client_code: '51', destination_e164: '+5565999999999', professional_code: pedro, service_code: '845365', appointment_date_iso: '2030-11-15', appointment_time: '10:00', duration_minutes: 45};
const slot = {...visit, date: '2030-11-15', time: '15:30'};
const at = Date.parse('2030-11-14T12:00:00-04:00');
const official = slots => ({source: 'official_task_reader', appointment_id: '901', date: '2030-11-15', professional_code: pedro, service_code: '845365', read_at: new Date(at).toISOString(), slots});
const decide = (message, change={}) => selectAutonomousSameDayMove({message, visits: change.visits === undefined ? [visit] : change.visits, availability: change.availability === undefined ? official([slot]) : change.availability, now: at});

test('moves only a clear same-day request with same Alysson/Pedro, service and duration', () => {
  const result = decide('Pedro, pode mudar para 15:30?');
  assert.equal(result.action, 'move');
  assert.equal(result.slot, slot);
});

for (const [name, message, change, reason] of [
  ['two visits', 'Pedro, mudar para 15:30', {visits: [visit, {...visit, appointment_id: '902'}]}, 'individual_visit_unverified'],
  ['date named', 'Pedro, mudar amanha para 15:30', {}, 'date_change_requires_human'],
  ['no time', 'Pedro, preciso remarcar', {}, 'target_time_not_clear'],
  ['two times', 'Pedro, 14:00 ou 15:30?', {}, 'target_time_not_clear'],
  ['other professional', 'Alysson, mudar para 15:30', {}, 'professional_changed_or_unclear'],
  ['same time', 'Pedro, mudar para 10:00', {}, 'target_equals_current_time'],
  ['service differs', 'Pedro, mudar para 15:30', {availability: official([{...slot, service_code: '9'}])}, 'official_slot_not_unique'],
  ['duration differs', 'Pedro, mudar para 15:30', {availability: official([{...slot, duration_minutes: 30}])}, 'official_slot_not_unique'],
  ['missing availability', 'Pedro, mudar para 15:30', {availability: null}, 'official_availability_missing'],
  ['stale availability', 'Pedro, mudar para 15:30', {availability: {...official([slot]), read_at: new Date(at - 300001).toISOString()}}, 'official_availability_missing'],
]) test(name, () => assert.deepEqual(decide(message, change), {action: 'human_review', reason}));

test('execution calls the one existing guarded adapter once and never sends', async () => {
  const decision = decide('Pedro, pode mudar para 15:30?');
  let checks = 0, moves = 0, marks = 0;
  const outcome = await executeSameDayMove(decision, {
    assertOrigin: async scope => { checks++; assert.equal(scope.operation, 'move'); assert.equal(scope.visit.appointment_id, '901'); },
    moveOfficialAppointment: async (candidate, target, config, mark, fetchImpl) => { moves++; assert.equal(candidate, visit); assert.equal(target, slot); assert.equal(config, 'same-session'); assert.equal(fetchImpl, undefined); await mark({appointment_id: '901'}); return {status: 'saved', target}; },
    agendaConfig: 'same-session', markAttempt: async () => { marks++; },
  });
  assert.equal(outcome.status, 'saved');
  assert.equal(checks, 1); assert.equal(moves, 1); assert.equal(marks, 1);
});

test('human decision never opens adapter nor creates a new session', async () => {
  const outcome = await executeSameDayMove(decide('quero remarcar'), {
    assertOrigin: async () => { throw Error('must_not_run'); }, moveOfficialAppointment: async () => { throw Error('must_not_run'); }, markAttempt: async () => {},
  });
  assert.deepEqual(outcome, {status: 'held', reason: 'target_time_not_clear'});
});
