import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {projectOfficialAcknowledgements} from './captured_visit.mjs';
import {askOne, questions} from './visit_questions.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
const validPhone=value=>/^\+55\d{10,11}$/.test(value||'');

function safeAck(row, cutoff) {
  return row && row.state==='queued' && /^[a-f0-9]{64}$/.test(row.id||'')
    && /^[a-f0-9]{64}$/.test(row.link_key||'') && validPhone(row.destination_e164)
    && Array.isArray(row.appointment_ids) && row.appointment_ids.length
    && Number.isFinite(Date.parse(row.source_at)) && (typeof row.authorized_exception==='string'||Date.parse(row.source_at)>=Date.parse(cutoff))
    && ((row.kind==='emoji' && row.text==='👍') || (row.kind==='text' && (row.text==='Confirmado.' || typeof row.authorized_exception==='string')));
}

function inScope(row, cutoff) {
  return row && /^[a-f0-9]{64}$/.test(row.id||'') && Number.isFinite(Date.parse(row.source_at))
    && Date.parse(row.source_at)>=Date.parse(cutoff);
}

function sameIds(left,right){return Array.isArray(left)&&Array.isArray(right)&&left.length===right.length&&new Set(left.map(String)).size===left.length&&left.every(id=>right.map(String).includes(String(id)));}
function authorized(state){
  try{
    const raw=JSON.parse(fs.readFileSync('/ops-config/authorized-visit-feedback.json','utf8'));
    const rows=Array.isArray(raw)?raw:Array.isArray(raw.entries)?raw.entries:[raw];
    return rows.filter(x=>typeof x.authorization_id==='string'&&typeof x.authorized_at==='string'&&typeof x.link_key==='string'&&typeof x.inbound_message_id==='string'&&typeof x.notice==='string'&&validPhone(x.provider_destination_e164||x.destination_e164)&&Array.isArray(x.appointment_ids)&&typeof x.text==='string'&&x.text.length>2&&x.text.length<=160);
  }catch{return [];}
}
function projectAuthorizedException(state){
  const out=state.jp_reply_ack_outbox||=[];let created=0;
  for(const item of authorized(state)){
    const link=state.dell_reply_links?.[item.link_key],dest=item.provider_destination_e164||item.destination_e164;
    if(!link||link.key!==item.link_key||link.mid!==item.inbound_message_id||link.notice!==item.notice||!sameIds(link.appointments,item.appointment_ids))continue;
    const id=sha(JSON.stringify(['jp-authorized-visit-feedback-v1',item.authorization_id,item.link_key]));
    if(out.some(row=>row.id===id))continue;
    out.push({id,link_key:item.link_key,appointment_ids:[...item.appointment_ids],destination_e164:dest,inbound_message_id:item.inbound_message_id,kind:'text',text:item.text,state:'queued',source_at:link.at,authorized_exception:item.authorization_id,candidate:item.candidate||null,created_at:new Date().toISOString()});created++;
  }return created;
}
async function currentEvidence(h,state,row){
  const link=state.dell_reply_links?.[row.link_key];
  if(!link||!sameIds(link.appointments,row.appointment_ids))return false;
  if(row.authorized_exception){
    const exact=authorized(state).find(item=>item.authorization_id===row.authorized_exception&&item.link_key===row.link_key&&item.text===row.text&&sameIds(item.appointment_ids,row.appointment_ids)&&(item.provider_destination_e164||item.destination_e164)===row.destination_e164);
    const blocked=typeof h.blocked==='function'&&h.blocked(state,row.destination_e164);
    if(!exact||String(exact.private_note)!=='466'||blocked&&link.status!=='human_hold'||!row.candidate||String(row.candidate.appointment_id)!==String(row.appointment_ids[0]))return false;
    if((state.jp_responses||[]).some(item=>String(item.appointment_id)===String(row.candidate.appointment_id)&&item.intent==='opt_out'))return false;
    if(Object.values(state.dell_reply_links||{}).some(item=>item.key!==link.key&&item.appointments?.map(String).includes(String(row.candidate.appointment_id))&&Date.parse(item.at)>Date.parse(link.at)))return false;
    if(!(Date.parse(row.candidate.appointment_date_iso+'T'+row.candidate.appointment_time+':00-04:00')>Date.now()))return false;
    const proof=await h.read(row.candidate);return proof?.confirmed===true&&proof.status==='1'&&String(proof.appointment_id)===String(row.candidate.appointment_id);
  }
  if(link.status!=='official_confirmed')return false;
  const candidates=[];
  for(const id of row.appointment_ids){const rows=(link.ackCandidates||[]).filter(item=>String(item.appointment_id)===String(id)&&item.destination_e164===row.destination_e164);if(rows.length!==1)return false;candidates.push(rows[0]);}
  const responses=(state.jp_responses||[]).filter(item=>item.message_id===link.mid&&row.appointment_ids.map(String).includes(String(item.appointment_id)));
  if(responses.length!==row.appointment_ids.length||responses.some(item=>item.destination_e164!==row.destination_e164||item.intent==='opt_out'||item.official_update?.status!=='confirmed'))return false;
  if((state.reminders||[]).some(item=>row.appointment_ids.map(String).includes(String(item.appointment_id))&&['paused_customer_request','paused_support'].includes(item.status)))return false;
  if(typeof h.blocked==='function'&&h.blocked(state,row.destination_e164))return false;
  for(const candidate of candidates){const proof=await h.read(candidate);if(!proof||proof.confirmed!==true||proof.status!=='1'||String(proof.appointment_id)!==String(candidate.appointment_id))return false;}
  return true;
}

