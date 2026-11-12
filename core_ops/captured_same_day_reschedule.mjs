/* Active reply-execute companion for a captured, linked reschedule request.
 * It has no sender. Writing occurs only through the pre-existing move grant.
 */
import {createHash} from 'node:crypto';
import {matchVisit} from './captured_visit.mjs';
import {selectAutonomousSameDayMove} from './autonomous_same_day_reschedule.mjs';
import {readExactMoveSlot} from './task_reads.mjs';

const id = key => [key.slice(0,8),key.slice(8,12),key.slice(12,16),key.slice(16,20),key.slice(20,32)].join('-');
const targetTime = text => [...String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase().matchAll(/\b(?:as|para|pra)\s*([01]?\d|2[0-3])(?:[:h]([0-5]\d))\b/g)].map(m=>m[1].padStart(2,'0')+':'+m[2]);
const changedDate = text => /\b(?:amanha|hoje|segunda|terca|quarta|quinta|sexta|sabado|domingo|dia\s*\d{1,2})\b|\b\d{1,2}[/-]\d{1,2}/.test(String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase());
const named = text => [...new Set([[/\bpedro\b/,'16897547'],[/\balysson\b|\balisson\b/,'17055820']].filter(([r])=>r.test(String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase())).map(([,v])=>v))];

export async function prepareCapturedSameDayReschedule(h, {fetchImpl=fetch}={}) {
  const state=h.load();
  for(const link of Object.values(state.dell_reply_links||{})){
    if(link.status!=='human_review'||!link.sourceEvent||link.reschedule?.state)continue;
    const event=link.sourceEvent, target=matchVisit(event,state,h);
    const members=target.members||[];
    const times=targetTime(event.text), people=named(event.text);
    let reason=null;
    if(target.status!=='matched'||members.length!==1)reason='individual_visit_unverified';
    else if(changedDate(event.text))reason='date_change_requires_human';
    else if(times.length!==1)reason='target_time_not_clear';
    else if(people.length!==1||people[0]!==String(members[0].professional_code))reason='professional_changed_or_unclear';
    if(reason){link.reschedule={state:'human_review',reason,at:new Date().toISOString()};state.version=(state.version||0)+1;h.save(state);return {status:'human_review',reason};}
    const visit=members[0], task={task_id:id(link.key),source_sha256:link.key,source_message_id:event.mid,client_code:visit.client_code,destination_e164:visit.destination_e164,status:'planned'};
    try{
      const slot=await readExactMoveSlot(task,h.load,visit,times[0],fetchImpl);
      const decision=selectAutonomousSameDayMove({message:event.text,visits:[{...visit,duration_minutes:slot.duration_minutes}],availability:{source:'official_task_reader',appointment_id:visit.appointment_id,date:visit.appointment_date_iso,professional_code:visit.professional_code,service_code:visit.service_code,read_at:new Date().toISOString(),slots:[slot]}});
      if(decision.action!=='move')reason=decision.reason;
      else {link.reschedule={state:'grant_required',task_id:task.task_id,source:link.key,appointment_id:visit.appointment_id,slot,at:new Date().toISOString()};state.version=(state.version||0)+1;h.save(state);return {status:'grant_required',task_id:task.task_id};}
    }catch{reason='official_availability_unverified';}
    link.reschedule={state:'human_review',reason,at:new Date().toISOString()};state.version=(state.version||0)+1;h.save(state);return {status:'human_review',reason};
  }
  return {status:'idle'};
}
