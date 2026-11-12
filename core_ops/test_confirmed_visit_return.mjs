import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {confirmedVisitReturn} from './confirmed_visit_return.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
const c={appointment_id:'42',kind:'appointment_confirmation',professional_code:'3',client_code:'4',service_code:'5',appointment_date_iso:'2035-01-03',appointment_time:'11:00',destination_e164:'+5500000000000'};
const row={state:'provider_accepted',candidate:c,provider_message_id:'final',authorized_exception:'changed-visit',accepted_at:new Date(Date.now()-20000).toISOString(),appointment_ids:['42'],destination_e164:c.destination_e164};
const e={text:'Combinado',kind:'text',providerTime:true,mid:'reply',content:sha('combinado'),at:new Date().toISOString(),latestDelivered:'final',delivered:['final'],pn:sha('5500000000000@s.whatsapp.net')};
let state={version:1,jp_reply_ack_outbox:[row]},reads=0;
const h={load:()=>structuredClone(state),save:s=>state=s,blocked:()=>false,support:()=>false,read:async()=>{reads++;return {...c,status:'1',confirmed:true,start:'2035-01-03T11:00:00'};}};
for(const change of [{text:'Combinado?'},{intervened:true},{latestDelivered:'old'},{pn:'other'},{quote:'outra'}, {text:'não'}]){
 assert.equal(await confirmedVisitReturn({...e,...change},h),null);
}
assert.equal(reads,0);
assert.equal((await confirmedVisitReturn(e,h)).status,'official_confirmed');
assert.equal(state.jp_responses[0].outbound_message_id,'final');
assert.equal(await confirmedVisitReturn(e,h),null);
assert.equal(state.jp_responses.length,1);
console.log('confirmation after delivered changed visit passed');
