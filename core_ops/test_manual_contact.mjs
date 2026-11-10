import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {change} from './manual_contact.mjs';

const actor='chatwoot/fixture/individual';
const row={request_id:randomUUID(),task_id:'fixture-task',client_code:'1234',destination_e164:'+5511999990000',source_message_id:'fake-event',
  dispatch:{status:'provider_accepted',message_id:'fake-event'},status:'pending'};
const task={...row,name:'Nome de Exemplo',previous_name:null,name_version:createHash('sha256').update('Nome de Exemplo').digest('hex'),
  reason_codes:[],write_attempts:0,consumer_verified:false};
export const sample=()=>({version:1,contact_registration_requests:[{...row}],contact_registration_tasks:[{...task}]});
const binding={request:row.request_id,task:row.task_id,client:row.client_code,phone:row.destination_e164,source:row.source_message_id,version:task.name_version,authorized:true};
export async function exercise(api,underlying,reset){
  const inspect=()=>api({request:row.request_id,action:'inspect'});
  const claim=()=>api({...binding,action:'claim',before:{source:'device',phone:binding.phone,name:null,reference:'fixture/initial',at:new Date().toISOString()}});
  const evidence=source=>({source,reference:'fixture/'+source,phone:binding.phone,name:task.name,at:new Date().toISOString()});
  const closed=()=>api({...binding,action:'close',web:evidence('web'),device:evidence('device')});
  assert.equal((await inspect()).status,'pending');
  await assert.rejects(()=>api({...binding,action:'claim',client:'5678'}));
  assert.equal((await claim()).status,'human_handling');assert.equal((await claim()).status,'human_handling');
  await assert.rejects(()=>api({...binding,action:'close',web:evidence('web')}));
  await assert.rejects(()=>api({...binding,action:'close',web:evidence('web'),device:{...evidence('device'),name:'Outra pessoa'}}));
  assert.equal((await inspect()).consumer_verified,false);
  assert.equal((await closed()).consumer_verified,true);assert.equal((await inspect()).status,'confirmed');
  const state=await underlying();assert.equal(state.contact_registration_tasks[0].write_attempts,0);
  assert.equal(state.contact_registration_requests[0].request_id,row.request_id);assert.equal(state.contact_registration_requests[0].status,'confirmed');
  const uncertain=sample();uncertain.contact_registration_tasks[0].status='unknown';uncertain.contact_registration_tasks[0].write_attempts=1;
  await reset(uncertain);assert.equal((await claim()).mode,'observe_only');
  await assert.rejects(()=>api({...binding,action:'close',web:evidence('web')}));
  assert.equal((await inspect()).status,'unknown');assert.equal((await (underlying())).contact_registration_tasks[0].write_attempts,1);
  return {case_id:row.request_id,task_id:row.task_id,closed_same_request:true,unknown_retained:true,bad_customer_denied:true,
    automatic_writes:0,external_contact:0,missing_or_mismatched_evidence_held:true};
}
if(import.meta.url===new URL('file://'+process.argv[1]).href){
  let s=sample();const deps={load:()=>structuredClone(s),save:v=>{s=structuredClone(v);},paused:()=>true};
  const api=async p=>change(p,actor,deps);
  const result=await exercise(api,async()=>s,async v=>{s=v;});
  assert.throws(()=>change({...binding,action:'claim'},'other',deps));
  assert.throws(()=>change({...binding,action:'claim'},actor,{...deps,paused:()=>false}));
  console.log(JSON.stringify(result));
}
