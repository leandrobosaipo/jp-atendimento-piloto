import fs from 'node:fs';
import {matchVisit, consumeVisit} from './captured_visit.mjs';
import {approval, guardedPost, keyOf} from './effect_guard.mjs';

function scopes(link,target,context){
  const hex=link.key.slice(0,32);
  const task=[hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  return target.members.map(c=>({...context,task,source:link.key,client:c.client_code,
    phone:c.destination_e164,operation:'confirm',reference:c.appointment_id}));
}

function revalidate(h,link){
  const state=h.load(),e=link.confirmationEvidence?.event;
  if(!e || !e.providerTime || e.intervened || e.conflict || e.disposition!=='possible_confirmation'
     || state.dell_reply_links?.[link.key]?.status!=='awaiting_official')return null;
  const target=matchVisit(e,state,h);
  if(target.status!=='matched' || target.dispatch.message_id!==link.notice
     || JSON.stringify(target.members.map(h.snapshot))!==JSON.stringify(link.confirmationEvidence.snapshots)
     || target.members.some(c=> !(Date.parse(c.appointment_date_iso+'T'+c.appointment_time+':00-04:00')>Date.now())))return null;
  if(Object.values(state.dell_reply_links).some(r=>r.key!==link.key && r.notice===link.notice))return null;
  return target;
}

export function pendingCapturedWork(h,cutoff){
  if(!Number.isFinite(Date.parse(cutoff)))return false;
  const state=h.load(),out=state.jp_reply_ack_outbox||[];
  const file='/ops-config/authorized-visit-feedback.json';
  if(fs.existsSync(file)){
    const permit=JSON.parse(fs.readFileSync(file));
    const link=state.dell_reply_links?.[permit.link_key];
    if(permit.authorization==='operator_confirmed_single_visit_move' && permit.authorization_id
       && Date.parse(permit.valid_until)>Date.now() && link?.mid===permit.inbound_message_id
       && link?.notice===permit.notice && !h.blocked(state,permit.destination_e164)
       && JSON.stringify(permit.appointment_ids)===JSON.stringify(link.appointments)
       && !(state.jp_reply_attempts||[]).some(a=>a.source_message_id===permit.inbound_message_id && a.kind==='send')
       && !out.some(r=>r.link_key===link.key && r.state!=='queued'))return true;
  }
  if(Object.values(state.dell_reply_links||{}).some(link=>link.reschedule?.state==='grant_required'))return true;
  return Object.values(state.dell_reply_links||{}).some(link=>{
    if(!(Date.parse(link.at)>=Date.parse(cutoff)))return false;
    if(link.status==='official_confirmed'){
      const attempts=out.filter(x=>x.link_key===link.key);
      return !attempts.length || attempts.some(x=>x.state==='queued');
    }
    if(link.status!=='awaiting_official'||!link.confirmationEvidence)return false;
    const target=revalidate(h,link);
    if(!target)return false;
    return scopes(link,target,{role:'reply'}).every(scope=>{
      const file='/ops-config/grants/'+keyOf(scope)+'.json';
      try{return Date.parse(JSON.parse(fs.readFileSync(file)).until)>Date.now() && !fs.existsSync('/ops-state/admissions/effects/'+keyOf(scope)+'.intent');}
      catch{return false;}
    });
  });
}

export async function reconcileOne(link,h,authority){
  let target=revalidate(h,link);
  if(!target)return {status:'held'};
  const grants=scopes(link,target,authority.context);
  if(!grants.every(g=>authority.permitted(g)))return {status:'held'};
  const before=[];
  for(const candidate of target.members)before.push(await h.read(candidate));
  const matches=(c,p)=>p.appointment_id===c.appointment_id && p.client_code===c.client_code
    && p.service_code===c.service_code && p.professional_code===c.professional_code
    && p.start?.startsWith(c.appointment_date_iso+'T'+c.appointment_time) && p.status==='1' && typeof p.confirmed==='boolean';
  if(!before.every((p,i)=>matches(target.members[i],p)))return {status:'held'};
  for(const [i,c] of target.members.entries()){
    if(!revalidate(h,link) || !grants.every(g=>authority.permitted(g)))return {status:'held'};
    const fresh=await h.read(c);
    if(!matches(c,fresh) || fresh.item_code!==before[i].item_code)return {status:'held'};
    if(fresh.confirmed)continue;
    // AppBarber toggles this flag. One durable intent guards both timeouts and retries.
    if(authority.attempted(grants[i]))return {status:'write_unknown'};
    try{await authority.confirm(grants[i]);}catch{/* The next read is authoritative even after a timeout. */}
    const after=await h.read(c);
    if(!matches(c,after)||!after.confirmed||after.item_code!==fresh.item_code)return {status:'write_unknown'};
  }
  if(!revalidate(h,link))return {status:'held'};
  return consumeVisit(link.confirmationEvidence.event,h);
}

export async function runCapturedConfirmation(h){
  const {admitted}=await import('./task_reads.mjs');
  const {socket}=await import('./effect_client.mjs');
  const context=admitted('reply',['reply-execute']);
  if(!h.dispatchReady?.(context.execution))return {status:'preflight_held'};
  const config='/ops-config',state='/ops-state',ledger=state+'/admissions/effects';
  const authority={context,permitted(g){
    try{return approval(config,state,g).approved_by==='JP standing appointment confirmation';}catch{return false;}
  },attempted:g=>fs.existsSync(ledger+'/'+keyOf(g)+'.intent'),
  confirm:scope=>{
    const body={tipo:'5',agendamento:scope.reference};
    return guardedPost({config,state,ledger,scope,body,submit:()=>socket({action:'write',scope,body},AbortSignal.timeout(15000))});
  }};
  for(const link of Object.values(h.load().dell_reply_links||{})){
    if(link.status!=='awaiting_official'||!link.confirmationEvidence)continue;
    const target=revalidate(h,link);
    if(target && scopes(link,target,context).every(g=>authority.permitted(g)))return reconcileOne(link,h,authority);
  }
  return {status:'idle'};
}
