'use strict';
const el=id=>document.getElementById(id);
const fmt=value=>new Date(value).toLocaleString('pt-BR',{timeZone:'America/Cuiaba',dateStyle:'short',timeStyle:'medium'});
let report;
function line(parent,text,tag='small'){
  const node=document.createElement(tag);node.textContent=text;parent.appendChild(node);return node;
}
function done(r){
  return r.status==='Confirmação registrada na agenda' || (!r.ticket && ['Resposta lida','Resposta entregue'].includes(r.reply));
}
function draw(){
  if(!report)return;
  const pending=report.rows.filter(r=>!done(r));
  el('summary').textContent=pending.length+' mensagens para conferir entre '+report.rows.length+' recebidas. Uma pessoa pode ter enviado mais de uma.';
  el('rows').replaceChildren();
  const filter=el('filter').value, query=el('search').value.trim().toLocaleLowerCase('pt-BR');
  const rows=report.rows.filter(r=>(filter==='all'||(filter==='done')===done(r)) && (r.name+' '+(r.ticket||'')).toLocaleLowerCase('pt-BR').includes(query));
  for(const r of rows){
    const tr=el('rows').insertRow();const first=tr.insertCell();line(first,r.name,'strong');line(first,'Entrada '+r.seq+(r.ticket?' · chamado '+r.ticket:''));
    tr.insertCell().textContent=fmt(r.at);const outcome=tr.insertCell();line(outcome,r.reply||r.status,'span');
    if(r.reply && (r.ticket || r.status==='Confirmação registrada na agenda'))line(outcome,r.status);
    for(const p of r.proof)line(outcome,'Registro de saída em '+fmt(p.at));
    if(!r.reply && r.later)line(outcome,'Houve mensagem posterior na conversa em '+fmt(r.later)+'. O vínculo com esta entrada precisa de conferência.');
    tr.insertCell().textContent=r.owner || (r.ticket?'Sem responsável atribuído':'—');
  }
  if(!rows.length)el('rows').insertRow().insertCell().textContent='Nenhuma mensagem nessa seleção.';
}
async function refresh(){
  try{
    const file=cockpit.file('/var/lib/jp-atendimento-evidencia/latest.json');
    let text;try{text=await file.read();}finally{file.close();}
    const value=JSON.parse(text);if(!Array.isArray(value.rows))throw Error();
    report=value;draw();
    el('time').textContent='Conferido pelo Dell em '+fmt(report.at)+' · horário de Cuiabá';
    el('alert').textContent=Date.now()-Date.parse(report.at)>600000?'A última conferência tem mais de dez minutos. Verifique o monitor.':'';
    el('coverage').textContent='O Dell recebeu até o evento '+report.high+', examinou até o '+report.inspected+' e processou até o '+report.processed+'. Rodada '+report.execution+'.';
    el('origin').textContent=report.origin+'. A tela acompanha as mensagens diretas registradas no Dell.';
    el('connections').replaceChildren();for(const c of [...report.connections].reverse())line(el('connections'),fmt(c[1])+' · '+({open:'conectado',connecting:'conectando',close:'desconectado'}[c[2]]||'estado a conferir'));
  }catch{el('alert').textContent='Não foi possível ler a conferência atual com esta sessão do painel.';}
}
el('filter').onchange=draw;el('search').oninput=draw;el('reload').onclick=refresh;refresh();setInterval(refresh,60000);
