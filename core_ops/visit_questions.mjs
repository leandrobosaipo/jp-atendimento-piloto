import fs from 'node:fs';
import {createHash} from 'node:crypto';

const digest=v=>createHash('sha256').update(v).digest('hex');
const policy='/ops-config/visit-questions.json';
const observation='/ops-config/read-grants/visit-questions-observed.json';
const ids=rows=>rows.map(c=>String(c.appointment_id));
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function questions(){
  return fs.existsSync(policy)? JSON.parse(fs.readFileSync(policy)).cases:[];
}
function observed(p){
  if(!fs.existsSync(observation))return null;
  const file=JSON.parse(fs.readFileSync(observation));
  if(Date.now()-file.at>75000 || file.at>Date.now()+1000)return null;
  return file.rows.find(v=>v.case===p.case && v.original===p.mid);
}
function binding(p,state,h){
  const original=Object.values(state.dell_reply_links||{}).filter(l=>l.mid===p.mid);
  if(original.length!==1)return false;
  const link=original[0];
  if(link.record!==p.record || link.notice!==p.notice || link.status!=='human_hold' || !equal(link.appointments,ids(p.members)))return false;
  const dispatches=(state.individual_dispatches||[]).filter(d=>d.message_id===p.notice);
  if(dispatches.length!==p.members.length || !p.members.every(c=>dispatches.some(d=>d.appointment_id===c.appointment_id && d.destination_e164===c.destination_e164)))return false;
  if(!h.questionObservation){
    const candidates=JSON.parse(fs.readFileSync(process.env.JP_APPROVAL_CANDIDATES_FILE||'/state/candidates.json')).candidates;
    const group=candidates.filter(c=>c.kind==='appointment_confirmation' && c.source_active!==false && c.client_code===p.members[0].client_code && c.appointment_date_iso===p.members[0].appointment_date_iso && c.official_status!=='realized');
    if(group.length!==p.members.length||!p.members.every(c=>group.some(v=>['appointment_id','destination_e164','professional_code','service_code','appointment_time'].every(k=>v[k]===c[k])&&!v.opt_out)))return false;
    if(candidates.some(c=>c.source_active!==false && c.destination_e164===p.members[0].destination_e164 && c.client_code!==p.members[0].client_code))return false;
  }
  return !p.members.some(c=>h.blocked(state,c.destination_e164)||h.support(state,c.appointment_id)) && !h.blocked(state,p.phone);
}
function visitFuture(p,now){return now<Date.parse(p.until);}
function due(p,now){
  const local=new Date(now-4*3600000);
  return visitFuture(p,now) && now>=Date.parse(p.after) && local.getUTCDay()!==0 && local.getUTCHours()>=7 && local.getUTCHours()<19;
}
export function proofMatches(c,p){
  return p?.appointment_id===c.appointment_id && p.client_code===c.client_code && p.service_code===c.service_code && p.professional_code===c.professional_code && p.status==='1' && p.start?.startsWith(c.appointment_date_iso+'T'+c.appointment_time) && typeof p.confirmed==='boolean';
}
function ready(p,h,now){
  const state=h.load(),o=(h.questionObservation||observed)(p);
  return due(p,now) && o?.untouched && binding(p,state,h) && !(state.jp_reply_attempts||[]).some(a=>a.source_message_id===p.mid);
}
export function questionPending(h){return questions().some(p=>ready(p,h,Date.now()));}
export async function askOne(h,fetchImpl,permits=questions()){
  for(const p of permits){
    if(!ready(p,h,Date.now()))continue;
    const checks=[];
    for(const c of p.members)checks.push(await h.read(c));
    if(!checks.every((o,i)=>proofMatches(p.members[i],o)&&!o.confirmed)||!ready(p,h,Date.now()))continue;
    const state=h.load(),attempt_id=digest(JSON.stringify(['jp-visit-question-v1',p.mid]));
    const a={kind:'send',attempt_id,source_message_id:p.mid,destination_e164:p.phone,text:p.question,status:'delivery_unknown',attempted_at:new Date().toISOString(),clarification_case:p.case};
    (state.jp_reply_attempts||=[]).push(a);state.version++;h.save(state);
    try{
      const r=await fetchImpl('http://owner.invalid/message/sendText/'+h.instance,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({number:p.phone.slice(1),text:p.question}),redirect:'error'});
      const b=await r.json();
      if(r.status===201&&b?.status==='provider_accepted'&&b.key?.fromMe===true&&b.key.remoteJid===p.phone.slice(1)+'@s.whatsapp.net'&&b.key.id){
        const now=h.load(),row=now.jp_reply_attempts.find(x=>x.attempt_id===attempt_id);
        row.status='provider_accepted';row.message_id=b.key.id;now.version++;h.save(now);
        return {status:'provider_accepted'};
      }
    }catch{}
    return {status:'delivery_unknown'};
  }
  return null;
}
export function answeredQuestion(event,state,h,permits=questions()){
  if(!event.latestDelivered||!event.providerTime||event.conflict||event.intervened||event.quote&&event.quote!==event.latestDelivered)return null;
  for(const p of permits){
    const o=(h.questionObservation||observed)(p);
    if(!o || o.answer!==event.mid || o.native!==event.latestDelivered || !event.delivered.includes(o.native) || event.pn!==p.person || !visitFuture(p,Date.now())||!binding(p,state,h))continue;
    const a=(state.jp_reply_attempts||[]).find(a=>a.clarification_case===p.case && a.source_message_id===p.mid && a.message_id===o.native && a.status==='provider_accepted');
    if(!a||Date.parse(event.at)<Date.parse(a.attempted_at)-1000)continue;
    return {status:'matched',candidate:p.members[0],members:p.members,dispatch:{message_id:o.native},case:p.case};
  }
  return null;
}
