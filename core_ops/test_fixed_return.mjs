import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { sendFixedReturn } from './fixed_return.mjs';
import { reviewDue } from './fixed_return_stage.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
const rows = Array.from({length:11},(_,i)=>({client:String(i),approval_key:hash(String(i)),
  options:[[24,'14:00'],[25,'15:00']],text:'Convite fictício '+i}));
const raw = JSON.stringify({items:rows}), now=1800000000000;
const lag={status:'inbox_catching_up',checked:now,retryAfter:now+3600000};
assert.equal(reviewDue(lag,now+59999),false);
assert.equal(reviewDue(lag,now+60000),true);
for(const status of ['identity_conflict','human_handling','previous_uncertain','customer_request_pending']){
  assert.equal(reviewDue({...lag,status},now+60000),false);
}
const scope={raw,digest:hash(raw),opens:now-60000,closes:now+60000};
const row=rows[0];
const verified={approval:scope.digest,execution:'41',key:row.approval_key,status:'matched',checked:now,
  number:'5511999990000',client:row.client,options:row.options,textHash:hash(row.text)};
let state, ticket, calls, hold, message;
const h={scope:()=>scope,ticket:()=>ticket,now:()=>now,dispatchReady:()=>true,businessTime:()=>true,
  load:()=>structuredClone(state),save:s=>{state=structuredClone(s);},held:()=>hold,
  reserve:(_s,_t,r)=>r.reservation_id,finish:()=>{},deliver:async(n,text)=>{
    calls++;assert.equal(n,verified.number);assert.equal(text,row.text);
    assert.equal(state.fixed_return_dispatches[0].status,'delivery_unknown');
    if(!message)throw Error('fixture_timeout');return message;
  }};
function reset(){state={version:0};ticket=structuredClone(verified);calls=0;hold=false;message='fixture123';}
for(const change of [{checked:now-16000},{execution:'40'},{status:'booked'},{options:[]},
                     {client:'2'},{textHash:hash('outro texto')},{approval:'unapproved'}]){
  reset();Object.assign(ticket,change);
  assert.equal(await sendFixedReturn(h,'41'),'held');assert.equal(calls,0);
}
reset();hold=true;assert.equal(await sendFixedReturn(h,'41'),'held');assert.equal(calls,0);
for(const accepted of [true,false]){
  reset();if(!accepted)message=null;
  const outcomes=await Promise.all([sendFixedReturn(h,'41'),sendFixedReturn(h,'41')]);
  assert.deepEqual(outcomes,[accepted?'provider_accepted':'delivery_unknown','already_attempted']);
  ticket.execution='42';assert.equal(await sendFixedReturn(h,'42'),'already_attempted');
  assert.equal(calls,1);
}
