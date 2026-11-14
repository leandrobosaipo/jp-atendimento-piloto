import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import {createHash,timingSafeEqual} from "node:crypto";
const sha=x=>createHash("sha256").update(x).digest("hex");
const read=f=>JSON.parse(fs.readFileSync(f));
const result=(status,body)=>({status,body});
const safe=(s,re)=>typeof s==="string"&&re.test(s);
function equal(x,y){const a=Buffer.from(x||""),b=Buffer.from(y||"");return a.length===b.length&&timingSafeEqual(a,b);}
function once(file,data){
  const fd=fs.openSync(file,"wx",0o600);try{fs.writeFileSync(fd,JSON.stringify(data));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  const parent=fs.openSync(path.dirname(file),"r");try{fs.fsyncSync(parent);}finally{fs.closeSync(parent);}
}
export function classifier(message,socketPath){
  return new Promise((resolve,reject)=>{
    const s=net.createConnection(socketPath);let data="";
    s.setTimeout(45000,()=>s.destroy(Error("classifier_timeout")));
    s.on("connect",()=>s.write(JSON.stringify(message)+"\n"));s.on("data",b=>{data+=b;if(Buffer.byteLength(data)>4096)s.destroy(Error("classifier_limit"));});
    s.on("error",reject);s.on("end",()=>{try{const o=JSON.parse(data);resolve(o.output?result(200,{output:o.output}):result(503,{error:"classification_unavailable"}));}catch{reject(Error("classifier_invalid"));}});
  });
}
export function operational({manifest,secrets,stateDir,inner,model}){
  assert(Object.keys(manifest).length>0&&new Set(Object.values(manifest).map(r=>r.workflow)).size===Object.keys(manifest).length);
  const tokens=Object.values(secrets.roles);assert(tokens.length===Object.keys(manifest).length&&tokens.every(t=>typeof t==="string"&&t.length>=32)&&new Set(tokens).size===tokens.length);
  const leasePath=path.join(stateDir,"ownership.json"),ledger=path.join(stateDir,"admissions");
  return async req=>{
    const {workflow,execution,node,authorization,route,raw}=req;
    const role=Object.keys(manifest).find(k=>manifest[k].workflow===workflow),spec=manifest[role];
    if(!spec||!safe(execution,/^\d{1,20}$/)||!safe(node,/^[A-Za-z0-9_-]{1,100}$/)||!Object.hasOwn(spec.nodes,node)||spec.nodes[node]!==route||!equal(authorization,"Bearer "+secrets.roles[role]))return result(403,{error:"role_denied"});
    if(!Buffer.isBuffer(raw)||raw.length>131072)return result(413,{error:"payload_limit"});
    let body;try{body=JSON.parse(raw.toString()||"{}");}catch{return result(400,{error:"invalid_json"});}
    if(!body||typeof body!=="object"||Array.isArray(body))return result(400,{error:"invalid_body"});
    const gate=read(leasePath);
    if(gate.mode==="maintenance"||!["rehearsal","owned"].includes(gate.mode)||gate.owner!==secrets.owner||!safe(gate.epoch,/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/)||!(Date.now()<Date.parse(gate.until))||!gate.roles?.includes(role))return result(423,{error:"maintenance"});
    const prefix=path.join(ledger,workflow+"-"+execution+"-");
    const previous=id=>{try{const r=read(prefix+id+".done");return r.epoch===gate.epoch?r:null;}catch{return null;}};
    const stage=route.split("/").at(-1);
    const required=({"send-initial":false,"send-reminders":false,reactivation:true,"brand-send":true,"reply-execute":true})[stage]??(route==="/api/confirmation-batch/run"?true:undefined);
    if(required!==undefined){
      const pre=Object.keys(spec.nodes).filter(n=>spec.nodes[n].endsWith("/before-dispatch")&&
        (role!=="clock"||spec.forced.includes(n)===required)).map(previous);
      if(!pre.some(r=>r?.sync_ready===true))return result(409,{error:"matching_preflight_required"});
    }
    if(route==="/ops/classify"){
      const source=Object.keys(spec.nodes).filter(n=>spec.nodes[n].endsWith("/reply-next")).map(previous);
      if(typeof body.task!=="string"||typeof body.message!=="string"||!source.some(r=>r?.model_binding===sha(JSON.stringify([body.task,body.message]))))return result(403,{error:"unbound_model_input"});
    }
    const marker=prefix+node;const payload_hash=sha(raw);
    try{once(marker+".intent",{workflow,execution,node,stage,epoch:gate.epoch,payload_hash,at:new Date().toISOString()});}
    catch(e){if(e.code==="EEXIST")return result(409,{error:"previous_admission_requires_reconciliation"});throw e;}
    try{
      const fresh=read(leasePath);if(JSON.stringify(fresh)!==JSON.stringify(gate)||Date.now()>=Date.parse(fresh.until))return result(423,{error:"ownership_changed"});
      const out=route==="/ops/classify"?await model(body):await inner({route,workflow,execution,node,epoch:gate.epoch,raw});
      const o=out.body,receipt={workflow,execution,node,epoch:gate.epoch,stage,status:out.status,response_sha256:sha(JSON.stringify(o)),at:new Date().toISOString()};
      if(stage==="before-dispatch"){receipt.sync_ready=out.status===200&&o?.ready===true&&String(o.execution_id)===execution&&(!body.force||o.refreshed===true);receipt.job=o?.job_id||null;}
      if(stage==="reply-next"&&o?.has_task===true&&o?.needs_ai===true&&typeof o.task?.task_id==="string"&&typeof o.input?.message==="string")receipt.model_binding=sha(JSON.stringify([o.task.task_id,o.input.message]));
      once(marker+".done",receipt);
      if(stage==="before-dispatch"&&!receipt.sync_ready)return result(409,{error:"preflight_held"});
      return out;
    }catch{return result(503,{error:"uncertain_reconciliation_required"});}
  };
}
