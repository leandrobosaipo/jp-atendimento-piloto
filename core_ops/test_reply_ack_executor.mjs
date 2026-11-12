import assert from 'node:assert/strict';
import {executeAcknowledgements} from './reply_ack_executor.mjs';

const id='a'.repeat(64), link='b'.repeat(64), cutoff='2026-11-12T12:00:00.000Z';
function fixture(){
  const candidate={appointment_id:'1',destination_e164:'+5565999999999'};
  const state={version:1,individual_dispatches:[{...candidate,conversation_snapshot:candidate}],dell_reply_links:{[link]:{status:'official_confirmed',appointments:['1'],mid:'inbound-1'}},jp_responses:[{appointment_id:'1',destination_e164:'+5565999999999',message_id:'inbound-1',official_update:{status:'confirmed'}}],jp_reply_ack_outbox:[{id,link_key:link,appointment_ids:['1'],destination_e164:'+5565999999999',inbound_message_id:'inbound-1',kind:'emoji',text:'👍',state:'queued',source_at:'2026-11-12T12:01:00.000Z'}]};
  return {state,h:{instance:'jp-protese-atendimento',load:()=>state,save:()=>{},read:async row=>({appointment_id:row.appointment_id,confirmed:true,status:'1'}) }};
}
{
  const {state,h}=fixture();let calls=0;
  const fetchImpl=async(url,opts)=>{calls++;assert.equal(url,'http://owner.invalid/message/sendText/jp-protese-atendimento');assert.deepEqual(JSON.parse(opts.body),{number:'5565999999999',text:'👍'});return new Response(JSON.stringify({status:'provider_accepted',key:{fromMe:true,id:'native-1'}}),{status:201});};
  assert.deepEqual(await executeAcknowledgements(h,cutoff,fetchImpl),{status:'provider_accepted',projected:0});
  assert.equal(calls,1);assert.equal(state.jp_reply_ack_outbox[0].state,'provider_accepted');assert.equal(state.jp_reply_attempts[0].status,'provider_accepted');
  assert.deepEqual(await executeAcknowledgements(h,cutoff,fetchImpl),{status:'accepted',projected:0});assert.equal(calls,1);
}
{
  const {state,h}=fixture();state.jp_reply_ack_outbox[0].source_at='2020-01-01T00:00:00.000Z';
  assert.deepEqual(await executeAcknowledgements(h,cutoff,async()=>{throw Error('must_not_send')}),{status:'idle',projected:0});
}
console.log('reply_ack_executor: binds one new acknowledgement and retains uncertain or historical work');
