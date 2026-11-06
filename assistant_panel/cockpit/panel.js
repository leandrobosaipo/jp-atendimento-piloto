'use strict';
const $=s=>document.querySelector(s);
let session=crypto.randomUUID(), busy=false, ready=false;
const dollars=n=>(n/1000000).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:4});
function call(payload){
  const p=cockpit.spawn(['/usr/local/libexec/atendimento-piloto-ai/client.py'],{err:'ignore'});
  p.input(JSON.stringify(payload));
  return Promise.resolve(p).then(s=>JSON.parse(s));
}
function bubble(who,text,detail){
  const article=document.createElement('article');article.className='bubble '+(who==='Você'?'user':'answer');
  const label=document.createElement('strong');label.textContent=who;
  const p=document.createElement('p');p.textContent=text;article.append(label,p);
  if(detail){const d=document.createElement('small');d.className='details';d.textContent=detail;article.append(d)}
  $('#messages').append(article);
}
async function info(){try{
  const i=await call({op:'info'});if(i.status!=='ok')throw Error();
  $('#info').textContent=i.model+' · Base '+i.kb_version+' · Uso reservado hoje (UTC) '+dollars(i.day_reserved_micro_usd)+' / '+dollars(i.daily_micro_usd)+' · Limite do teste '+dollars(i.trial_micro_usd);
  ready=true;
}catch{ready=false;$('#info').textContent='A configuração privada está indisponível.'}$('#send').disabled=!ready||busy}
const errors={held_uncertain:'A tentativa ficou retida porque a resposta não pôde ser confirmada. Ela não será reenviada automaticamente.',budget_limit:'O limite de uso do teste foi atingido.',session_limit:'Esta conversa chegou ao limite de turnos. Inicie uma nova conversa.',invalid_question:'Revise a mensagem: use até 850 caracteres.',context_limit:'O contexto ficou extenso. Tente uma pergunta mais curta em uma nova conversa.',unavailable:'O serviço privado não respondeu. O estado da tentativa pode estar incerto.'};
$('form').addEventListener('submit',async e=>{
  e.preventDefault();if(busy||!ready)return;const q=$('#text').value.trim();if(!q)return;
  busy=true;$('#send').disabled=true;$('#reset').disabled=true;$('#text').value='';bubble('Você',q);$('#status').textContent='Consultando a base e o modelo…';
  try{const r=await call({op:'ask',id:crypto.randomUUID(),session,text:q});
    if(r.status!=='ok')throw Error(r.code);
    const refs=r.sources.map(s=>s.title).join(' · ');
    const usage=r.mode==='local'?'Saudação local · sem chamada à API':'Tokens: '+r.tokens.input+' entrada / '+r.tokens.output+' saída · Estimativa: '+dollars(r.estimate_micro_usd);
    bubble(r.decision==='handoff'?'Sugestão de encaminhamento':'Assistente virtual',r.reply,(refs?'Fontes: '+refs+' · ':'')+usage);$('#status').textContent='Resposta registrada no piloto.';
  }catch(err){$('#status').textContent=errors[err.message]||'A tentativa não foi confirmada. Não houve reenvio automático.'}
  finally{busy=false;$('#reset').disabled=false;await info();$('#text').focus()}
});
$('#reset').addEventListener('click',()=>{if(busy)return;session=crypto.randomUUID();$('#messages').replaceChildren();$('#text').value='';$('#status').textContent='Nova conversa. O orçamento do teste foi preservado.';$('#text').focus()});
document.querySelectorAll('.suggest button').forEach(b=>b.addEventListener('click',()=>{$('#text').value=b.textContent;$('#text').focus()}));info();
