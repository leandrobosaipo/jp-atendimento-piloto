import fs from "node:fs";
import path from "node:path";
import {createHash,randomUUID} from "node:crypto";

const hash=b=>createHash("sha256").update(b).digest("hex");
const read=f=>JSON.parse(fs.readFileSync(f));
const ok=(v)=>{if(!v)throw Error("effect_scope_denied");};
const fields=["role","workflow","node","task","source","client","phone","operation","reference"];
const kinds={reply:["confirm","create","move"],registration:["contact"]};
const same=(a,b)=>fields.every(k=>a[k]===b[k]);
export const keyOf=scope=>hash(JSON.stringify([scope.role,scope.task,scope.source,scope.operation,scope.reference]));
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==="object"?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;
export const bodyDigest=v=>hash(JSON.stringify(canonical(v)));
function once(file,obj){
  const fd=fs.openSync(file,"wx",0o600);
  try{fs.writeFileSync(fd,JSON.stringify(obj));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  const dir=fs.openSync(path.dirname(file),"r");try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}
}
export function approval(config,state,scope){
  ok(Object.hasOwn(kinds,scope.role)&&kinds[scope.role].includes(scope.operation)&&/^\d+$/.test(scope.execution)&&/^\d+$/.test(scope.client)&&/^\+55\d{10,11}$/.test(scope.phone));
  ok(typeof scope.reference==="string"&&scope.reference.length>0&&scope.reference.length<=160&&typeof scope.source==="string"&&scope.source.length>0&&scope.source.length<=160);
  ok((scope.role==="registration"?/^jp-contact-[a-f0-9]{24}$/:/^[a-f0-9-]{36}$/).test(scope.task));
  const name=keyOf(scope),g=read(path.join(config,"grants",name+".json")),owner=read(path.join(state,"ownership.json"));
  const now=Date.now(),until=Date.parse(g.until);
  ok(g.schema===1&&same(g,scope)&&typeof g.approved_by==="string"&&g.approved_by.trim()&&g.approved_by.length<=160&&g.authorization===true);
  ok(g.epoch===scope.epoch&&owner.epoch===g.epoch&&owner.roles?.includes(g.role)&&until>now&&until<=Date.parse(owner.until)&&until-Date.parse(g.approved_at)<=300000&&Date.parse(g.approved_at)<=now);
  ok(["fixture","official"].includes(g.target)&&owner.mode===(g.target==="fixture"?"rehearsal":"owned"));
  const stage=scope.role==="reply"?"reply-execute":"update";
  const spec=read(path.join(config,"manifest.json"))[scope.role];ok(spec.workflow===scope.workflow&&spec.nodes[scope.node]===(scope.role==="reply"?"/api/n8n/stages/reply-execute":"/api/contact-registration/update"));
  const prefix=path.join(state,"admissions",scope.workflow+"-"+scope.execution+"-"+scope.node),intent=read(prefix+".intent");
  ok(["epoch","execution","workflow","node"].every(k=>scope[k]===intent[k])&&intent.stage===stage&&!fs.existsSync(prefix+".done"));
  ok(/^[a-f0-9]{64}$/.test(g.payload_sha256));return g;
}
export function approveBody(operation,body){
  const contents=structuredClone(body);
  if(operation==="create"){
    ok(/^JP nova visita [a-f0-9-]{36}$/.test(contents.observacao));
    for(const k of ["lembrete","sms","whats"])ok(contents[k]==="0");
    contents.observacao="JP nova visita {native-uuid}";
  }
  return bodyDigest(contents);
}
export function wireOperation(url,options){
  const u=new URL(url);ok(!u.search&&!u.hash&&options.method==="POST");
  if(u.origin==="http://task-contact.invalid"&&u.pathname==="/jp/contacts/write"&&options.redirect==="error")
    return {operation:"contact",body:JSON.parse(options.body)};
  ok(u.origin==="https://sistema.appbarber.com.br"&&options.redirect==="manual");
  const op=({"/pages/actions/atualizaHorario.php":"confirm","/pages/cadastros/insereAgendamentov5.php":"create","/pages/cadastros/alteraAgendamento.php":"move"})[u.pathname];
  ok(op);const params=new URLSearchParams(options.body),body=Object.fromEntries(params);ok(Object.keys(body).length===[...params].length);
  return {operation:op,body};
}
export function nativeScope(context,state,operation,body){
  if(operation==="contact"){
    const t=state.contact_registration_tasks?.filter(r=>r.task_id===context.task&&r.status==="update_requested")||[];ok(t.length===1);
    const a=t[0],rows=state.contact_registration_requests?.filter(r=>r.task_id===a.task_id&&r.source_message_id===a.source_message_id&&r.client_code===a.client_code&&r.destination_e164===a.destination_e164)||[];ok(rows.length===1);
    const r=rows[0];ok(r.dispatch?.status==="provider_accepted"&&r.dispatch.message_id===r.source_message_id&&r.authorization?.scope&&a.write_attempts===1&&body.phone===a.destination_e164.replace(/^\+/,"")&&a.name===body.name);
    const proof=body.sourceProof;ok(proof?.source==="whatsapp_contact_screen_human"&&proof.phone===body.phone&&proof.name===body.expectedPreviousName&&proof.proofId&&Date.parse(proof.observedAt)<=Date.now()&&Date.now()-Date.parse(proof.observedAt)<300000);
    return {...context,role:"registration",operation,source:a.source_message_id,client:a.client_code,phone:a.destination_e164,reference:r.request_id};
  }
  const tasks=state.jp_reply_tasks?.filter(t=>t.task_id===context.task&&t.status==="planned")||[];ok(tasks.length===1);
  const t=tasks[0],attempts=(state.jp_reply_attempts||[]).filter(a=>a.task_id===t.task_id&&a.client_code===t.client_code&&a.destination_e164===t.destination_e164&&a.status==="write_unknown"&&a.attempted_at);
  const matched=attempts.filter(a=>{
    if(operation==="confirm")return ["confirm","confirm_created"].includes(a.kind)&&a.appointment_id===body.agendamento&&body.tipo==="5";
    if(a.kind!==operation||a.source_sha256!==t.source_sha256)return false;
    const o=a.option;
    if(operation==="create")return t.plan?.kind==="book"&&o&&body.cliente===t.client_code&&body.profissional===o.professional_code&&body.item===o.service_code&&body.dataagendamento===o.date+" "+o.time&&body.duracao===String(o.duration_minutes);
    return operation==="move"&&t.plan?.kind==="move"&&a.before?.appointment_id===t.plan.move_appointment_id&&a.before.item_code===body.codItem&&o&&body.profissionalAlteraAgendamento===o.professional_code&&body.horaAlteraAgendamento===o.time&&body.dataAlteraAgendamento===o.date.split("-").reverse().join("/")&&body.tipoAlteraAgendamento==="4";
  });
  ok(matched.length===1);const a=matched[0];
  const reference=operation==="confirm"?body.agendamento:operation==="move"?t.plan.move_appointment_id:[a.option.date,a.option.time,a.option.professional_code,a.option.service_code].join("/");
  return {...context,role:"reply",operation,source:t.source_sha256,client:t.client_code,phone:t.destination_e164,reference};
}
export async function guardedPost({config,state,ledger,scope,body,submit}){
  const g=approval(config,state,scope),digest=approveBody(scope.operation,body);
  ok(g.payload_sha256===digest);const key=keyOf(scope),file=path.join(ledger,key+".intent");
  try{once(file,{key,payload_sha256:digest,attempt:randomUUID(),at:new Date().toISOString(),execution:scope.execution,operation:scope.operation});}
  catch(e){if(e.code==="EEXIST")throw Error("effect_unknown_reconcile_only");throw e;}
  try{
    const again=approval(config,state,scope);ok(bodyDigest(again)===bodyDigest(g));
    const result=await submit(g.target);
    once(path.join(ledger,key+".observed"),{at:new Date().toISOString(),http_status:result.status,native_readback_required:true});
    return result;
  }catch{throw Error("effect_unknown_reconcile_only");}
}
