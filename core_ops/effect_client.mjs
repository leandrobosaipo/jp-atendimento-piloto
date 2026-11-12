import http from "node:http";
import {AsyncLocalStorage} from "node:async_hooks";
import {createHash,timingSafeEqual} from "node:crypto";
import {admitted,withTask} from "./task_reads.mjs";
import {approval,guardedPost,nativeScope,wireOperation} from "./effect_guard.mjs";
const current=new AsyncLocalStorage();
export function socket(request,signal){
  const body=JSON.stringify(request);if(Buffer.byteLength(body)>24000)throw Error("effect_packet_too_large");
  return new Promise((resolve,reject)=>{
    const q=http.request({method:"POST",socketPath:"/model/task-effects.sock",path:"/v1",signal,headers:{"Content-Type":"application/json","Content-Length":Buffer.byteLength(body)}},r=>{
      let size=0;const chunks=[];r.on("error",reject);r.on("data",b=>{if((size+=b.length)>800000)r.destroy(Error("effect_response_limit"));else chunks.push(b);});
      r.on("end",()=>{try{const out=JSON.parse(Buffer.concat(chunks));if(r.statusCode!==200)throw Error("effect_held_or_unknown");resolve(new Response(JSON.stringify(out),{status:200}));}catch(e){reject(e);}});
    });q.setTimeout(12500,()=>q.destroy(Error("effect_unknown")));q.on("error",reject);q.end(body);
  });
}
export function wrapEngine(engine,load){
  const original=engine.execute;
  return {...engine,execute(input){
    if(!input.dispatchReady||!input.sendEnabled)return original(input);
    const row=(load().jp_reply_tasks||[]).find(t=>t.task_id===input.task_id);
    if(!row||row.status!=="planned")return original(input);
    if(createHash("sha256").update(JSON.stringify([row.task_id,row.source_sha256,row.destination_e164,row.client_code])).digest("hex")!==input.token)return original(input);
    const request=admitted("reply",["reply-execute"]);
    if(input.writeEnabled&&["book","move","confirm"].includes(row.plan?.kind)){
      const base={...request,task:row.task_id,source:row.source_sha256,client:row.client_code,phone:row.destination_e164},p=row.plan,o=p.option;
      const slot=o&&[o.date,o.time,o.professional_code,o.service_code].join("/");
      const operations=p.kind==="confirm"?(p.appointments||[]).map(a=>["confirm",a.appointment_id]):
        [[p.kind==="book"?"create":"move",p.kind==="book"?slot:p.move_appointment_id],["confirm_created",slot]];
      try{for(const [operation,reference] of operations)approval("/ops-config","/ops-state",{...base,operation,reference});}
      catch{return {status:"task_write_permission_held",attempted:0};}
    }
    return current.run({request,task:row.task_id,load},()=>withTask(row,load,()=>original(input)));
  }};
}
export function registrationPreflight(runtime,load,config){
  const original=runtime.update;
  return {...runtime,update(input){
    if(config()?.enabled!==true)return original(input);
    const s=load(),req=s.contact_registration_requests?.find(r=>r.request_id===input.request_id);
    const t=s.contact_registration_tasks?.find(x=>x.task_id===req?.task_id);
    if(!req||typeof input.capability!=="string"||!/^\w{64}$/.test(req.capability_hash||"")||Date.now()>=Date.parse(req.capability_expires_at)||
      !timingSafeEqual(Buffer.from(req.capability_hash),Buffer.from(createHash("sha256").update(input.capability).digest("hex"))))return original(input);
    if(t?.status==="pending"){
      try{
        const scope=admitted("registration",["update"]);
        approval("/ops-config","/ops-state",{...scope,operation:"contact",reference:req.request_id,source:req.source_message_id,client:req.client_code,phone:req.destination_e164,task:t.task_id});
      }catch{return {status:"task_write_permission_held",ready:false};}
    }
    return original(input);
  }};
}
export function contactScope(action,row,load,fn){
  const request=admitted("registration",action==="write"?["update"]:["claim","update"]);
  const matches=(load().contact_registration_requests||[]).filter(r=>r.request_id===row.request_id&&r.client_code===row.client_code&&r.destination_e164===row.destination_e164&&r.source_message_id===row.source_message_id);
  if(matches.length!==1||matches[0].dispatch?.status!=="provider_accepted"||matches[0].dispatch.message_id!==row.source_message_id)throw Error("contact_request_unbound");
  return current.run({request,load,task:matches[0].task_id,contact:matches[0]},fn);
}
export async function contactSource(row,load){
  return contactScope("read",row,load,async()=>{
    const {request,contact:r}=current.getStore();
    const response=await socket({action:"contact_source",scope:{...request,client:r.client_code,phone:r.destination_e164,source:r.source_message_id,request_id:r.request_id}},AbortSignal.timeout(15000));
    return response.json();
  });
}
export function effectFetch(prior){
  return (url,options={})=>{
    const ctx=current.getStore(),u=new URL(url);if(!ctx)return prior(url,options);
    const isContact=u.origin==="http://task-contact.invalid"&&["/jp/contacts/read","/jp/contacts/write"].includes(u.pathname);
    const isAgenda=u.origin==="https://sistema.appbarber.com.br"&&["/pages/actions/atualizaHorario.php","/pages/cadastros/insereAgendamentov5.php","/pages/cadastros/alteraAgendamento.php","/pages/actions/buscaAgenda3.php"].includes(u.pathname);
    if(!isContact&&!isAgenda)return prior(url,options);
    if(isContact&&!ctx.contact||!isContact&&ctx.contact||u.search||u.hash||options.method!=="POST")throw Error("effect_route_denied");
    const req=admitted(ctx.request.role,[ctx.request.stage]),state=ctx.load();
    if(isContact&&u.pathname.endsWith("/read")){
      const r=ctx.contact,b=JSON.parse(options.body);if(b.phone!==r.destination_e164.replace(/^\+/,""))throw Error("contact_identity_changed");
      return socket({action:"contact_read",scope:{...req,client:r.client_code,phone:r.destination_e164,source:r.source_message_id,request_id:r.request_id},body:b},options.signal);
    }
    if(u.pathname.endsWith("/buscaAgenda3.php")){
      const t=state.jp_reply_tasks.find(r=>r.task_id===ctx.task);if(!t||!t.client_code||t.status!=="planned")throw Error("calendar_scope_denied");
      return socket({action:"calendar",scope:{...req,task:ctx.task,client:t.client_code,phone:t.destination_e164,source:t.source_sha256},body:Object.fromEntries(new URLSearchParams(options.body))},options.signal);
    }
    const {operation,body}=wireOperation(url,options);
    const scope=nativeScope({...req,task:ctx.task},state,operation,body);
    return guardedPost({config:"/ops-config",state:"/ops-state",ledger:"/ops-state/admissions/effects",scope,body,
      submit:()=>socket({action:"write",scope,body},options.signal)});
  };
}
