import {AsyncLocalStorage} from "node:async_hooks";
import {createHmac,randomBytes} from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import {scopedServer} from "./dispatch.mjs";

const request=new AsyncLocalStorage(),customer=new AsyncLocalStorage();
const read=path=>JSON.parse(fs.readFileSync(path));
const requireBinding=ok=>{if(!ok)throw Error("task_read_scope_denied");};
const operations=new Map(Object.entries({
  "/pages/cadastros/buscaClientes.php":"clients","/pages/relatorios/buscaListaAgendamento.php":"visits",
  "/pages/relatorios/buscaAgendamentos.php":"history","/pages/actions/buscaProfissionais.php":"professionals",
  "/pages/actions/buscaServicos.php":"services","/pages/cadastros/buscaHorariosDisponiveisv2.php":"slots"
}));
export function taskServer(handler){
  return scopedServer((req,res)=>{
    const h=req.headers,ctx={workflow:h["x-jp-n8n-workflow-id"],node:h["x-jp-node-id"],
      execution:h["x-jp-n8n-execution-id"],epoch:h["x-jp-owner-epoch"],route:req.url,active:true};
    res.once("close",()=>{ctx.active=false;});
    return request.run(ctx,()=>handler(req,res));
  });
}
export function checkTask(task,state){
  const rows=(state.jp_reply_tasks||[]).filter(r=>r.task_id===task.task_id);
  requireBinding(rows.length===1 && ["queued","retry","prepared","planned"].includes(rows[0].status));
  const current=rows[0];
  requireBinding(["task_id","source_sha256","source_message_id","client_code","destination_e164"].every(k=>task[k]===current[k]));
  requireBinding(/^[a-f0-9-]{36}$/.test(task.task_id)&&/^[a-f0-9]{64}$/.test(task.source_sha256)&&Boolean(task.source_message_id));
  requireBinding(/^\+55[1-9]\d{9,10}$/.test(task.destination_e164)&&(task.client_code==null||/^\d{1,14}$/.test(task.client_code)));
  return {task:task.task_id,source:task.source_sha256,phone:task.destination_e164.slice(1),client:task.client_code==null?null:String(task.client_code)};
}
export function admitted(roleName,stages){
  const c=request.getStore();requireBinding(c?.active&&/^\d{1,20}$/.test(c.execution)&&/^[\w-]{1,100}$/.test(c.node));
  const role=read("/ops-config/manifest.json")[roleName],owner=read("/ops-state/ownership.json");
  const stage=c.route.split("/").at(-1);
  requireBinding(c.workflow===role.workflow&&role.nodes[c.node]===c.route&&stages.includes(stage));
  requireBinding(["rehearsal","owned"].includes(owner.mode)&&owner.roles.includes(roleName)&&owner.epoch===c.epoch&&Date.now()<Date.parse(owner.until));
  const prefix="/ops-state/admissions/"+c.workflow+"-"+c.execution+"-"+c.node,mark=read(prefix+".intent");
  requireBinding(!fs.existsSync(prefix+".done")&&["workflow","execution","node","epoch"].every(k=>c[k]===mark[k])&&mark.stage===stage);
  return {workflow:c.workflow,node:c.node,execution:c.execution,epoch:c.epoch,role:roleName,stage};
}
const admission=()=>admitted("reply",["reply-next","reply-plan","reply-execute"]);
export function withTask(task,load,fn){
  admission();checkTask(task,load());
  return customer.run(()=>checkTask(task,load()),fn);
}
export async function readTaskClients(native){
  const result=await native("/pages/cadastros/buscaClientes.php",{});
  requireBinding(result.task_customer_verified===true&&result.data?.length===1);return result.data;
}
export function scopedFetch(prior){
  return (url,options={})=>{
    const current=customer.getStore(),u=new URL(url);
    if(!current||u.origin!=="https://sistema.appbarber.com.br")return prior(url,options);
    const op=operations.get(u.pathname);
    requireBinding(op&&!u.search&&!u.hash&&options.method==="POST"&&options.redirect==="manual"&&typeof options.body==="string"&&options.body.length<=512);
    const fields=new URLSearchParams(options.body),params=Object.fromEntries(fields);
    requireBinding(Object.keys(params).length===[...fields].length);
    const scope={...admission(),...current()},generation=read("/config/session.json").generation;
    const body=JSON.stringify({scope,generation,op,params,at:Math.floor(Date.now()/1000),nonce:randomBytes(16).toString("hex")});
    const mac=createHmac("sha256",fs.readFileSync("/ops-config/task-read.key")).update(body).digest("hex");
    return new Promise((resolve,reject)=>{
      const s=net.createConnection({path:"/model/task-reads.sock",signal:options.signal});
      const parts=[];let size=0;
      s.setTimeout(14000,()=>s.destroy(Error("task_read_timeout")));s.on("error",reject);
      s.on("connect",()=>s.write(JSON.stringify({body,mac})+"\n"));
      s.on("data",b=>{if((size+=b.length)>1048576)s.destroy(Error("task_read_response_limit"));else parts.push(b);});
      s.on("end",()=>{try{const r=JSON.parse(Buffer.concat(parts));admission();current();resolve(new Response(JSON.stringify(r.body),{status:r.status}));}catch(e){reject(e);}});
    });
  };
}

