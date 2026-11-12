/*
 * Deterministic gate for the only autonomous reschedule permitted by the
 * current policy: one clear, same-day change for one verified visit.
 *
 * It deliberately does not read AppBarber, write state, send a reply, or own
 * a timer. The reply workflow supplies a fresh official visit and an official
 * slots read through its already-bound task reader. The caller must invoke
 * executeSameDayMove while `withTask` + `effectFetch` are active: that is what
 * binds the native `move` grant to the original inbound task and prevents a
 * second session or an unguarded AppBarber request.
 */

const PEOPLE = new Set(['16897547', '17055820']); // Pedro, Alysson
const time = value => typeof value === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
const text = value => String(value ?? '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
const held = reason => ({action: 'human_review', reason});

function requestedTime(message) {
  const matches = [...text(message).matchAll(/\b(?:as|para|pra)\s*([01]?\d|2[0-3])(?:[:h]([0-5]\d))\b/g)]
    .map(m => `${m[1].padStart(2, '0')}:${m[2]}`);
  return [...new Set(matches)];
}

function namedProfessional(message) {
  const value = text(message);
  const names = [
    [/\bpedro\b/, '16897547'],
    [/\balysson\b|\balisson\b/, '17055820'],
  ].filter(([pattern]) => pattern.test(value)).map(([, code]) => code);
  return [...new Set(names)];
}

function changesDate(message) {
  return /\b(?:amanha|hoje|segunda|terca|quarta|quinta|sexta|sabado|domingo|dia\s*\d{1,2})\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b/.test(text(message));
}
function affirmativeMove(message) {
  const value=text(message);
  const affirmative=/\b(?:pode\s+(?:mudar|trocar|remarcar|reagendar)\s+(?:para|pra)|(?:vamos\s+)?(?:mudar|trocar|remarcar|reagendar)\s+(?:para|pra)|combinamos\s+(?:as|para|pra)|ficou\s+(?:as|para|pra))\b/.test(value);
  if (!affirmative || /\b(?:nao|cancel|desmarc|talvez|se|seria|poderia|consegue|ocupad|disponivel|acho)\b/.test(value)) return false;
  return true;
}

function validVisit(visit) {
  return visit && typeof visit === 'object' && /^\d+$/.test(String(visit.appointment_id)) &&
    /^\d+$/.test(String(visit.client_code)) && /^\+55\d{10,11}$/.test(String(visit.destination_e164)) &&
    PEOPLE.has(String(visit.professional_code)) && /^\d+$/.test(String(visit.service_code)) &&
    /^\d{4}-\d{2}-\d{2}$/.test(String(visit.appointment_date_iso)) && time(visit.appointment_time) &&
    Number.isInteger(visit.duration_minutes) && visit.duration_minutes > 0;
}

/**
 * Return a move only when every binding is explicit. Anything missing or
 * contradictory is a human triage result, never a best-effort interpretation.
 */
export function selectAutonomousSameDayMove({message, visits, availability, now = Date.now()}) {
  if (!Array.isArray(visits) || visits.length !== 1 || !validVisit(visits[0])) return held('individual_visit_unverified');
  const visit = visits[0];
  if (changesDate(message)) return held('date_change_requires_human');
  const requested = requestedTime(message);
  if (requested.length !== 1) return held('target_time_not_clear');
  if (!affirmativeMove(message)) return held('reschedule_not_affirmative');
  const professional = namedProfessional(message);
  // The linked individual visit already supplies the professional. Naming the
  // same person is accepted; naming another or two people is triaged.
  if (professional.length > 1 || professional.length === 1 && professional[0] !== String(visit.professional_code)) return held('professional_changed_or_unclear');
  const target = requested[0];
  if (target === visit.appointment_time) return held('target_equals_current_time');
  if (Date.parse(`${visit.appointment_date_iso}T${target}:00-04:00`) <= now) return held('target_not_future');
  if (!availability || availability.source !== 'official_task_reader' || availability.appointment_id !== visit.appointment_id ||
    availability.date !== visit.appointment_date_iso || availability.professional_code !== visit.professional_code ||
    availability.service_code !== visit.service_code || !Number.isFinite(Date.parse(availability.read_at)) ||
    Date.parse(availability.read_at) > now || now - Date.parse(availability.read_at) > 300000 || !Array.isArray(availability.slots)) {
    return held('official_availability_missing');
  }
  const matching = availability.slots.filter(slot => slot && slot.date === visit.appointment_date_iso && slot.time === target &&
    String(slot.professional_code) === String(visit.professional_code) && String(slot.service_code) === String(visit.service_code) &&
    Number(slot.duration_minutes) === visit.duration_minutes);
  if (matching.length !== 1) return held('official_slot_not_unique');
  return {action: 'move', visit, slot: matching[0]};
}

/**
 * The caller supplies the existing, guarded adapter. `assertOrigin` is called
 * immediately before the adapter and is intentionally required: the adapter
 * must be running in the existing n8n reply task context, not in a new session.
 */
export async function executeSameDayMove(decision, {assertOrigin, moveOfficialAppointment, agendaConfig, markAttempt, fetchImpl}) {
  if (!decision || decision.action !== 'move') return {status: 'held', reason: decision?.reason || 'decision_required'};
  if (typeof assertOrigin !== 'function' || typeof moveOfficialAppointment !== 'function' || typeof markAttempt !== 'function') {
    throw Error('guarded_move_dependencies_required');
  }
  await assertOrigin({operation: 'move', visit: decision.visit, slot: decision.slot});
  return moveOfficialAppointment(decision.visit, decision.slot, agendaConfig, markAttempt, fetchImpl);
}
