"""Install only on the verified Dell runtime with the confirmation read retry."""
import hashlib

BASE='407584b587c2dd240a6cfc87cac614b25b7efd07f5e3cf253b68b2c6db6ed540'
ANCHOR="      if(stage==='record-error'){"
HANDLER="""      if(stage==='fixed-return-next'||stage==='fixed-return'){
        const cfg=readManualConfig(individualConfigFile);
        const allowed=()=>n8nStageSendEnabled&&cfg?.enabled===true&&cfg.instance===instance&&
          instance==='jp-protese-atendimento'&&cfg.evolution_url==='http://owner.invalid'&&
          sendAuthorizationUntil(cfg)>Date.now()&&reactivation.businessTime();
        const h={load,save,businessTime:allowed,dispatchReady:e=>dispatchSyncProof(load(),e,true),
          reserve:reserveStateSendBudget,finish:finishSendBudget,
          held:(s,n)=>contactBlocked(s,n)||(s.delay_support_cases||[]).some(c=>sameDestination(c.destination_e164,n)&&['open','claimed','answered'].includes(c.status))||
            (s.jp_reply_tasks||[]).some(t=>sameDestination(t.destination_e164,n)&&['human','waiting_team','delivery_unknown'].includes(t.status))||
            (s.reminders||[]).some(r=>sameDestination(r.destination_e164,n)&&['paused_customer_request','paused_support'].includes(r.status)),
          verifyLine:async()=>{
            const get=async p=>{const r=await fetch(cfg.evolution_url+p,{headers:{apikey:cfg.token},signal:AbortSignal.timeout(4000)});
              if(!r.ok)throw Error('fixed_return_line_unavailable');return r.json()};
            const conn=await get('/instance/connectionState/'+instance),list=await get('/instance/fetchInstances');
            const lines=list.filter(l=>(l.name||l.instanceName)===instance);
            if(conn.instance?.state!=='open'||lines.length!==1||!sameDestination(lines[0].ownerJid,'+5565992771377'))throw Error('fixed_return_line_changed');
          },
          deliver:async(number,text)=>{
            const r=await fetch(cfg.evolution_url+'/message/sendText/'+instance,{method:'POST',redirect:'error',
              headers:{'Content-Type':'application/json'},body:JSON.stringify({number,text}),signal:AbortSignal.timeout(15000)});
            if(r.status!==201)return null;const v=await r.json();
            return v.status==='provider_accepted'&&v.key?.fromMe===true?v.key.id:null;
          }};
        const value=await fixedReturnStage(h,stage,identity,req.headers['x-jp-node-id']);
        return reply(res,200,value);
      }
"""

def transform(raw):
    if hashlib.sha256(raw).hexdigest()!=BASE:
        raise ValueError('native_runtime_changed')
    s=raw.decode()
    if s.count(ANCHOR)!=1:
        raise ValueError('native_stage_changed')
    if s.startswith('#!'):
        prefix,s=s.split('\n',1)
    else:
        prefix=''
    return (prefix+"\nimport {fixedReturnStage} from '/ops-app/fixed_return_stage.mjs';\n"+
            s.replace(ANCHOR,HANDLER+ANCHOR,1)).encode()
