import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const runtime=fs.readFileSync(process.argv[2],'utf8');
const fn=runtime.slice(runtime.indexOf('async function runInitialConfirmationTick('),
  runtime.indexOf('\nasync function runReminderTick('));
const day='2026-11-23';
const plans=Array.from({length:13},(_,i)=>({
  plan_id:String(i),candidate_ids:[String(i)],status:i<7?'blocked':'scheduled',
  blocked_reason:i<6?'contact_needs_human_review':i===6?'presentation_history_uncertain':null
}));
const store={version:1,initial_confirmations:plans,initial_confirmation_source_sync:{day,send_day:day,status:'completed'}};
const attempts=[],reads=new Map();
let window='due';
const ctx=vm.createContext({load:()=>store,save:()=>{},localDay:()=>day,
  initialSettings:()=>({enabled:true}),initialWindow:()=>window,planInitialConfirmations:()=>{},
  dispatchSyncProof:()=>true,initialAttemptGuard:()=>{},digest:v=>v,batchFields:[],
  candidates:()=>({rows:plans.map(p=>({candidate_id:p.plan_id}))}),initialText:()=>'Teste fictício',
  attemptBatchItem:async({initial_plan:plan})=>{
    const id=plan.plan_id,count=reads.get(id)||0;
    reads.set(id,count+1);
    if(id!=='12' && count===0){
      if(id==='7'){const err=new Error('The operation was aborted');err.name='AbortError';throw err;}
      throw Error('agenda_readback_failed');
    }
    attempts.push(id);
    const stored=plans.find(p=>p.plan_id===id);
    stored.attempted_at=new Date().toISOString();
    return id==='8'?'delivery_unknown':'provider_accepted';
  }});
const tick=vm.runInContext(fn+';runInitialConfirmationTick',ctx);
const run=()=>tick({status:'connected'},'123',true,'send-initial');
await run();assert.deepEqual(attempts,['12']);
await run();await run();
assert.equal(attempts.length,6);
assert.equal(new Set(attempts).size,6);
assert.equal(plans.filter(p=>p.status==='blocked').length,7);
assert.equal(plans[8].status,'delivery_unknown');
window='expired';plans[7].status='scheduled';delete plans[7].attempted_at;
await run();assert.equal(attempts.length,6);
console.log('13 simulated plans: 7 holds, 5 read retries, 1 first-pass acceptance, no duplicate or expired dispatch');
