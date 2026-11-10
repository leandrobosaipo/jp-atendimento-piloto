import assert from "node:assert/strict";
import {createHash} from "node:crypto";
const sha=s=>createHash("sha256").update(s).digest("hex");
const stages=name=>"/api/n8n/stages/"+name,contact=name=>"/api/contact-registration/"+name;
export const scenarios={
  batch:[stages("before-dispatch"),"/api/confirmation-batch/run"],
  "contact-queue":[stages("contact-outbox"),contact("claim"),contact("result")],
  "contact-hook":[contact("claim"),contact("update"),contact("result")],
  "brand-paused":[stages("brand-due")],
  "brand-priority":[stages("brand-due"),stages("before-dispatch"),stages("brand-send")]
};
const stamp="2026-11-10T14:00:00Z",expires="2026-11-10T14:10:00Z";
export function seed(plan,state,write,dir){
  const {scenario:s,workflow,capability:cap}=plan;
  assert(Object.hasOwn(scenarios,s)&&typeof cap==="string"&&cap.length>=32);
  assert.deepEqual(plan.nodes.map(n=>n.path),scenarios[s]);
  const env={JP_N8N_BATCH_WORKFLOW_ID:workflow,JP_BRAND_N8N_WORKFLOW_ID:s.startsWith("brand")?workflow:"fixture-unused"};
  if(s==="batch"){
    const snapshot={candidate_id:"jp-semana-9001",client_code:"1",kind:"appointment_confirmation",appointment_id:"9001",appointment_date:"12/11/2026",appointment_time:"11:00",service:"Manutenção Prótese Capilar",professional:"Teste",destination_e164:null};
    const text="Texto de prova, sem destino.";
    state.jp_jobs.push({job_id:"fixture-batch",type:"confirmation_batch",operator:"fixture",status:"queued",started_at:stamp,capability_hash:sha(cap),capability_expires_at:expires,capability_consumed_at:null,
      items:[{candidate_id:snapshot.candidate_id,appointment_id:snapshot.appointment_id,destination_e164:null,text,text_sha256:sha(text),snapshot,status:"queued"}]});
  }
  if(s.startsWith("contact")){
    const row={request_id:"fixture-contact",client_code:"1",source_message_id:"fake-accepted",destination_e164:"+5500000000000",status:"pending",created_at:stamp,capability:cap,capability_hash:sha(cap),capability_expires_at:expires,task_id:"fixture-task"};
    state.contact_registration_requests=[row];
    state.contact_registration_tasks=[{task_id:row.task_id,request_id:row.request_id,client_code:row.client_code,destination_e164:row.destination_e164,
      status:s==="contact-hook"?"pending":"unknown",pilot_seed:true,write_attempts:s==="contact-hook"?0:1,previous_name:"Synthetic",source_proof:{source:"whatsapp_contact_screen_human",observedAt:stamp,name:"Synthetic",phone:"5500000000000"}}];
    write("contact-config.json",{enabled:false,instance:"fixture",pilot_client_code:"1",pilot_approval:{manual_name_replacement_approved:true}});
    env.JP_CONTACT_REGISTRATION_CONFIG_FILE=dir+"/contact-config.json";
  }
  if(s.startsWith("brand")){
    const slot="2026-11-10T20:00:00-04:00",m={version:1,campaign_id:"fixture",instance:"fixture",maximum_attempted_recipients:50,caption:"Synthetic",
      asset:{asset_id:"JP-EDU-C02",mime:"video/mp4",filename:"fixture.mp4",sha256:"a".repeat(64)},context_reviewed_at:stamp,
      recipients:[{entry_id:"fixture",client_code:"1",destination_sha256:"b".repeat(64),send_at:slot,expires_at:"2026-11-10T20:05:00-04:00",evidence:{appointment_id:"1",client_code:"1",date:"2026-11-01",time:"10:00",service_code:"77",professional_code:"88"}}]};
    const hash=sha(JSON.stringify(m));m.recipient_approval={approved:true,source:"synthetic-only",approved_at:stamp,manifest_sha256:hash};m.enabled=s==="brand-priority";
    if(m.enabled)m.activation={approved:true,approved_at:stamp,workflow_id:workflow,manifest_sha256:hash};
    write("brand.json",m);state.reactivation.bookings=[{candidate_id:"fixture-only",status:"saved",registration:{status:"manual_review"}}];
    Object.assign(env,{FIXTURE_BRAND_CLOCK:"1",JP_BRAND_MANIFEST_FILE:dir+"/brand.json",JP_N8N_STAGE_SEND_ENABLED:m.enabled?"1":"0",JP_BRAND_SEND_ENABLED:m.enabled?"1":"0"});
  }
  return env;
}
export function checkRequest(plan,route,body,ex){
  const cap=plan.capability;
  if(route.endsWith("/before-dispatch"))return assert.deepEqual(body,plan.scenario==="batch"?{force:true,job_id:"fixture-batch",capability:cap}:{force:true});
  if(route==="/api/confirmation-batch/run")return assert.deepEqual(body,{execution_id:ex,job_id:"fixture-batch",capability:cap});
  if(route.startsWith("/api/contact-registration/"))return assert.deepEqual(body,{request_id:"fixture-contact",capability:cap});
  assert.deepEqual(body,{});
}
export function checkResult(plan,route,r,obj,state){
  const scenario=plan.scenario;
  if(route==="/api/confirmation-batch/run"){
    const job=state.jp_jobs.find(j=>j.job_id==="fixture-batch");assert(r.status===409&&obj.error==="n8n_send_gate_disabled"&&job.status==="queued"&&!job.capability_consumed_at);
    assert(job.items.length===1&&job.items[0].status==="queued"&&job.items[0].text_sha256===sha(job.items[0].text));
    return {gate:obj.error,batch_unconsumed:true,selected_queued:1};
  }
  assert(r.ok);
  if(route.endsWith("/before-dispatch"))return {};
  if(route.endsWith("/contact-outbox")){assert(obj.items.length===1&&obj.items[0].capability===plan.capability);return {pending:1};}
  if(route.endsWith("/claim")){assert.equal(obj.ready,scenario==="contact-hook");assert.equal(obj.status,scenario==="contact-hook"?"pending":"unknown");return {ready:obj.ready,status:obj.status};}
  if(route.endsWith("/update")){assert.equal(obj.status,"paused");return {status:obj.status};}
  if(route.endsWith("/result")){assert(obj.ok===true&&obj.consumer_verified===false&&obj.status===(scenario==="contact-hook"?"pending":"unknown"));return {status:obj.status,consumer_verified:false,write_attempts:state.contact_registration_tasks[0].write_attempts};}
  if(route.endsWith("/brand-due")){assert.deepEqual(obj,scenario==="brand-paused"?[]:[{pending:true}]);return {pending:obj.length};}
  assert(route.endsWith("/brand-send")&&obj.ok===true&&obj.reason==="commercial_work_has_priority"&&obj.attempted===0&&obj.message_sent===false&&obj.official_agenda_mutated===false);
  return {status:"held",reason:obj.reason,attempted:0};
}
