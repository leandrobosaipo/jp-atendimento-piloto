import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {executeRescheduleFeedback} from './reply_reschedule_feedback_executor.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex'),key=sha('link'),id=sha(JSON.stringify(['jp-same-day-move-feedback-v1','task',key,'1','2030-01-02','11:00']));
let s={version:1,dell_reply_links:{[key]:{reschedule:{state:'saved',task_id:'task',appointment_id:'1',client:'2',phone:'+5565999999999',slot:{date:'2030-01-02',time:'11:00',professional_code:'p',service_code:'s'},readback:{}}}},jp_reply_attempts:[{task_id:'task',kind:'move',status:'saved'}],jp_reply_ack_outbox:[{id,link_key:key,appointment_ids:['1'],destination_e164:'+5565999999999',inbound_message_id:'in',kind:'reschedule',text:'Ficou combinado para *11:00*.',state:'queued',reschedule_task_id:'task'}]};
const h={instance:'jp',load:()=>s,save:v=>s=v,read:async()=>({appointment_id:'1',client_code:'2',professional_code:'p',service_code:'s',start:'2030-01-02T11:00:00-04:00',status:'1'})};let sent=0;
assert.deepEqual(await executeRescheduleFeedback(h,async()=>{sent++;return new Response(JSON.stringify({status:'provider_accepted',key:{fromMe:true,id:'out'}}),{status:201});}),{status:'provider_accepted'});assert.equal(sent,1);assert.equal(s.jp_reply_ack_outbox[0].state,'provider_accepted');assert.equal((await executeRescheduleFeedback(h,async()=>{throw Error('no resend')})).status,'idle');
console.log('reschedule feedback: exact readback, one owner outbox send, no resend PASS');
