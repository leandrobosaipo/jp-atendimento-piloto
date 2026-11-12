/* Active reply-execute companion for a captured, linked reschedule request.
 * It has no sender. Writing occurs only through the pre-existing move grant.
 */
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import {matchVisit} from './captured_visit.mjs';
import {selectAutonomousSameDayMove} from './autonomous_same_day_reschedule.mjs';
import {readExactMoveSlot} from './task_reads.mjs';

const id = key => [key.slice(0,8),key.slice(8,12),key.slice(12,16),key.slice(16,20),key.slice(20,32)].join('-');
const targetTime = text => [...String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase().matchAll(/\b(?:as|para|pra)\s*([01]?\d|2[0-3])(?::([0-5]\d)|h([0-5]\d)?)?\b/g)].map(m=>m[1].padStart(2,'0')+':'+(m[2]||m[3]||'00'));
const changedDate = text => /\b(?:amanha|hoje|segunda|terca|quarta|quinta|sexta|sabado|domingo|dia\s*\d{1,2})\b|\b\d{1,2}[/-]\d{1,2}/.test(String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase());
const named = text => [...new Set([[/\bpedro\b/,'16897547'],[/\balysson\b|\balisson\b/,'17055820']].filter(([r])=>r.test(String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase())).map(([,v])=>v))];
const affirmative=text=>/\b(?:pode\s+(?:mudar|trocar|remarcar|reagendar)\s+(?:para|pra)|(?:vamos\s+)?(?:mudar|trocar|remarcar|reagendar)\s+(?:para|pra)|combinamos\s+(?:as|para|pra)|ficou\s+(?:as|para|pra))\b/.test(String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase())&&!/\b(?:nao|cancel\w*|desmarc\w*|talvez|se|seria|poderia|consegue|ocupad\w*|disponivel|acho)\b/.test(String(text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase());
export function reschedulePolicyEligible(link,policy,cutoff){
 const e=link?.sourceEvent,after=Date.parse(policy?.afterUtc),at=Date.parse(e?.at),cut=Date.parse(cutoff);
 const phrase=String(e?.text||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').trim().toLowerCase();
 const explicit=/^(?:(?:pedro|alysson|alisson)\s*,?\s+)?(?:(?:(?:pode|vamos)\s+)?(?:mudar|trocar|remarcar|reagendar)(?: o horario)?\s+(?:para|pra)|combinamos\s+(?:as|para|pra))\s*([01]?\d|2[0-3])(?:(?::|h)[0-5]\d|h)?(?:,? por favor)?[.!?]?$/u;
 if(!explicit.test(phrase)||phrase.includes('combinamos')&&phrase.endsWith('?'))return false;
 return policy?.enabled===true&&Number.isInteger(policy.afterSequence)&&Number.isFinite(after)&&Number.isFinite(at)&&Number.isFinite(cut)
  && e.capture_sequence>policy.afterSequence&&at>=after&&at>=cut&&link.status==='human_review'&&!link.reschedule&&e.providerTime===true&&!e.intervened&&!e.conflict
  && targetTime(e.text).length===1&&!changedDate(e.text)&&affirmative(e.text);
}

// Only the hold introduced by this very capture can be discounted for a move.
// All earlier feedback, manual holds, opt-outs and support remain authoritative.
export function matchMoveSource(link,state,h){
  const event=link.sourceEvent;
  if(Object.values(state.dell_reply_links||{}).some(other=>other.key!==link.key&&other.notice===link.notice))return {status:'visit_review'};
  const earlier={...state,jp_responses:(state.jp_responses||[]).filter(row=>!(
    row.source==='dell_capture' && row.message_id===event.mid && row.outbound_message_id===link.notice &&
    link.appointments.includes(row.appointment_id)
  ))};
  return matchVisit(event,state,{...h,feedback:c=>h.feedback(c,earlier)});
}

export async function prepareCapturedSameDayReschedule(h, {fetchImpl=fetch}={}) {
  let state=h.load();
  let policy;try{policy=JSON.parse(fs.readFileSync('/ops-config/automatic-reschedules.json','utf8'));}catch{return {status:'idle'};}
  for(const link of Object.values(state.dell_reply_links||{})){
    if(!reschedulePolicyEligible(link,policy,policy.afterUtc))continue;
    const event=link.sourceEvent, target=matchMoveSource(link,state,h);
    const members=target.members||[];
    const times=targetTime(event.text), people=named(event.text);
    let reason=null;
    if(target.status!=='matched'||members.length!==1)reason='individual_visit_unverified';
    else if(changedDate(event.text))reason='date_change_requires_human';
    else if(times.length!==1)reason='target_time_not_clear';
    else if(people.length>1||people.length===1&&people[0]!==String(members[0].professional_code))reason='professional_changed_or_unclear';
    if(reason){link.reschedule={state:'human_review',reason,at:new Date().toISOString()};state.version=(state.version||0)+1;h.save(state);return {status:'human_review',reason};}
    const visit=members[0], task={task_id:id(link.key),source_sha256:link.key,source_message_id:event.mid,client_code:visit.client_code,destination_e164:visit.destination_e164,status:'planned'};
    try{
      state.jp_reply_tasks||=[];
      const existing=state.jp_reply_tasks.filter(row=>row.task_id===task.task_id);
      if(existing.length>1||existing.length===1&&['human','delivery_unknown','sending'].includes(existing[0].status))throw Error('captured_task_conflict');
      if(!existing.length){state.jp_reply_tasks.push({...task,kind:'reschedule',source_at:event.at,source_text:event.text,context:{appointments:[visit]},created_at:new Date().toISOString()});state.version=(state.version||0)+1;h.save(state);}
      const before=await h.read(visit);
      if(!before||String(before.appointment_id)!==String(visit.appointment_id)||String(before.client_code)!==String(visit.client_code)
        ||String(before.service_code)!==String(visit.service_code)||String(before.professional_code)!==String(visit.professional_code)
        ||before.status!=='1'||!before.item_code)throw Error('official_before_unverified');
      const start=Date.parse(before.start),end=Date.parse(before.end);
      const duration_minutes=(end-start)/60000;
      if(!Number.isInteger(duration_minutes)||duration_minutes<=0)throw Error('official_duration_unverified');
      const slot=await readExactMoveSlot(task,h.load,visit,times[0],fetchImpl);
      const decision=selectAutonomousSameDayMove({message:event.text,visits:[{...visit,duration_minutes}],availability:{source:'official_task_reader',appointment_id:visit.appointment_id,date:visit.appointment_date_iso,professional_code:visit.professional_code,service_code:visit.service_code,read_at:new Date().toISOString(),slots:[slot]}});
      if(decision.action!=='move')reason=decision.reason;
      else {
        // Re-load after official reads; never overwrite a newer capture/hold.
        state=h.load();const current=state.dell_reply_links?.[link.key];
        if(!current||current.status!=='human_review'||current.sourceEvent?.mid!==event.mid||current.sourceEvent?.content!==event.content)throw Error('captured_source_changed');
        const proposal={task_id:task.task_id,source:current.key,appointment_id:visit.appointment_id,client:visit.client_code,phone:visit.destination_e164,
          before:{date:visit.appointment_date_iso,time:visit.appointment_time,professional_code:visit.professional_code,service_code:visit.service_code,duration_minutes,item_code:before.item_code},
          slot,capture_sequence:event.capture_sequence,message_id:event.mid,fingerprint:event.content,source_at:event.at,person:event.pn};
        if(!Number.isInteger(proposal.capture_sequence)||!proposal.fingerprint)throw Error('capture_proof_missing');
        const stored=state.jp_reply_tasks.find(row=>row.task_id===task.task_id);
        if(!stored||stored.status!=='planned')throw Error('move_task_not_planned');
        stored.plan={kind:'move',move_appointment_id:String(visit.appointment_id),option:slot};
        current.reschedule={state:'grant_required',...proposal,at:new Date().toISOString()};state.version=(state.version||0)+1;h.save(state);return {status:'grant_required',task_id:task.task_id,proposal};
      }
    }catch{reason='official_availability_unverified';}
    state=h.load();
    const remaining=state.dell_reply_links?.[link.key];
    if(remaining?.sourceEvent?.mid===event.mid && remaining.status==='human_review' && !remaining.reschedule){
      remaining.reschedule={state:'human_review',reason,at:new Date().toISOString()};state.version=(state.version||0)+1;h.save(state);
    }
    return {status:'human_review',reason};
  }
  return {status:'idle'};
}
