import http from "node:http";
import fs from "node:fs";
import {spawn} from "node:child_process";
import {classifier,operational} from "./admission.mjs";
import {startRenewal} from "./renewal.mjs";

const config=JSON.parse(fs.readFileSync("/config/runtime.json")),manifest=JSON.parse(fs.readFileSync("/ops-config/manifest.json"));
const secrets=JSON.parse(fs.readFileSync("/ops-config/roles.json")),roles=JSON.parse(fs.readFileSync("/config/roles.json")),stateDir="/ops-state";
const env={...roles,TZ:"America/Cuiaba",JP_N8N_STAGE_MODE:"1",JP_LOCAL_API_HOST:"127.0.0.1",JP_LOCAL_API_PORT:"8792",
  JP_EVOLUTION_INSTANCE:config.instance,JP_LOCAL_API_STATE:"/state/state.json",JP_STATE_ENCRYPTION_KEY:config.stateKey,
  JP_APPROVAL_CANDIDATES_FILE:"/state/candidates.json",JP_LOCAL_API_READ_TOKEN:config.read,JP_LOCAL_API_OPERATE_TOKEN:config.operate,
  JP_FOLLOWUP_SCHEDULER_FILE:"/config/scheduler.json",JP_APPBARBER_SESSION_STORE_FILE:"/config/session.json",JP_APPBARBER_SESSION_KEY_FILE:"/config/session.key",
  JP_JOBS_N8N_CONFIG_FILE:"/ops-config/jobs.json",CORE_MANUAL_HOOK:secrets.syncHook,JP_N8N_MANUAL_WEBHOOK_URL:secrets.manualHook,
  JP_APPBARBER_RENEWAL_WORKER_TOKEN:config.worker,CORE_DISPATCH_SOCKET:"/dispatch/dispatch.sock",
  JP_INDIVIDUAL_CONFIRMATION_CONFIG_FILE:"/ops-config/individual.json",JP_MANUAL_CONTACT_CONFIG_FILE:"/ops-config/manual.json",
  JP_CONTACT_REGISTRATION_CONFIG_FILE:"/state/contact-effect-config.json",
  JP_N8N_STAGE_SEND_ENABLED:secrets.brokerEnabled===true?"1":"0",JP_N8N_STAGE_AGENDA_WRITE_ENABLED:"0",
  JP_REPLY_SCOPED_WRITE_ENABLED:secrets.agendaWriteEnabled===true?"1":"0",JP_REPLY_AUTOMATION_ENABLED:secrets.interpretation===true?"1":"0"};
let renewalExecution=null;
await startRenewal(config,env,()=>renewalExecution);
const child=spawn(process.execPath,["--import","/ops-app/transport.mjs","/legacy/scripts/jp-evolution-local-api.mjs"],{env,stdio:"ignore"});
child.on("exit",()=>process.exitCode=1).on("exit",()=>server.close());
const innerUrl="http://127.0.0.1:8792";
const handle=operational({manifest,secrets,stateDir,model:m=>classifier(m,"/model/interpret.sock"),inner:async({route,workflow,execution,node,epoch,raw})=>{
  const renewing=route.endsWith("/renewal-run");if(renewing&&renewalExecution)return {status:409,body:{error:"renewal_busy"}};
  if(renewing)renewalExecution=execution;
  try{
  const r=await fetch(innerUrl+route,{method:"POST",redirect:"error",body:raw,signal:AbortSignal.timeout(600000),
    headers:{"Content-Type":"application/json",Authorization:"Bearer "+config.scheduler,"X-JP-n8n-workflow-id":workflow,"X-JP-n8n-execution-id":execution,"X-JP-Node-ID":node,"X-JP-Owner-Epoch":epoch}});
  return {status:r.status,body:await r.json()};
  }finally{if(renewing)renewalExecution=null;}
}});
const server=http.createServer(async(req,res)=>{
  const reply=(s,o)=>{res.writeHead(s,{"Content-Type":"application/json","Cache-Control":"no-store"});res.end(JSON.stringify(o));};
  try{
    if(req.method==="GET"&&req.url==="/healthz"){
      const r=await fetch(innerUrl+"/healthz",{signal:AbortSignal.timeout(2000)});
      const owner=JSON.parse(fs.readFileSync(stateDir+"/ownership.json"));
      return reply(r.ok?200:503,{ok:r.ok,mode:owner.mode,broker_configured:secrets.brokerEnabled===true,official_writes:secrets.agendaWriteEnabled===true});
    }
    if(req.method!=="POST")return reply(404,{});
    const chunks=[];let bytes=0;for await(const b of req){bytes+=b.length;if(bytes>131072)return reply(413,{});chunks.push(b);}
    const result=await handle({workflow:req.headers["x-jp-n8n-workflow-id"],execution:req.headers["x-jp-n8n-execution-id"],node:req.headers["x-jp-node-id"],
      authorization:req.headers.authorization,route:req.url,raw:Buffer.concat(chunks)});reply(result.status,result.body);
  }catch{return reply(503,{error:"operational_core_unavailable"});}
});
server.listen(8791,"0.0.0.0");
process.on("SIGTERM",()=>server.close(()=>child.kill("SIGTERM")));