export async function executeAcknowledgements(h, cutoff, fetchImpl=fetch) {
  if(!Number.isFinite(Date.parse(cutoff))) throw Error('ack_cutoff_required');
  let state=h.load();
  const projected=projectOfficialAcknowledgements(state,cutoff), exceptionCreated=projectAuthorizedException(state);
  if(projected.created||exceptionCreated){state.version=(state.version||0)+1;h.save(state);}
  state=h.load();
  const row=(state.jp_reply_ack_outbox||[]).filter(item=>safeAck(item,cutoff))
    .sort((a,b)=>Date.parse(a.source_at)-Date.parse(b.source_at)||a.id.localeCompare(b.id))[0];
  if(!row){
    const clarification=await askOne(h,fetchImpl);
    if(clarification)return clarification;
    const previous=(state.jp_reply_ack_outbox||[]).filter(item=>inScope(item,cutoff))
      .map(item=>(state.jp_reply_attempts||[]).find(attempt=>attempt.attempt_id===item.id)).find(Boolean);
  return {status:previous?.status==='provider_accepted'?'accepted':previous?'delivery_unknown':'idle',projected:projected.created+exceptionCreated};
  }
  if(!await currentEvidence(h,state,row))return {status:'held',projected:projected.created};
  state.jp_reply_attempts||=[];
  const prior=state.jp_reply_attempts.find(item=>item.attempt_id===row.id);
  if(prior)return {status:prior.status==='provider_accepted'?'accepted':'delivery_unknown',projected:projected.created};
  const attempt={kind:'send',attempt_id:row.id,source_message_id:row.inbound_message_id,destination_e164:row.destination_e164,
    text:row.text,attempted_at:new Date().toISOString(),status:'delivery_unknown',ack_id:row.id};
  const parent=state.dell_reply_links[row.link_key];
  const question=questions().find(p=>sameIds(p.members.map(c=>c.appointment_id),row.appointment_ids) && state.jp_reply_attempts.some(a=>a.clarification_case===p.case && a.message_id===parent.notice && a.source_message_id===p.mid && a.status==='provider_accepted'));
  if(question)attempt.destination_e164=question.phone;
  state.jp_reply_attempts.push(attempt);row.state='delivery_unknown';row.attempt_id=row.id;row.attempted_at=attempt.attempted_at;
  state.version=(state.version||0)+1;h.save(state);
  const number=attempt.destination_e164.slice(1);
  let response,body;
  try {
    response=await fetchImpl('http://owner.invalid/message/sendText/'+h.instance,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({number,text:row.text}),redirect:'error'});
    body=await response.json();
  } catch {
    return {status:'delivery_unknown',projected:projected.created};
  }
  state=h.load();
  const stored=(state.jp_reply_ack_outbox||[]).find(item=>item.id===row.id);
  const storedAttempt=(state.jp_reply_attempts||[]).find(item=>item.attempt_id===row.id);
  if(response.status===201&&body?.status==='provider_accepted'&&body?.key?.fromMe===true&&typeof body.key.id==='string'){
    storedAttempt.status='provider_accepted';storedAttempt.message_id=body.key.id;
    stored.state='provider_accepted';stored.provider_message_id=body.key.id;stored.accepted_at=new Date().toISOString();
    state.version=(state.version||0)+1;h.save(state);
    return {status:'provider_accepted',projected:projected.created};
  }
  return {status:'delivery_unknown',projected:projected.created};
}

export const acknowledgementAttemptId=row=>sha(JSON.stringify(['jp-confirmation-ack-v1',row?.id]));
