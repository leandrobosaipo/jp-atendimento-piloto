import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import {spawn} from "node:child_process";
import {randomBytes,timingSafeEqual} from "node:crypto";
import {fileURLToPath} from "node:url";
const dir=process.env.FIXTURE_DIR||"/fixture",plan=JSON.parse(fs.readFileSync(dir+"/grant.json"));
const aux=plan.scenario?await import("./auxiliary.mjs"):null;
assert(plan.workflow&&(aux||plan.nodes.length===11)&&new Set(plan.nodes.map(n=>n.id)).size===plan.nodes.length);
const key=randomBytes(32).toString("hex");
const write=(name,data)=>fs.writeFileSync(dir+"/"+name,JSON.stringify(data),{flag:"wx",mode:0o600,flush:true});
const read=name=>JSON.parse(fs.readFileSync(dir+"/"+name));
const state={version:1,events:[],cases:[],intents:[],audit:[],conversations:[],messages:[],reviews:[],individual_dispatches:[],jp_responses:[],reminders:[],jp_jobs:[],initial_confirmations:[],
  n8n_orchestration:{activated_at:"2026-11-09T12:00:00Z"},reactivation:{requests:[{candidate_id:"fixture-only",status:"interested"}],bookings:[],
  policy:{automation:{enabled:true,candidate_ids:["fixture-only"],target_dates:["2026-11-12"]}}}};
const overrides=aux?aux.seed(plan,state,write,dir):{};
write("state.json",state);write("candidates.json",{generated_at:"2026-11-09T10:00:00Z",send_enabled:false,candidates:[]});
write("session.json",{session:{origin:"https://fixture.invalid",cookieHeader:"synthetic=1",expires_at:"2099-01-01T00:00:00Z"}});
write("scheduler.json",{token:key});
const port=Number(process.env.FIXTURE_PORT||8791),innerPort=port+1;
const child=spawn(process.execPath,["--import",fileURLToPath(new URL("./clock_source.mjs",import.meta.url)),process.env.FIXTURE_API||"/legacy/scripts/jp-evolution-local-api.mjs"],{stdio:"ignore",env:{
  FIXTURE_DIR:dir,TZ:"America/Cuiaba",JP_LOCAL_API_HOST:"127.0.0.1",JP_LOCAL_API_PORT:String(innerPort),JP_EVOLUTION_INSTANCE:"fixture",
  JP_N8N_STAGE_MODE:"1",JP_N8N_STAGE_WORKFLOW_IDS:plan.workflow,JP_N8N_REACTIVATION_WORKFLOW_ID:plan.workflow,
  JP_N8N_STAGE_SEND_ENABLED:"0",JP_N8N_STAGE_AGENDA_WRITE_ENABLED:"0",JP_REPLY_AUTOMATION_ENABLED:"0",JP_LOCAL_API_STATE:dir+"/state.json",
  JP_LOCAL_API_READ_TOKEN:randomBytes(24).toString("hex"),JP_LOCAL_API_OPERATE_TOKEN:randomBytes(24).toString("hex"),
  JP_APPROVAL_CANDIDATES_FILE:dir+"/candidates.json",JP_FOLLOWUP_SCHEDULER_FILE:dir+"/scheduler.json",JP_AGENDA_SYNC_SESSION_FILE:dir+"/session.json",
  JP_APPBARBER_SESSION_STORE_FILE:"",JP_STATE_ENCRYPTION_KEY:"",JP_JOBS_N8N_CONFIG_FILE:dir+"/absent",JP_APPBARBER_RENEWAL_WORKER_TOKEN_FILE:"",...overrides}});
child.once("exit",()=>process.exit(1));process.on("SIGTERM",()=>{child.kill("SIGTERM");process.exit(0);});
let execution=null,index=0,busy=false,failed=false;
function equal(x,y){const a=Buffer.from(x||""),b=Buffer.from(y);return a.length===b.length&&timingSafeEqual(a,b);}
const api=http.createServer(async(req,res)=>{
  const reply=(status,b)=>{res.writeHead(status,{"Content-Type":"application/json"});res.end(JSON.stringify(b));};
  if(req.url==="/healthz"&&req.method==="GET"){
    try{const r=await fetch("http://127.0.0.1:"+innerPort+"/healthz");return reply(r.status,{ok:r.ok});}catch{return reply(503,{});}
  }
  const exp=plan.nodes[index],ex=req.headers["x-jp-n8n-execution-id"];
  if(req.method!=="POST"||!equal(req.headers.authorization,"Bearer "+plan.token)||req.headers["x-jp-n8n-workflow-id"]!==plan.workflow||!/^\d{1,20}$/.test(ex||"")||Date.now()>Date.parse(plan.until))return reply(403,{});
  if(failed||busy||!exp||req.headers["x-jp-node-id"]!==exp.id||req.url!==(aux?exp.path:"/api/n8n/stages/"+exp.stage)||(execution!==null&&execution!==ex))return reply(409,{});
  busy=true;
  try{
    let raw="";for await(const b of req){raw+=b;if(raw.length>512)throw Error("body");}
    const body=JSON.parse(raw||"{}");if(aux)aux.checkRequest(plan,req.url,body,ex);else assert.deepEqual(body,index===9?{force:true}:{});
    execution=ex;const prefix=String(index+1).padStart(2,"0");
    write(prefix+".intent",{execution:ex,node:exp.id,stage:exp.stage});
    const r=await fetch("http://127.0.0.1:"+innerPort+req.url,{method:"POST",headers:{Authorization:"Bearer "+key,"Content-Type":"application/json","X-JP-n8n-workflow-id":plan.workflow,"X-JP-n8n-execution-id":ex},body:JSON.stringify(body),signal:AbortSignal.timeout(50000)});
    const o=await r.json(),extra=aux?aux.checkResult(plan,req.url,r,o,read("state.json")):{};
    if(!aux){assert(r.ok);assert(Array.isArray(o)?o.length===1&&o[0].pending===true:o.ok===true);}
    const receipt={workflow:plan.workflow,execution:ex,ordinal:index+1,node:exp.id,stage:exp.stage,http:r.status,...(aux?extra:{body}),at:new Date().toISOString()};
    for(const k of ["status","reason","ready","checked_at","refreshed","job_id","attempted","effects","planned","due","blocked","audio_status","enrolled","reviewed","dispatch_sync"])if(k in o)receipt[k]=o[k];
    if(exp.stage==="before-dispatch"){
      const s=read("state.json"),job=s.jp_jobs.find(j=>j.job_id===o.job_id);
      assert(o.ready===true&&o.refreshed===true&&job?.status==="completed"&&job.execution_id===ex&&job.source_checked_at===o.checked_at&&read("candidates.json").generated_at===o.checked_at);
    }
    if(["send-initial","send-reminders","reconcile-responses"].includes(exp.stage))assert(o.status==="paused"&&o.attempted===0);
    if(exp.stage==="reactivation-due")receipt.pending=o[0].pending;
    if(exp.stage==="reactivation")assert(o.effects===0);
    write(prefix+".done",receipt);index++;return reply(r.status,o);
  }catch(err){failed=true;write("held.json",{index:index+1,stage:exp.stage,kind:err.name});return reply(409,{ok:false,status:"held"});}finally{busy=false;}
});
api.listen(port,process.env.FIXTURE_HOST||"0.0.0.0");
