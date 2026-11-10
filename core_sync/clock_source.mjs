// Only loaded in the disposable synthetic handler. Every fetch is intercepted.
import fs from "node:fs";
const start=Date.now(),base=Date.parse("2026-11-10T10:00:00-04:00"),RealDate=Date;
globalThis.Date=class extends RealDate{
  constructor(...args){super(...(args.length?args:[base+RealDate.now()-start]));}
  static now(){return base+RealDate.now()-start;}
};
globalThis.fetch=async (input,options={})=>{
  const u=new URL(input),name=u.pathname.split("/").at(-1);
  const ok=u.origin==="https://fixture.invalid"&&options.method==="POST"&&
    ["/pages/cadastros/buscaClientes.php","/pages/relatorios/buscaListaAgendamento.php","/pages/actions/buscaServicos.php"].includes(u.pathname)&&!u.search&&!u.hash;
  fs.appendFileSync(process.env.FIXTURE_DIR+"/source.ndjson",JSON.stringify({name:ok?name:"blocked",allowed:ok})+"\n",{mode:0o600});
  if(!ok)throw Error("fixture_transport_denied");
  const rows=name==="buscaClientes.php"?Array.from({length:100},(_,i)=>({Codigo:String(i+1),Nome:"Fixture "+(i+1)})):
    name==="buscaListaAgendamento.php"?[{Age_Codigo:"9001",CliCodigo:"1",DataAgendamento:"12/11/2026",HoraAgendamento:"11:00:00",Ser_Descricao:"Manutenção Prótese Capilar",ProNome:"Teste",ProCodigo:"88",Ser_Codigo:"77",Status:"Agendado"}]:[];
  return new Response(JSON.stringify({recordsFiltered:rows.length,recordsTotal:rows.length,data:rows}),{headers:{"Content-Type":"application/json"}});
};
