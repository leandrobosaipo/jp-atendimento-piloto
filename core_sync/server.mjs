import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {timingSafeEqual} from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import {inboundRoute,retainInbound} from "./inbound.mjs";
import {createWorkerHttpServer,runRenewalWorkerOnce} from "/legacy/scripts/jp-appbarber-login-worker.mjs";
import {decodeStateEnvelope} from "/legacy/scripts/jp-state-envelope.mjs";

process.umask(0o077);
const cfg=JSON.parse(fs.readFileSync("/config/runtime.json"));
const roles=JSON.parse(fs.readFileSync("/config/roles.json"));
const appEnv={...roles,JP_N8N_STAGE_MODE:"1",JP_N8N_STAGE_AGENDA_WRITE_ENABLED:"0",JP_N8N_STAGE_SEND_ENABLED:"0",JP_REPLY_AUTOMATION_ENABLED:"0",
 JP_LOCAL_API_HOST:"127.0.0.1",JP_LOCAL_API_PORT:"8792",JP_EVOLUTION_INSTANCE:cfg.instance,
 JP_LOCAL_API_STATE:"/state/state.json",JP_STATE_ENCRYPTION_KEY:cfg.stateKey,JP_APPROVAL_CANDIDATES_FILE:"/state/candidates.json",
 JP_LOCAL_API_READ_TOKEN:cfg.read,JP_LOCAL_API_OPERATE_TOKEN:cfg.operate,JP_FOLLOWUP_SCHEDULER_FILE:"/config/scheduler.json",
 JP_APPBARBER_SESSION_STORE_FILE:"/config/session.json",JP_APPBARBER_SESSION_KEY_FILE:"/config/session.key",
 JP_APPBARBER_RENEWAL_WORKER_TOKEN:cfg.worker,JP_JOBS_N8N_CONFIG_FILE:"/config/jobs.json",CORE_MANUAL_HOOK:cfg.manualHook,JP_N8N_MANUAL_WEBHOOK_URL:cfg.relayHook,
 TZ:"America/Cuiaba"};