/** Read one exact same-day slot through the existing signed task reader. */
export async function readExactMoveSlot(task, load, visit, time, fetchImpl=fetch){
  if(!visit||!/^\d+$/.test(String(visit.service_code||''))||!/^(16897547|17055820)$/.test(String(visit.professional_code||''))||
     !/^\d{4}-\d{2}-\d{2}$/.test(String(visit.appointment_date_iso||''))||!/^([01]\d|2[0-3]):[0-5]\d$/.test(time||''))throw Error('move_slot_scope_denied');
  return withTask(task,load,async()=>{
    const bound=scopedFetch(fetchImpl), post=async(path,params)=>{
      const r=await bound('https://sistema.appbarber.com.br'+path,{method:'POST',redirect:'manual',body:new URLSearchParams(params).toString()});
      if(r.status!==200)throw Error('move_slot_read_held');return r.json();
    };
    const catalog=await post('/pages/actions/buscaServicos.php',{profissional:String(visit.professional_code),tipo:'9'});
    const services=(catalog.servicos||[]).filter(row=>String(row.Ser_Codigo)===String(visit.service_code)&&String(row.TipoItem)==='1');
    if(services.length!==1||!Number.isInteger(Number(services[0].Ser_Intervalo_Padrao))||Number(services[0].Ser_Intervalo_Padrao)<=0)throw Error('move_service_unverified');
    const [year,month,day]=visit.appointment_date_iso.split('-');
    const raw=await post('/pages/cadastros/buscaHorariosDisponiveisv2.php',{servico:String(visit.service_code),profissional:String(visit.professional_code),dia:String(Number(day)),mes:String(Number(month)),ano:year});
    const rows=(raw.data||[]).filter(row=>String(row.PesCodigoPrincipal)===String(visit.professional_code));
    if(rows.length!==1||!Array.isArray(rows[0].horarios)||rows[0].tempo!==undefined&&Number(rows[0].tempo)!==Number(services[0].Ser_Intervalo_Padrao))throw Error('move_slot_read_held');
    const hit=rows[0].horarios.filter(row=>String(row.DataIni||'').slice(0,5)===time);
    if(hit.length!==1)throw Error('move_slot_unavailable');
    return {date:visit.appointment_date_iso,time,professional_code:String(visit.professional_code),service_code:String(visit.service_code),duration_minutes:Number(services[0].Ser_Intervalo_Padrao),service:String(services[0].Ser_Descricao||visit.service||''),professional:visit.professional};
  });
}
