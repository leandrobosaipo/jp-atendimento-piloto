import {AsyncLocalStorage} from "node:async_hooks";
import {createHash} from "node:crypto";
import http from "node:http";
import fs from "node:fs";
const context=new AsyncLocalStorage(),sha=v=>createHash("sha256").update(v).digest("hex");
const digits=v=>String(v||"").replace(/\D/g,"");
const key=(source,id,part)=>sha(JSON.stringify(["central-attempt-v1",source,id,part]));
export function records(s){
  const rows=[];
  function add(source,a,event,attempt,destination){
    if(!event||!attempt||!/^\+?[1-9]\d{7,14}$/.test(destination||"")||!a?.attempted_at)return;
    const parts=a.parts?.length?a.parts:[a];
    for(const [i,p] of parts.entries())if(p.attempted_at)rows.push({id:key(source,attempt,p.index??i),event:sha(JSON.stringify(["central-event-v1",source,event])),number:digits(destination),status:p.status,
      textHash:p.content_sha256||p.text_sha256||(p.text?sha(p.text):a.content_sha256||a.text_sha256),asset:a.asset_sha256,caption:a.caption_sha256});
  }
  const groups=new Map();
  for(const a of s.individual_dispatches||[]){
    if(a.visit_attempt_id&&a.visit_group){
      const members=groups.get(a.visit_attempt_id)||[];members.push(a);groups.set(a.visit_attempt_id,members);
    }else add("visit",a,a.initial_plan_id||a.review_id,a.attempt_id||a.outbound_budget_reservation_id,a.destination_e164);
  }
  for(const [attempt,members] of groups){
    const a=members[0],g=a.visit_group,ids=g.appointment_ids;
    if(!g.group_id||!/^[a-f0-9]{64}$/.test(g.group_sha256||"")||!Array.isArray(ids)||!ids.length||ids.length!==members.length)continue;
    const actual=new Set(members.map(m=>String(m.appointment_id)));
    if(actual.size!==members.length||new Set(ids.map(String)).size!==members.length||!ids.every(id=>actual.has(String(id))))continue;
    if(!members.every(m=>m.review_id===a.review_id&&m.destination_e164===a.destination_e164&&m.content_sha256===a.content_sha256&&m.status===a.status&&
      m.attempted_at===a.attempted_at&&m.outbound_budget_reservation_id===a.outbound_budget_reservation_id&&JSON.stringify(m.visit_group)===JSON.stringify(g)))continue;
    add("visit",a,a.initial_plan_id||a.review_id,attempt,a.destination_e164);
  }
  for(const a of s.manual_dispatches||[])add("manual",a,a.review_id,a.attempt_id||a.outbound_budget_reservation_id,a.destination_e164);
  for(const a of s.reminders||[])add("reminder",a,a.reminder_id,a.outbound_budget_reservation_id||a.reminder_id,a.destination_e164);
  for(const a of s.jp_reply_attempts||[])if(a.kind==="send")add("reply",a,a.source_message_id,a.attempt_id,a.destination_e164);
  for(const a of s.brand_education?.entries||[])add("brand",a.attempt,a.entry_id,a.attempt?.attempt_id,a.destination_e164);
  for(const a of s.reactivation?.requests||[])for(const d of a.dispatches||[])add("reactivation",d,a.request_id||a.candidate_id,d.attempt_id||d.outbound_budget_reservation_id,a.destination_e164);
  return rows;
}
export function resolveAttempt(before,current,operation,payload){
  const number=String(payload.number||"");
  if(!/^[1-9]\d{7,14}$/.test(number))throw Error("individual_destination_required");
  const hits=current.filter(r=>r.status==="delivery_unknown"&&!before.has(r.id)&&r.number===digits(number)&&
    (operation==="sendText"?typeof payload.text==="string"&&sha(payload.text)===r.textHash:
      typeof payload.media==="string"&&payload.media.length<=22500000&&r.asset===sha(Buffer.from(payload.media,"base64"))&&r.caption===sha(payload.caption||"")));
  if(hits.length!==1)throw Error("unbound_native_attempt");return hits[0];
}
function post(socketPath,input,signal){
  const data=JSON.stringify(input);
  return new Promise((resolve,reject)=>{
    const q=http.request({socketPath,method:"POST",path:"/deliver",signal,headers:{"Content-Type":"application/json","Content-Length":Buffer.byteLength(data)}},r=>{
      const chunks=[];let size=0;r.on("data",b=>{if((size+=b.length)>16384)r.destroy(Error("response_limit"));else chunks.push(b);});
      r.on("error",reject);r.on("end",()=>{try{const o=JSON.parse(Buffer.concat(chunks));const accepted=r.statusCode===201&&o.status==="provider_accepted"&&o.key?.fromMe===true&&/^[\w-]{6,160}$/.test(o.key.id||"")&&o.key.remoteJid===digits(input.payload.number)+"@s.whatsapp.net";
        resolve(new Response(JSON.stringify(accepted?o:{status:"delivery_unknown"}),{status:accepted?201:409,headers:{"Content-Type":"application/json"}}));}catch(e){reject(e);}});});
    q.on("error",reject);q.end(data);
  });
}
let loadState;
export function setStateReader(reader){loadState=reader;}
export function scopedServer(handler){
  return http.createServer((req,res)=>{
    const ctx={workflow:req.headers["x-jp-n8n-workflow-id"],execution:req.headers["x-jp-n8n-execution-id"],node:req.headers["x-jp-node-id"],
      epoch:req.headers["x-jp-owner-epoch"],active:true,before:new Set()};
    if(ctx.epoch&&loadState&&/\/(?:send-initial|send-reminders|reply-execute|brand-send|reactivation|run|manual)$/.test(req.url))ctx.before=new Set(records(loadState()).map(r=>r.id));
    res.once("close",()=>{ctx.active=false;});return context.run(ctx,()=>handler(req,res));
  });
}
export async function deliver(url,options,socketPath,instance){
  const ctx=context.getStore(),u=new URL(url),gate=JSON.parse(fs.readFileSync("/ops-state/ownership.json"));
  if(!ctx?.active||gate.mode!=="owned"||ctx.epoch!==gate.epoch||Date.now()>=Date.parse(gate.until)||!socketPath)throw Error("dispatch_held");
  const op=u.pathname.split("/")[2];
  if(u.origin!=="http://owner.invalid"||options.method!=="POST"||!["sendText","sendMedia"].includes(op)||u.pathname!=="/message/"+op+"/"+instance||u.search||u.hash)throw Error("dispatch_route_denied");
  const payload=JSON.parse(options.body),r=resolveAttempt(ctx.before,records(loadState()),op,payload);
  ctx.before.add(r.id);
  const wire={operation:op,instance,workflow:ctx.workflow,execution:ctx.execution,node:ctx.node,source_event_id:r.event,attempt_id:r.id,native_path:u.pathname,payload};
  if(Object.values(payload).some(v=>v!==null&&!["string","boolean","number"].includes(typeof v)))throw Error("non_scalar_payload");
  const digest=sha(JSON.stringify(Object.fromEntries(Object.keys(payload).sort().map(k=>[k,payload[k]]))));
  const dir="/ops-state/dispatch-proofs",fd=fs.openSync(dir+"/"+sha(r.id)+".json","wx",0o600);
  try{fs.writeFileSync(fd,JSON.stringify({epoch:gate.epoch,until:Math.floor(Date.parse(gate.until)/1000),operation:op,instance,workflow:ctx.workflow,execution:ctx.execution,node:ctx.node,source_event_id:r.event,attempt_id:r.id,destination_jid:r.number+"@s.whatsapp.net",payload_sha256:digest}));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  const parent=fs.openSync(dir,"r");try{fs.fsyncSync(parent);}finally{fs.closeSync(parent);}
  return post(socketPath,wire,options.signal);
}
