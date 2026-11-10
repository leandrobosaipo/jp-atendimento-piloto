import fs from 'node:fs';
import {createHash,timingSafeEqual} from 'node:crypto';

const sha=s=>createHash('sha256').update(s).digest('hex');
const need=b=>{if(!b)throw Error('manual_review_required');};
const unique=list=>{need(list.length===1);return list[0];};
const view=(r,t)=>({request:r.request_id,task:t.task_id,client:t.client_code,source:r.source_message_id,
  phone:t.destination_e164,desired_name:t.name,previous_name:t.previous_name??null,version:t.name_version,
  status:t.status,mode:t.manual_handoff?.mode??null,operator:t.manual_handoff?.operator??null,consumer_verified:t.consumer_verified===true});

export function change(input,actor,{load,save,paused}){
  need(paused() && typeof actor==='string' && actor.length>0 && typeof input.request==='string');
  const state=load(),requests=state.contact_registration_requests||[],tasks=state.contact_registration_tasks||[];
  const row=unique(requests.filter(r=>r.request_id===input.request)),task=unique(tasks.filter(t=>t.task_id===row.task_id));
  need(row.client_code===task.client_code && row.destination_e164===task.destination_e164 && row.source_message_id===task.source_message_id &&
    row.dispatch?.status==='provider_accepted' && row.dispatch?.message_id===task.source_message_id && !!task.client_code &&
    /^\+55\d{11}$/.test(task.destination_e164) && typeof task.name==='string' && task.name.length>0 && sha(task.name)===task.name_version);
  need(requests.filter(r=>r.task_id===task.task_id||r.source_message_id===row.source_message_id).length===1);
  const current=view(row,task);
  if(input.action==='inspect')return current;
  need(input.authorized===true && ['task','client','source','phone','version'].every(k=>typeof input[k]==='string'&&input[k]===current[k]));
  const other=tasks.filter(t=>t.task_id!==task.task_id && t.destination_e164===task.destination_e164 &&
    !['confirmed','failed'].includes(t.status));
  need(other.length===0);
  const at=new Date().toISOString();
  if(input.action==='claim'){
    if(task.manual_handoff){need(task.manual_handoff.operator===actor);return current;}
    need(['pending','unknown','readback_confirmed'].includes(task.status));
    if(task.status==='pending')need(task.write_attempts===0 && !task.reason_codes?.length);
    const unknown=task.status!=='pending';
    if(!unknown){
      const before=input.before,seen=Date.parse(before?.at);
      need(before?.source==='device' && before.phone===task.destination_e164 && before.name===(task.previous_name??null) &&
        Number.isFinite(seen) && seen<=Date.now() && Date.now()-seen<=300000 && /^[\w.:/-]{4,120}$/.test(before.reference||''));
    }
    task.manual_handoff={operator:actor,at,prior:task.status,mode:unknown?'observe_only':'manual',
      request:row.request_id,previous_reasons:[...(task.reason_codes||[])]};
    if(!unknown)task.manual_handoff.before={at:input.before.at,reference_sha256:sha(input.before.reference)};
    if(!unknown)task.status=row.status='human_handling';
  }else{
    need(input.action==='close' && task.manual_handoff?.operator===actor && task.manual_handoff.request===row.request_id);
    if(task.manual_handoff.closed_at){need(task.consumer_verified===true);return current;}
    need(['human_handling','unknown','readback_confirmed'].includes(task.status));
    const proofs={};
    for(const medium of ['web','device']){
      const proof=input[medium],when=Date.parse(proof?.at);
      need(proof?.source===medium && proof.phone===task.destination_e164 && proof.name===task.name &&
        typeof proof.reference==='string' && /^[\w.:/-]{4,120}$/.test(proof.reference) &&
        Number.isFinite(when) && when>=Date.parse(task.manual_handoff.at) && when<=Date.now() && Date.now()-when<=300000);
      proofs[medium]={at:proof.at,reference_sha256:sha(proof.reference)};
    }
    need(proofs.web.reference_sha256!==proofs.device.reference_sha256 &&
      !Object.values(proofs).some(p=>p.reference_sha256===task.manual_handoff.before?.reference_sha256));
    task.manual_handoff.closed_at=at;task.manual_handoff.proofs=proofs;
    task.status=row.status='confirmed';task.consumer_status='confirmed';task.consumer_verified=true;
    task.consumer_confirmed_at=at;task.consumer_proof={operator:actor,web_verified:true,device_verified:true,at,manual:true};
  }
  task.updated_at=row.updated_at=at;state.version++;save(state);
  const after=load(),confirmed=unique(after.contact_registration_tasks.filter(t=>t.task_id===task.task_id));
  const request=unique(after.contact_registration_requests.filter(r=>r.request_id===row.request_id));
  need(confirmed.status===task.status && confirmed.manual_handoff.operator===actor);
  return view(request,confirmed);
}

export async function handleContactManual(req,res,{load,save,body,reply,config}){
  const deny=()=>reply(res,403,{ok:false,error:'operator_identity_required'});
  if(req.method!=='POST'||req.headers.origin||!['127.0.0.1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress))return deny();
  let auth;
  try{
    auth=JSON.parse(fs.readFileSync('/ops-config/manual-contact.json'));
    const token=req.headers.authorization?.match(/^Bearer ([0-9a-f]{64})$/)?.[1];
    if(!token||!timingSafeEqual(Buffer.from(sha(token),'hex'),Buffer.from(auth.relay_sha256,'hex'))||
       req.headers['x-contact-actor']!==auth.actor)return deny();
  }catch{return deny();}
  try{
    const input=await body(req);
    const result=change(input,auth.actor,{load,save,paused:()=>config()?.enabled!==true &&
      !JSON.parse(fs.readFileSync('/ops-state/ownership.json')).roles.includes('registration')});
    return reply(res,200,{ok:true,result});
  }catch{return reply(res,409,{ok:false,error:'manual_review_required'});}
}
