import { createHash } from 'node:crypto';

const hash = value => createHash('sha256').update(value).digest('hex');

// A ticket is produced by the private agenda reader. All customer-facing
// fields come from the fixed, operator-approved document.
export async function sendFixedReturn(h, execution) {
  const scope = h.scope();
  const ticket = h.ticket(execution);
  const now = h.now();
  if (!scope || !ticket || scope.digest !== hash(scope.raw) ||
      ticket.approval !== scope.digest || ticket.execution !== execution ||
      ticket.status !== 'matched' || ticket.checked > now || now - ticket.checked > 15000 ||
      !Number.isFinite(ticket.checked) || !(scope.opens <= now && now < scope.closes) ||
      !h.dispatchReady(execution) || !h.businessTime()) return 'held';
  const rows = JSON.parse(scope.raw).items;
  if (rows.length !== 11 || new Set(rows.map(r => r.client)).size !== 11 ||
      new Set(rows.map(r => r.approval_key)).size !== 11) return 'held';
  const row = rows.find(r => r.approval_key === ticket.key);
  if (!row || row.client !== ticket.client || !/^55[1-9]\d{9,10}$/.test(ticket.number) ||
      ticket.textHash !== hash(row.text) ||
      JSON.stringify(ticket.options) !== JSON.stringify(row.options)) return 'held';
  const id = hash(JSON.stringify(['fixed-return-v1', scope.digest, row.client]));
  // No asynchronous work between checking the ledger and writing the intent.
  const state = h.load(), prior = (state.fixed_return_dispatches || []).find(a => a.attempt_id === id);
  if (prior) return 'already_attempted';
  const destination = '+' + ticket.number;
  if (h.held(state, destination)) return 'held';
  const reservation = h.reserve(state,row.text,{kind:'reactivation',destination_e164:destination,
    single:true,reservation_id:id});
  if (!reservation) return 'budget_held';
  const attempt = {attempt_id:id,request_id:row.approval_key,client_code:row.client,
    destination_e164:destination,text_sha256:ticket.textHash,execution,attempted_at:new Date(now).toISOString(),
    status:'delivery_unknown',outbound_budget_reservation_id:reservation};
  (state.fixed_return_dispatches ||= []).push(attempt);
  state.version++;h.save(state);
  let messageId;
  try {
    messageId = await h.deliver(ticket.number,row.text);
  } catch {
    // The physical operation may already have reached WhatsApp.
  }
  const latest = h.load();
  const saved = latest.fixed_return_dispatches.find(a => a.attempt_id === id);
  if (typeof messageId === 'string' && /^[\w-]{6,160}$/.test(messageId)) {
    saved.message_id = messageId;
    saved.status = 'provider_accepted';
    latest.version++;h.save(latest);
  }
  h.finish(reservation,saved);
  return saved.status;
}
