/* Bind same-day moves to the existing signed reader/effect source. */
import {approval} from './effect_guard.mjs';
import {admitted,readExactMoveSlot} from './task_reads.mjs';
import {effectFetch,withReplyEffect} from './effect_client.mjs';

const valid=p=>p&&/^[a-f0-9-]{36}$/.test(p.task_id||'')&&/^[a-f0-9]{64}$/.test(p.source||'')&&/^\d+$/.test(String(p.appointment_id||''))&&/^\d+$/.test(String(p.client||''))&&/^\+55\d{10,11}$/.test(p.phone||'');
const taskFor=(load,p)=>{
 const row=(load().jp_reply_tasks||[]).filter(t=>t.task_id===p.task_id&&t.status==='planned'&&t.source_sha256===p.source&&String(t.client_code)===String(p.client)&&t.destination_e164===p.phone);
 if(row.length!==1)throw Error('reschedule_task_unbound');return row[0];
};
const scopeFor=p=>({...admitted('reply',['reply-execute']),task:p.task_id,source:p.source,client:String(p.client),phone:p.phone,operation:'move',reference:String(p.appointment_id)});
const visitFor=p=>({appointment_id:String(p.appointment_id),client_code:String(p.client),destination_e164:p.phone,appointment_date_iso:p.before.date,appointment_time:p.before.time,professional_code:String(p.before.professional_code),service_code:String(p.before.service_code),duration_minutes:p.before.duration_minutes,item_code:p.before.item_code});

export function createSameDayRescheduleHelpers({load,agendaConfig,moveOfficialAppointment,fetchImpl=fetch}){
 if(typeof load!=='function'||typeof agendaConfig!=='function'||typeof moveOfficialAppointment!=='function')throw Error('reschedule_helpers_dependencies_required');
 return {
  async grantPresent(p){try{if(!valid(p))return false;taskFor(load,p);const grant=approval('/ops-config','/ops-state',scopeFor(p));return grant.approved_by==='JP captured affirmative same-day reschedule';}catch{return false;}},
  async recheck(p){if(!valid(p))return null;const task=taskFor(load,p);return readExactMoveSlot(task,load,visitFor(p),p.slot.time,fetchImpl);},
  async move(p){if(!valid(p))throw Error('reschedule_move_scope_denied');const task=taskFor(load,p),visit=visitFor(p);
   return withReplyEffect(task,load,()=>moveOfficialAppointment(visit,p.slot,agendaConfig(),async()=>{},effectFetch(fetchImpl)));
  }
 };
}
