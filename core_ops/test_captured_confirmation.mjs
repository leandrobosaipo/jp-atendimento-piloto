import assert from 'node:assert/strict';
import {consumeVisit} from './captured_visit.mjs';
import {reconcileOne,awaitingSessionRecovery} from './captured_confirmation.mjs';
import {createHash} from 'node:crypto';
const checked=Date.parse('2026-11-12T20:00:00Z');
const stale={dispatch_sync:{status:'failed',job_id:'a',prepared_at:new Date(checked).toISOString()},
  jp_jobs:[{job_id:'a',error:'appbarber_session_invalid'}]};
assert.equal(awaitingSessionRecovery(stale,checked+60000,checked-1000),true);
assert.equal(awaitingSessionRecovery(stale,checked+60000,checked+1000),false);
assert.equal(awaitingSessionRecovery(stale,checked+900000,checked-1000),false);
const hash=s=>createHash('sha256').update(s).digest('hex');
const day=new Date(Date.now()+86400000).toISOString().slice(0,10);
async function scenario({confirmed=[],held=false,unknown=false,deny=false}={}){
  const members=['11','22'].map((id,i)=>({appointment_id:id,client_code:'3',destination_e164:'+5511999999999',appointment_time:i?'12:00':'11:00',
    appointment_date_iso:day,appointment_date:'future',service_code:id,professional_code:'4'}));
  const d={message_id:'notice',destination_e164:members[0].destination_e164,content_sha256:'z',conversation_snapshot:{client_code:'3',text_sha256:'z'},visit_group:{appointment_ids:['11','22']}};
  const st={individual_dispatches:[d],version:0};let paused=false;const writes=[],attempted=new Set();
  const h={target:()=>({members,candidate:members[0],dispatch:d}),window:()=>true,support:()=>false,blocked:()=>paused,
    feedback:()=>null,load:()=>st,save:()=>{},snapshot:c=>({...c}),instance:'fixture',read:async c=>({
      ...c,confirmed:confirmed.includes(c.appointment_id),status:'1',start:day+'T'+c.appointment_time,item_code:c.appointment_id+'9'})};
  const event={mid:'answer',record:hash('source'),content:hash('response'),at:new Date().toISOString(),providerTime:true,
    pn:hash('5511999999999@s.whatsapp.net'),kind:'emoji_text',text:'👍',disposition:'possible_confirmation',delivered:['notice']};
  await consumeVisit(event,h);const [link]=Object.values(st.dell_reply_links);paused=held;
  const authority={context:{role:'reply'},permitted:()=>!deny,attempted:g=>attempted.has(g.reference),
    confirm:async g=>{attempted.add(g.reference);writes.push(g.reference);if(unknown)throw Error('disconnect');confirmed.push(g.reference);}};
  const first=await reconcileOne(link,h,authority);
  await reconcileOne(link,h,authority);
  return {first:first.status,writes};
}
assert.deepEqual(await scenario(),{first:'official_confirmed',writes:['11','22']});
assert.deepEqual(await scenario({confirmed:['11']}),{first:'official_confirmed',writes:['22']});
assert.deepEqual(await scenario({held:true}),{first:'held',writes:[]});
assert.deepEqual(await scenario({deny:true}),{first:'held',writes:[]});
assert.deepEqual(await scenario({unknown:true}),{first:'write_unknown',writes:['11']});
console.log('Confirmed grouped visits, already confirmed item, human pause, denied and ambiguous write.');