const inner="http://127.0.0.1:8792";
let renewalExecution=null;
const proc=spawn(process.execPath,["--import","/app/transport.mjs","/legacy/scripts/jp-evolution-local-api.mjs"],{env:appEnv,stdio:"ignore"});
proc.once("exit",()=>process.exit(1));
process.once("SIGTERM",()=>{proc.kill("SIGTERM");process.exit(0);});
// The native HTTP executor still performs its authenticated no-work claim.
// Pending login work is held for the existing, separately operated reader.
const workEnv={...appEnv,JP_APPBARBER_RENEWAL_API_URL:inner,JP_APPBARBER_LOGIN_URL:"https://sistema.appbarber.com.br/login.php",JP_APPBARBER_LOGIN_CREDENTIALS_FILE:"/nonexistent",JP_APPBARBER_CREDENTIALS_KEY_FILE:"/nonexistent"};
const worker=createWorkerHttpServer({token:cfg.worker,host:"127.0.0.1",port:8795,runOnce:async()=>{
 const state=JSON.parse(decodeStateEnvelope(JSON.parse(fs.readFileSync("/state/state.json")),Buffer.from(cfg.stateKey,"hex")));
 const job=state.access_renewals?.at(-1);
 if(job&&["requested","authenticating","validating","awaiting_human","awaiting_sync","synchronizing"].includes(job.status)){
   const actionPath="/state/renewal-intents/"+job.job_id+".json";
   assert(/^[a-f0-9-]{36}$/.test(job.job_id));
   assert(/^\d{1,20}$/.test(renewalExecution||""));
   try{fs.writeFileSync(actionPath,JSON.stringify({job:job.job_id,stage:job.status,execution:renewalExecution,observed_at:new Date().toISOString()}),{mode:0o600,flag:"wx",flush:true});}
   catch(e){if(e.code!=="EEXIST")throw e;}
   return {ok:true,status:"awaiting_human",reason_code:"controlled_renewal_required"};
 }
 return runRenewalWorkerOnce({env:workEnv,executeWork:async()=>({status:"operator_required"})});
}});
await worker.listen();
const paths={
 "/api/agenda-sync/run":cfg.manualId,"/api/n8n/manual":cfg.relayId,"/api/n8n/stages/before-dispatch":cfg.relayId,
 "/api/n8n/stages/full-sync":cfg.dailyId,
 "/api/n8n/stages/record-error":cfg.errorId,"/api/n8n/stages/reply-next":cfg.replyId,"/api/n8n/stages/support":cfg.replyId,
 "/api/n8n/stages/maintenance":cfg.renewalId,
 "/api/n8n/stages/renewal-run":cfg.renewalId
};
const equal=(a,b)=>{const x=Buffer.from(a||""),y=Buffer.from(b||"");return x.length===y.length&&timingSafeEqual(x,y);};
const busy=new Set();
const handler=async(req,res)=>{
 const send=(status,data)=>{res.writeHead(status,{"Content-Type":"application/json","Cache-Control":"no-store"});res.end(JSON.stringify(data));};
 const path=req.url,id=req.headers["x-jp-n8n-workflow-id"],exec=req.headers["x-jp-n8n-execution-id"];
 if(req.method==="GET"&&path==="/healthz"){
  try{const r=await fetch(inner+"/healthz");return send(r.status,{ok:r.ok,scope:"isolated_agenda",sending:false,agendaWrites:false});}catch{return send(503,{ok:false});}
 }
 if(path===inboundRoute&&req.method==="POST"){
  try{
   const pieces=[];let size=0;
   for await(const b of req){size+=b.length;if(size>16384)return send(413,{ok:false});pieces.push(b);}
   const token=fs.readFileSync("/config/receiver.key","utf8").trim();
   const find=key=>{
    const envelope=JSON.parse(fs.readFileSync("/state/state.json"));
    const state=JSON.parse(decodeStateEnvelope(envelope,Buffer.from(cfg.stateKey,"hex")));
    return (state.jp_durable_inbox||[]).filter(row=>row.message_id===key);
   };
   const forward=async raw=>{
    const r=await fetch(inner+inboundRoute,{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+cfg.scheduler,"X-JP-n8n-workflow-id":id,"X-JP-n8n-execution-id":exec},body:raw,signal:AbortSignal.timeout(20000)});
    return {ok:r.ok,body:await r.json()};
   };
   const result=await retainInbound({raw:Buffer.concat(pieces),id:req.headers["x-jp-record-id"],workflow:id,execution:exec,auth:req.headers.authorization,
    expectedWorkflow:roles.JP_N8N_INBOUND_WORKFLOW_ID,token,grant:JSON.parse(fs.readFileSync("/state/grant.json")),instance:cfg.instance,stateDir:"/state",find,forward});
   return send(result.status,result.body);
  }catch{return send(409,{ok:false,status:"inbound_held"});}
 }
 if(req.method!=="POST"||!Object.hasOwn(paths,path))return send(404,{ok:false});
 if(!id||paths[path]!==id||!/^\d{1,20}$/.test(exec||"")||!equal(req.headers.authorization,"Bearer "+cfg.ingress))return send(403,{ok:false});
 if(busy.has(path))return send(409,{ok:false,reason:"busy"});
 busy.add(path);
 try{
  const grant=JSON.parse(fs.readFileSync("/state/grant.json"));assert(Date.now()<Date.parse(grant.until)&&grant.paths.includes(path));
  const pieces=[];let bytes=0;
  for await(const p of req){bytes+=p.length;assert(bytes<=2048);pieces.push(p);}
  const body=JSON.parse(Buffer.concat(pieces).toString()||"{}");
  if(path==="/api/agenda-sync/run"){assert(typeof body.job_id==="string"&&typeof body.capability==="string");body.execution_id=exec;}
  else if(path==="/api/n8n/stages/record-error"){
   assert(Object.keys(body).sort().join(",")==="last_node,source_execution_id,source_workflow_id");
   assert(cfg.errorFixtureId&&body.source_workflow_id===cfg.errorFixtureId&&body.last_node==="Falha controlada");
   assert(body.source_execution_id===null||/^\d{1,20}$/.test(String(body.source_execution_id)));
  }else if(path==="/api/n8n/manual"){
   assert(body.method==="POST"&&body.route==="/api/agenda-sync/start"&&body.body?.operator==="dell_rehearsal"&&Object.keys(body.body).length===1);
   assert(typeof body.request_id==="string"&&typeof body.capability==="string");
  }else if(path==="/api/n8n/stages/before-dispatch")assert(body.force===true&&Object.keys(body).length===1);
  else assert(Object.keys(body).length===0);
  const marker="/state/admissions/"+exec+"-"+path.split("/").at(-1)+".json";
  const fd=fs.openSync(marker,"wx",0o600);
  try{fs.writeSync(fd,JSON.stringify({phase:"started",path,workflow:id,execution:exec}));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  if(path==="/api/n8n/stages/renewal-run")renewalExecution=exec;
  const r=await fetch(inner+path,{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+cfg.scheduler,"X-JP-n8n-workflow-id":id,"X-JP-n8n-execution-id":exec},body:JSON.stringify(body),signal:AbortSignal.timeout(600000)});
  const obj=await r.json();
  const safe={ok:obj.ok===true,status:obj.status||null,job_id:obj.job_id||null,duplicate:obj.duplicate===true,
    ...(obj.result?.status?{worker_result:obj.result.status}:{}),...(obj.error?{error:String(obj.error).replace(/[^a-z_]/g,"").slice(0,70)}:{})};
  if(path==="/api/n8n/stages/reply-next"&&r.ok){
   assert(obj.has_task===false&&obj.status==="paused");safe.has_task=false;
  }
  if(path==="/api/n8n/stages/support"&&r.ok){
   assert(obj.status==="paused"&&obj.attempted===0&&obj.reason==="n8n_send_gate_disabled");Object.assign(safe,{attempted:0,reason:obj.reason});
  }
  fs.writeFileSync(marker+".done",JSON.stringify({http:r.status,at:new Date().toISOString(),result:safe}),{flag:"wx",mode:0o600});
  return send(r.status,path==="/api/n8n/manual"?{status:obj.status||r.status,body:{ok:obj.body?.ok===true}}:safe);
 }catch{return send(409,{ok:false,reason:"held_or_previously_admitted"});}
 finally{if(path==="/api/n8n/stages/renewal-run")renewalExecution=null;busy.delete(path);}
};
const api=http.createServer(handler);api.listen(8791,"0.0.0.0");
