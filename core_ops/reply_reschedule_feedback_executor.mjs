/* Companion consumer for the existing jp_reply_ack_outbox.  It shares the
 * reply owner and outbox schema with acknowledgement delivery; it cannot
 * project, move, or retry an uncertain provider outcome. */
import {createHash} from 'node:crypto';
const sha=v=>createHash('sha256').update(v).digest('hex');
const phone=v=>/^\+55\d{10,11}$/.test(v||'');
function rowIsSafe(row){return row&&row.kind==='reschedule'&&row.state==='queued'&&/^[a-f0-9]{64}$/.test(row.id||'')&&/^[a-f0-9]{64}$/.test(row.link_key||'')&&phone(row.destination_e164)&&Array.isArray(row.appointment_ids)&&row.appointment_ids.length===1&&typeof row.reschedule_task_id==='string'&&/^Ficou combinado para \*([01]\d|2[0-3]):[0-5]\d\*\.$/.test(row.text||'');}
async function evidence(h,state,row){
 const link=state.dell_reply_links?.[row.link_key],p=link?.reschedule,attempt=(state.jp_reply_attempts||[]).find(x=>x.task_id===row.reschedule_task_id&&x.kind==='move');
 if(!p||p.state!=='saved'||!attempt||attempt.status!=='saved'||String(p.appointment_id)!==String(row.appointment_ids[0])||p.phone!==row.destination_e164||!p.readback)return false;
 const after=await h.read({kind:'appointment_confirmation',appointment_id:p.appointment_id,client_code:p.client,professional_code:p.slot.professional_code,service_code:p.slot.service_code,appointment_date_iso:p.slot.date,appointment_date:p.slot.date.split('-').reverse().join('/'),appointment_time:p.slot.time});
 return after?.appointment_id===p.appointment_id&&after?.client_code===p.client&&after?.professional_code===p.slot.professional_code&&after?.service_code===p.slot.service_code&&String(after?.start||'').startsWith(p.slot.date+'T'+p.slot.time)&&after?.status==='1';
}
export async function executeRescheduleFeedback(h,fetchImpl=fetch){
 let state=h.load(),row=(state.jp_reply_ack_outbox||[]).filter(rowIsSafe).sort((a,b)=>a.id.localeCompare(b.id))[0];
 if(!row)return {status:'idle'};
 const version=state.version;
 if(!await evidence(h,state,row))return {status:'held'};
 state=h.load();
 if(state.version!==version || h.blocked?.(state,row.destination_e164))return {status:'held'};
 row=state.jp_reply_ack_outbox.find(item=>item.id===row.id);
 if(!rowIsSafe(row) || Object.values(state.dell_reply_links||{}).some(other=>other.key!==row.link_key && other.appointments?.includes(row.appointment_ids[0]) && Date.parse(other.at)>=Date.parse(row.source_at)))return {status:'held'};
 state.jp_reply_attempts||=[];if(state.jp_reply_attempts.some(x=>x.attempt_id===row.id))return {status:'delivery_unknown'};
 state.jp_reply_attempts.push({kind:'send',attempt_id:row.id,source_message_id:row.inbound_message_id,destination_e164:row.destination_e164,text:row.text,status:'delivery_unknown',ack_id:row.id,attempted_at:new Date().toISOString()});row.state='delivery_unknown';state.version=(state.version||0)+1;h.save(state);
 let res,body;try{res=await fetchImpl('http://owner.invalid/message/sendText/'+h.instance,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({number:row.destination_e164.slice(1),text:row.text}),redirect:'error'});body=await res.json();}catch{return {status:'delivery_unknown'};}
 const latest=h.load(),stored=latest.jp_reply_ack_outbox.find(x=>x.id===row.id),attempt=latest.jp_reply_attempts.find(x=>x.attempt_id===row.id);
 if(res.status===201&&body?.status==='provider_accepted'&&body?.key?.fromMe===true&&typeof body.key.id==='string'){stored.state='provider_accepted';stored.provider_message_id=body.key.id;attempt.status='provider_accepted';attempt.message_id=body.key.id;latest.version=(latest.version||0)+1;h.save(latest);return {status:'provider_accepted'};}
 return {status:'delivery_unknown'};
}
export const rescheduleFeedbackAttemptId=row=>sha(JSON.stringify(['jp-same-day-move-feedback-v1',row?.reschedule_task_id]));
