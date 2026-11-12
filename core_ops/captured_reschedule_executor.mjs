/* One-shot continuation after the independent issuer has installed a move grant.
 * No sender lives here: feedback is projected into the reply outbox only after
 * the official post-write readback matches the exact appointment and slot. */
import {createHash} from 'node:crypto';

const sha=value=>createHash('sha256').update(value).digest('hex');
export function queueRescheduleFeedback(state,p){
 const out=state.jp_reply_ack_outbox||=[];
 const id=sha(JSON.stringify(['jp-same-day-move-feedback-v1',p.task_id,p.source,p.appointment_id,p.slot.date,p.slot.time]));
 if(!out.some(row=>row.id===id))out.push({id,link_key:p.source,appointment_ids:[p.appointment_id],destination_e164:p.phone,
   inbound_message_id:p.message_id,kind:'reschedule',text:`Ficou combinado para *${p.slot.time}*.`,state:'queued',source_at:p.source_at,
   reschedule_task_id:p.task_id,created_at:new Date().toISOString()});
 return id;
}

const sameSlot=(p,slot)=>slot&&slot.date===p.slot.date&&slot.time===p.slot.time&&String(slot.professional_code)===String(p.slot.professional_code)&&String(slot.service_code)===String(p.slot.service_code)&&slot.duration_minutes===p.slot.duration_minutes;
export async function executePreparedReschedule(h,{grantPresent,recheck,move,enqueueFeedback}){
 const state=h.load();
 for(const link of Object.values(state.dell_reply_links||{})){
  const p=link.reschedule;if(!p||p.state!=='grant_required')continue;
  if(!await grantPresent(p))return {status:'grant_required'};
  if(!sameSlot(p,await recheck(p)))return {status:'held'};
  if((state.jp_reply_attempts||[]).some(a=>a.task_id===p.task_id&&a.kind==='move'))return {status:'write_unknown'};
  state.jp_reply_attempts||=[];state.jp_reply_attempts.push({task_id:p.task_id,kind:'move',status:'write_unknown',attempted_at:new Date().toISOString(),appointment_id:p.appointment_id,client_code:p.client,destination_e164:p.phone,source_sha256:p.source,before:{...p.before,appointment_id:p.appointment_id},option:p.slot});p.state='write_pending';state.version=(state.version||0)+1;h.save(state);
  const after=await move(p);
  if(!after||after.appointment_id!==p.appointment_id||after.client_code!==p.client||after.professional_code!==p.slot.professional_code||after.service_code!==p.slot.service_code||!String(after.start||'').startsWith(p.slot.date+'T'+p.slot.time))return {status:'write_unknown'};
  const latest=h.load(),row=(latest.jp_reply_attempts||[]).find(a=>a.task_id===p.task_id&&a.kind==='move');
  const current=Object.values(latest.dell_reply_links||{}).find(item=>item.reschedule?.task_id===p.task_id)?.reschedule;
  if(!row||!current||current.state!=='write_pending')return {status:'write_unknown'};
  row.status='saved';row.readback=after;current.state='saved';current.readback=after;latest.version=(latest.version||0)+1;h.save(latest);
  await enqueueFeedback(latest,current);return {status:'saved'};
 }
 return {status:'idle'};
}
