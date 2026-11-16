import fs from 'node:fs';
import {createHash} from 'node:crypto';

const sha=value=>createHash('sha256').update(value).digest('hex');
const readProof=()=>JSON.parse(fs.readFileSync('/ops-config/read-grants/public-requests.json','utf8'));
const digits=value=>String(value??'').replace(/\D/g,'');

export function nextRequest(h, read=readProof, now=Date.now()){
  let proof;
  try{proof=read();}catch{return null;}
  if(!Number.isFinite(proof.at)||now<proof.at*1000||now-proof.at*1000>55000)return null;
  const row=proof.items?.[0];
  if(!row||!/^55[1-9]\d{9,10}$/.test(row.number)||typeof row.answer!=='string'||!row.answer.trim()||row.answer.length>300)return null;
  const s=h.load(),key=sha(JSON.stringify([row.mid,row.fp])),link=s.dell_reply_links?.[key];
  if(!link||link.mid!==row.mid||link.record!==row.record||!['identity_review','visit_review'].includes(link.status)||link.appointments?.length||link.notice)return null;
  const pn=row.number,phones=new Set([pn,pn.length===12?pn.slice(0,4)+'9'+pn.slice(4):pn.slice(0,4)+pn.slice(5)]);
  const same=n=>phones.has(digits(n));
  if([...phones].some(n=>h.blocked(s,'+'+n)))return null;
  if((s.jp_reply_attempts||[]).some(a=>a.source_message_id===row.mid))return null;
  if(!row.exception && (s.jp_reply_tasks||[]).some(t=>same(t.destination_e164)&&['human','waiting_team','delivery_unknown'].includes(t.status)))return null;
  if(!row.exception && (s.reminders||[]).some(r=>same(r.destination_e164)&&['paused_support','paused_customer_request'].includes(r.status)))return null;
  if(row.exception && (row.seq!==488||row.answer!=='Para qual dia ou período você gostaria de mudar a manutenção?'))return null;
  return row;
}

export async function answerRequest(h,fetchImpl,read=readProof){
  const row=nextRequest(h,read);
  if(!row)return null;
  const state=h.load(),id=sha(JSON.stringify(['jp-new-request-v1',row.mid]));
  const attempt={kind:'send',attempt_id:id,source_message_id:row.mid,destination_e164:'+'+row.number,text:row.answer,
    status:'delivery_unknown',attempted_at:new Date().toISOString(),incoming_sequence:row.seq};
  (state.jp_reply_attempts||=[]).push(attempt);state.version++;h.save(state);
  try{
    const response=await fetchImpl('http://owner.invalid/message/sendText/'+h.instance,{method:'POST',redirect:'error',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({number:row.number,text:row.answer})});
    const body=await response.json();
    if(response.status===201&&body.status==='provider_accepted'&&body.key?.fromMe===true&&typeof body.key.id==='string'){
      const current=h.load(),saved=current.jp_reply_attempts.find(a=>a.attempt_id===id);
      saved.status='provider_accepted';saved.message_id=body.key.id;current.version++;h.save(current);
      return {status:'provider_accepted'};
    }
  }catch{}
  return {status:'delivery_unknown'};
}
