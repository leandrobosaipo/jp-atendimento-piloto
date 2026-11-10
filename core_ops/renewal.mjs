import fs from "node:fs";
import {createWorkerHttpServer,runRenewalWorkerOnce} from "/legacy/scripts/jp-appbarber-login-worker.mjs";
import {decodeStateEnvelope} from "/legacy/scripts/jp-state-envelope.mjs";
export async function startRenewal(cfg,appEnv,execution){
  const workerEnv={...appEnv,JP_APPBARBER_RENEWAL_API_URL:"http://127.0.0.1:8792",JP_APPBARBER_LOGIN_URL:"https://sistema.appbarber.com.br/login.php",JP_APPBARBER_LOGIN_CREDENTIALS_FILE:"/nonexistent",JP_APPBARBER_CREDENTIALS_KEY_FILE:"/nonexistent"};
  const worker=createWorkerHttpServer({token:cfg.worker,host:"127.0.0.1",port:8795,runOnce:async()=>{
    const s=JSON.parse(decodeStateEnvelope(JSON.parse(fs.readFileSync("/state/state.json")),Buffer.from(cfg.stateKey,"hex")));
    const job=s.access_renewals?.at(-1);
    if(job&&["requested","authenticating","validating","awaiting_human","awaiting_sync","synchronizing"].includes(job.status)){
      if(!/^[a-f0-9-]{36}$/.test(job.job_id)||!/^\d{1,20}$/.test(execution()||""))throw Error("native_renewal_identity_required");
      try{fs.writeFileSync("/state/renewal-intents/"+job.job_id+".json",JSON.stringify({job:job.job_id,stage:job.status,execution:execution(),observed_at:new Date().toISOString()}),{flag:"wx",mode:0o600,flush:true});}
      catch(e){if(e.code!=="EEXIST")throw e;}
      return {ok:true,status:"awaiting_human",reason_code:"controlled_renewal_required"};
    }
    return runRenewalWorkerOnce({env:workerEnv,executeWork:async()=>({status:"operator_required"})});
  }});await worker.listen();return worker;
}
