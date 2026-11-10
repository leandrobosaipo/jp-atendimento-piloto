import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const inboundRoute="/api/n8n/inbound";
const sha=data=>crypto.createHash("sha256").update(data).digest("hex");
const validId=value=>typeof value==="string"&&/^[a-f0-9]{64}$/.test(value);
const equal=(a,b)=>{const x=Buffer.from(a||""),y=Buffer.from(b||"");return x.length===y.length&&crypto.timingSafeEqual(x,y);};
const shape=(status,body)=>({status,body});
function writeOnce(file,data){
  const fd=fs.openSync(file,"wx",0o600);
  try{fs.writeFileSync(fd,JSON.stringify(data));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  const dir=fs.openSync(path.dirname(file),"r");try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}
}
function allowedMessage(data,instance){
  assert(data&&data.instance===instance&&!Object.hasOwn(data,"historical_review_import"));
  assert(String(data.event||"").toLowerCase().replaceAll("_",".")==="messages.upsert");
  const m=data.data,k=m?.key;
  assert(m&&!Array.isArray(m)&&k?.fromMe===false&&typeof k.id==="string"&&/^[\w-]{6,120}$/.test(k.id));
  assert(typeof k.remoteJid==="string"&&/^[A-Za-z][A-Za-z0-9_.-]{1,80}@lid$/.test(k.remoteJid));
  assert(m.messageTimestamp===1&&Object.keys(m.message||{}).join(",")==="conversation");
  assert(typeof m.message.conversation==="string"&&m.message.conversation.length>0&&m.message.conversation.length<=256);
  return {id:k.id,jidHash:sha(k.remoteJid),textHash:sha(m.message.conversation)};
}
// One immutable record, one native call. A started record without a receipt
// requires reconciliation against the native inbox before any further action.
export async function retainInbound({raw,id,workflow,execution,auth,expectedWorkflow,token,grant,instance,stateDir,find,forward}){
  if(!token||!expectedWorkflow||workflow!==expectedWorkflow||!equal(auth,"Bearer "+token)||!/^\d{1,20}$/.test(execution||""))return shape(403,{ok:false});
  if(!Buffer.isBuffer(raw)||raw.length>16384||!validId(id))return shape(400,{ok:false});
  const digest=sha(raw);
  if(!(Date.now()<Date.parse(grant?.until))||!grant.paths?.includes(inboundRoute)||!Object.hasOwn(grant.records||{},id)||grant.records[id]!==digest)return shape(403,{ok:false});
  let message;
  try{message=allowedMessage(JSON.parse(raw),instance);}catch{return shape(400,{ok:false});}
  const base=path.join(stateDir,"inbound-receipts",id+".json"),done=base+".done";
  if(fs.existsSync(base)){
    const prior=JSON.parse(fs.readFileSync(base));
    if(prior.sha256!==digest||prior.workflow!==workflow)return shape(409,{ok:false,status:"conflict"});
    if(fs.existsSync(done))return shape(200,JSON.parse(fs.readFileSync(done)));
    return shape(409,{ok:false,status:"reconcile_required"});
  }
  if(find(message.id).length)return shape(409,{ok:false,status:"existing_message_requires_review"});
  const marker=path.join(stateDir,"admissions",execution+"-inbound.json");
  try{
    writeOnce(base,{phase:"started",record_id:id,workflow,execution,sha256:digest});
    writeOnce(marker,{phase:"started",path:inboundRoute,record_id:id,workflow,execution});
    const r=await forward(raw);
    if(!r.ok||r.body?.ok!==true||r.body.ignored!==true||r.body.reason!=="before_n8n_activation")return shape(409,{ok:false,status:"reconcile_required"});
    const found=find(message.id);
    if(found.length!==1)return shape(409,{ok:false,status:"reconcile_required"});
    const row=found[0];
    if(row.instance!==instance||row.direction!=="inbound"||row.source!=="authenticated_n8n_durable_inbox"||
       row.at!=="1970-01-01T00:00:01.000Z"||row.identity_hash!==message.jidHash||row.text_sha256!==message.textHash||
       row.conversation_id!==null)return shape(409,{ok:false,status:"reconcile_required"});
    const receipt={ok:true,record_id:id,workflow,execution,status:"retained"};
    writeOnce(marker+".done",{http:200,at:new Date().toISOString(),result:receipt});
    writeOnce(done,receipt);return shape(200,receipt);
  }catch{return shape(409,{ok:false,status:"reconcile_required"});}
}
