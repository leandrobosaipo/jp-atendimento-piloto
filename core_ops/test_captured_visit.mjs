import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {consumeVisit,projectOfficialAcknowledgements,readRescheduleProposal} from './captured_visit.mjs';

const sha=v=>createHash('sha256').update(v).digest('hex');
const date=new Date(Date.now()+86400000).toISOString().slice(0,10);
function fixture(flags=[true], held=false){
  const members=flags.map((_,i)=>({appointment_id:String(i+1),candidate_id:'c'+i,client_code:'22',destination_e164:'+5500000000000',
    appointment_date_iso:date,appointment_date:'day',appointment_time:'12:30',service_code:'3',professional_code:'4'}));
  const c=members[0],d={appointment_id:c.appointment_id,destination_e164:c.destination_e164,message_id:'fixture-notice',content_sha256:'x',
    review_id:'r',revision:1,conversation_snapshot:{...c,text_sha256:'x'},visit_group:members.length>1?{appointment_ids:members.map(m=>m.appointment_id)}:null};
  const state={version:1,individual_dispatches:[d],reviews:[{...d,...c}],messages:[]};
  const proofs=m=>({appointment_id:m.appointment_id,confirmed:flags[members.indexOf(m)],status:'1',checked_at:new Date().toISOString()});
  return {state,h:{target:(s,e)=>!e.quoted_message_id||e.quoted_message_id===d.message_id?{candidate:c,dispatch:d,members}:null,
    window:()=>true,blocked:()=>held,support:()=>false,feedback:()=>null,load:()=>state,save:()=>{},snapshot:m=>m,
    read:async m=>proofs(m),instance:'fixture'},e:{mid:'fixture-response',record:sha('origin'),content:sha('emoji'),at:new Date().toISOString(),providerTime:true,
    pn:sha('5500000000000@s.whatsapp.net'),delivered:[d.message_id],quote:d.message_id,kind:'reaction',text:'👍',disposition:'possible_confirmation'}};
}
for(const [flags,expected] of [[[false],'awaiting_official'],[[true,false],'awaiting_official'],[[true,true],'official_confirmed']]){
  const {h,state,e}=fixture(flags);assert.equal((await consumeVisit(e,h)).status,expected);
  assert.equal(state.jp_responses.length,flags.length);await consumeVisit(e,h);assert.equal(state.jp_responses.length,flags.length);
}
for(const change of [{quote:'different'},{delivered:[]},{disposition:'removed'},{intervened:true},{conflict:true},{providerTime:false}]){
  const {h,e}=fixture();assert.notEqual((await consumeVisit({...e,...change},h)).status,'official_confirmed');
}
{
 const {h,e,state}=fixture([true],true);const result=await consumeVisit(e,h,true);
 assert.equal(result.status,'human_hold');assert.equal(state.version,1);
}
{
 const {h,e,state}=fixture();await consumeVisit(e,h);
 await consumeVisit({...e,mid:'another',content:sha('removed'),disposition:'removed'},h);
 assert.equal(Object.values(state.dell_reply_links).at(-1).status,'human_review');
}
{
  const {h,e,state}=fixture([true]);
  await consumeVisit(e,h);
  const cutoff=new Date(Date.now()-1000).toISOString();
  assert.deepEqual(projectOfficialAcknowledgements(state,cutoff),{created:1,queued:1});
  const [ack]=state.jp_reply_ack_outbox;
  assert.equal(ack.kind,'emoji');assert.equal(ack.text,'👍');
  assert.equal(ack.source_at,e.at);
  assert.equal(Object.values(state.dell_reply_links)[0].ackCandidates[0].appointment_id,'1');
  assert.equal(ack.destination_e164,'+5500000000000');
  assert.deepEqual(projectOfficialAcknowledgements(state,cutoff),{created:0,queued:1});
  assert.deepEqual(projectOfficialAcknowledgements(state,new Date(Date.now()+1000).toISOString()),{created:0,queued:1});
}
{
  const {h,e,state}=fixture([true]);
  await consumeVisit(e,h);
  const link=Object.values(state.dell_reply_links)[0];
  link.at='2020-01-01T00:00:00.000Z';link.checkedAt=new Date().toISOString();
  assert.deepEqual(projectOfficialAcknowledgements(state,'2026-11-12T00:00:00.000Z'),{created:0,queued:0});
}
assert.throws(()=>projectOfficialAcknowledgements({},'not-a-date'),/ack_cutoff_required/);
{
 const mid='reschedule-mid',content=sha('reschedule-content'),key=sha(JSON.stringify([mid,content]));
 const state={dell_reply_links:{[key]:{reschedule:{state:'grant_required',message_id:mid,fingerprint:content,task_id:'t'}}}};
 const h={load:()=>state};assert.equal(readRescheduleProposal({mid,content},h).status,'grant_required');
 assert.equal(readRescheduleProposal({mid:'later',content},h).status,'idle');
 assert.deepEqual(state.dell_reply_links[key].reschedule,{state:'grant_required',message_id:mid,fingerprint:content,task_id:'t'});
}
console.log('captured_visit: association, official readback, hold, deduplication and preview passed');
