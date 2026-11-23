import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {sendFixedReturn} from './fixed_return.mjs';

const base='/ops-config/fixed-return';
const clock='7Ur9XdO2FiFEnMhY';
const read=f=>JSON.parse(fs.readFileSync(f,'utf8'));
function scope(){
  const cfg=read(base+'/scope.json');
  const raw=fs.readFileSync(base+'/approved.json','utf8');
  if(cfg.workflow!==clock || !/^[a-f0-9]{64}$/.test(cfg.digest) ||
      createHash('sha256').update(raw).digest('hex')!==cfg.digest)throw Error('approval_changed');
  return {...cfg,raw};
}
export async function fixedReturnStage(h,stage,identity,node){
  const cfg=scope(),now=Date.now();
  if(identity.workflowId!==clock||cfg.nodes[stage]!==node)throw Error('unbound_invitation_stage');
  if(cfg.enabled!==true)return [];
  if(now>=cfg.closes && stage==='fixed-return-next'){
    const state=h.load();
    if(state.fixed_return_closed?.approval!==cfg.digest){
      const attempted=new Set((state.fixed_return_dispatches||[]).map(a=>a.request_id));
      const pending=JSON.parse(cfg.raw).items.filter(r=>!attempted.has(r.approval_key));
      state.fixed_return_closed={approval:cfg.digest,at:now,
        held:pending.map(r=>({key:r.approval_key,reason:'window_closed',
          previous:state.fixed_return_reviews?.[r.approval_key]?.status||null}))};
      state.version++;h.save(state);
    }
    return [];
  }
  if(!(cfg.opens<=now&&now<cfg.closes)||!h.businessTime())return [];
  if(stage==='fixed-return-next'){
    const state=h.load(),attempted=new Set((state.fixed_return_dispatches||[]).map(a=>a.request_id));
    const available=JSON.parse(cfg.raw).items.filter(r=>cfg.keys.includes(r.approval_key)&&!attempted.has(r.approval_key)&&
      !(state.fixed_return_reviews?.[r.approval_key]?.retryAfter>now));
    return available.length?[{approval_key:available[Math.floor(now/60000)%available.length].approval_key}]:[];
  }
  const execution=identity.executionId;
  const ticket=()=>read('/ops-config/read-grants/fixed-return/latest.json');
  const current=ticket();
  if(current.execution!==execution||!cfg.keys.includes(current.key))return {status:'held'};
  if(current.status!=='matched'){
    const state=h.load();
    (state.fixed_return_reviews||={})[current.key]={status:current.status,checked:now,retryAfter:now+3600000};
    state.version++;h.save(state);
    return {status:current.status};
  }
  if(cfg.dispatch!==true)return {status:'reviewed_only'};
  await h.verifyLine();
  return {status:await sendFixedReturn({...h,now:Date.now,scope:()=>cfg,ticket},execution)};
}
