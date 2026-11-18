import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {records} from './dispatch.mjs';

// Only a root-owned assertion from the receipt observer can update the display.
const path='/ops-config/read-grants/central-receipt.json';
const hash=v=>createHash('sha256').update(v).digest('hex');
export function loadReceipt(){
  const stat=fs.lstatSync(path);
  if(!stat.isFile()||stat.uid!==0||(stat.mode&0o777)!==0o440||stat.size>1800)throw Error('receipt_file');
  return JSON.parse(fs.readFileSync(path,'utf8'));
}

export function projectReceipt(h, record, p){
  if(!/^[a-f0-9]{64}$/.test(record)||p.record!==record||!['delivered','read'].includes(p.kind)||
     !/^\d{4}-\d\d-\d\dT.*Z$/.test(p.at)||!Number.isFinite(Date.parse(p.at)))throw Error('receipt_proof');
  const state=h.load();
  const physical=records(state).filter(x=>x.id===p.attempt&&x.event===p.source);
  const rows=(state.individual_dispatches||[]).filter(x=>x.message_id===p.native);
  if(physical.length!==1||!rows.length)return {applied:false};
  if(!rows.every(x=>x.status==='provider_accepted'&&x.initial_plan_id&&
     String(x.destination_e164).replace(/^\+/,'')===physical[0].number&&
     hash(JSON.stringify(['central-attempt-v1','visit',x.visit_attempt_id||x.attempt_id||x.outbound_budget_reservation_id,0]))===p.attempt&&
     x.content_sha256===physical[0].textHash&&Date.parse(x.attempted_at)<=Date.parse(p.at)))return {applied:false};
  const planIds=new Set(rows.map(x=>x.initial_plan_id));
  if(planIds.size!==1||hash(JSON.stringify(['central-event-v1','visit',rows[0].initial_plan_id]))!==p.source)return {applied:false};
  const plans=(state.initial_confirmations||[]).filter(x=>planIds.has(x.plan_id));
  if(plans.length!==1||plans[0].status!=='provider_accepted')return {applied:false};
  let changed=false;
  for(const row of [...rows,...plans]){
    const update=(k,v)=>{if(row[k]!==v){row[k]=v;changed=true;}};
    update('delivery_confirmed',true);
    if(!row.delivered_at||Date.parse(p.at)<Date.parse(row.delivered_at))update('delivered_at',p.at);
    if(p.kind==='read'&&!row.read_at)update('read_at',p.at);
    update('delivery_status',row.read_at?'read':'delivered');
    if(p.kind==='read'||!row.delivery_receipt_id)update('delivery_receipt_id',record);
  }
  if(changed){state.version++;h.save(state);}
  return {applied:true,changed};
}
