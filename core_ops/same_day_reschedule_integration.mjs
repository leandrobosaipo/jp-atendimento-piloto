/* Bind the conservative same-day selector to the existing reply engine.
 *
 * This wrapper is deliberately below the existing planner: the normal planner
 * first performs its blocked/contact/team/uncertain checks. Only its harmless
 * offer/question result may be replaced. The eventual execute still goes
 * through wrapEngine -> effectFetch -> nativeScope -> guardedPost.
 */
import {selectAutonomousSameDayMove} from './autonomous_same_day_reschedule.mjs';

const future = (visit, now) => Date.parse(`${visit.appointment_date_iso}T${visit.appointment_time}:00-04:00`) > now;
const duration = proof => {
  const ms = Date.parse(proof?.end) - Date.parse(proof?.start);
  return Number.isFinite(ms) && ms > 0 && ms % 60000 === 0 ? ms / 60000 : null;
};

function update(deps, id, change) {
  const state = deps.load();
  const task = (state.jp_reply_tasks || []).filter(t => t.task_id === id);
  if (task.length !== 1) throw Error('same_day_task_changed');
  change(task[0], state);
  state.version = (state.version || 0) + 1;
  deps.save(state);
  return task[0];
}

function human(deps, task, reason) {
  const held = update(deps, task.task_id, row => {
    row.plan = {kind: 'human', reason, autonomous_same_day: true};
    row.status = 'human'; row.reason = reason; row.planned_at = new Date().toISOString();
  });
  deps.onHuman?.({task_id: held.task_id, source_message_id: held.source_message_id,
    destination_e164: held.destination_e164, client_code: held.client_code, source_text: held.source_text, reason});
  return {ok: true, task_id: held.task_id, action: 'human', reason, executable: false};
}

/**
 * Scope every planning read to the already admitted reply task. This makes the
 * existing `scopedFetch` reader enforce workflow/node/epoch/admission checks;
 * it does not create an AppBarber session.
 */
export function wrapReplyPlanning(engine, deps, withTask) {
  const original = engine.plan;
  return {...engine, plan: async input => {
    const task = (deps.load().jp_reply_tasks || []).find(t => t.task_id === input?.task_id);
    if (!task) return original(input);
    return withTask(task, deps.load, () => original(input));
  }};
}

/**
 * Replace an otherwise safe offer by one immediate move only for the narrow
 * same-day contract. All ambiguous cases are retained as private human work.
 */
export function wrapSameDayReschedule(engine, deps) {
  const original = engine.plan;
  const now = () => Number(deps.now ? deps.now() : Date.now());
  return {...engine, plan: async input => {
    const baseline = await original(input);
    if (!baseline?.ok || !['offer', 'question'].includes(baseline.action)) return baseline;
    const task = (deps.load().jp_reply_tasks || []).find(t => t.task_id === input?.task_id);
    if (!task || task.status !== 'planned' || task.kind !== 'reschedule' || task.plan?.autonomous_same_day) return baseline;
    const visits = (task.context?.appointments || []).filter(v => future(v, now()));
    // Static ambiguity is triaged without another provider read.
    if (visits.length !== 1) return human(deps, task, 'individual_visit_unverified');
    const appointment = visits[0];
    // The first pass intentionally uses a placeholder duration only to decide
    // textual ambiguity before touching the provider. The authoritative pass
    // below replaces it with the duration from the official readback.
    const provisional = selectAutonomousSameDayMove({message: task.source_text,
      visits: [{...appointment, duration_minutes: 1}], availability: null, now: now()});
    if (provisional.reason && provisional.reason !== 'official_availability_missing') return human(deps, task, provisional.reason);
    let before, options;
    try {
      before = await deps.readConfirmation(appointment);
      const minutes = duration(before);
      if (!minutes || before.appointment_id !== appointment.appointment_id || before.client_code !== task.client_code ||
          before.professional_code !== appointment.professional_code || before.service_code !== appointment.service_code || before.status !== '1') {
        return human(deps, task, 'official_visit_changed');
      }
      const visit = {...appointment, duration_minutes: minutes};
      options = await deps.slots(task, {dates: [visit.appointment_date_iso], period: 'any', context: task.context,
        selection: {service_code: visit.service_code, professional_code: visit.professional_code,
          professional: visit.professional, allow_alternatives: false}});
      const decision = selectAutonomousSameDayMove({message: task.source_text, visits: [visit],
        availability: {source: 'official_task_reader', appointment_id: visit.appointment_id, date: visit.appointment_date_iso,
          professional_code: visit.professional_code, service_code: visit.service_code, read_at: new Date(now()).toISOString(), slots: options}, now: now()});
      if (decision.action !== 'move') return human(deps, task, decision.reason);
      const planned = update(deps, task.task_id, row => {
        row.plan = {kind: 'move', option: decision.slot, move_appointment_id: visit.appointment_id, move_source: before,
          policy_revision: decision.slot.policy_revision, autonomous_same_day: true};
        row.status = 'planned'; row.reason = null; row.planned_at = new Date(now()).toISOString();
      });
      return {ok: true, task_id: planned.task_id, token: baseline.token, action: 'move', executable: true};
    } catch {
      return human(deps, task, 'official_availability_unverified');
    }
  }};
}
