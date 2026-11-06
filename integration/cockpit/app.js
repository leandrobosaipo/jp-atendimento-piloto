'use strict';
const $=s=>document.querySelector(s);
const call=async obj=>{const p=cockpit.spawn(['/usr/local/libexec/atendimento-piloto-integracao/client.py'],{err:'ignore'});p.input(JSON.stringify(obj));return JSON.parse(await p)};
function el(tag,text){const e=document.createElement(tag);e.textContent=text;return e}
let busy=false;
function lock(v){busy=v;['#send','#actor','#refresh'].forEach(s=>$(s).disabled=v)}
async function show(evt,expand=false){
 const div=el('article','');div.className='card';div.append(el('strong','Cliente fictício '+$('#actor').value),el('p',evt.question||''));$('#messages').append(div);
 if(evt.status!=='ok'){div.append(el('small','Tentativa retida ('+(evt.state||'sem confirmação')+'). Consulte o histórico antes de qualquer nova mensagem.'));return}
 div.append(el('strong',evt.private?'Revisão humana · nota privada':'Resposta no Chatwoot'),el('p',evt.reply));
 div.append(el('small','Conversa '+evt.conversation+' · Entrada '+evt.incoming+' · Saída '+evt.outgoing+' · '+(evt.ai?.model||'')+' · '+(evt.provider?.observed_at?'AppBarber '+evt.provider.observed_at:(evt.provider?.source||'Base privada'))));
 if(evt.attachment){
   const button=el('button','Ver tabela anexada');div.append(el('p',''),button);
   button.addEventListener('click',async()=>{button.disabled=true;try{const a=await call({op:'asset',request:evt.request});if(a.status!=='ok')throw Error();const bytes=Uint8Array.from(atob(a.image),c=>c.charCodeAt(0));const image=el('img','');image.alt='Tabela aprovada · '+evt.attachment.group;
     // Use a local blob for a user-selected, authenticated image; no storage URLs are exposed.
     const u=URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));image.onload=()=>URL.revokeObjectURL(u);image.src=u;div.append(image);button.remove()}catch{button.textContent='Imagem indisponível';button.disabled=false}});
   if(expand)button.click();
 }
}
async function history(){if(busy)return;lock(true);try{const r=await call({op:'history',actor:$('#actor').value});if(r.status!=='ok')throw Error();$('#messages').replaceChildren();for(const x of r.events)await show(x);$('#status').textContent='Conversa '+r.conversation+' · '+r.events.length+' registro(s) do piloto.';
 for(const [id,options] of [['#service',r.services||{}],['#professional',r.professionals||{}]]){const s=$(id);if(s.options.length===1)for(const [k,n] of Object.entries(options)){const o=el('option',n);o.value=k;s.add(o)}}}catch{$('#status').textContent='Falha ao consultar o histórico. Nenhuma mensagem enviada.'}finally{lock(false)}}
$('#actor').onchange=history;$('#refresh').onclick=history;
$('form').onsubmit=async e=>{e.preventDefault();if(busy)return;const q=$('#question').value.trim();if(!q)return;lock(true);$('#status').textContent='Conferindo a entrada e o recibo no Chatwoot…';
 const request=crypto.randomUUID();try{const r=await call({op:'ask',request,actor:$('#actor').value,text:q,intent:$('#intent').value,service:$('#service').value,professional:$('#professional').value,date:$('#date').value});await show({...r,question:q},true);$('#question').value='';$('#status').textContent=r.status==='ok'?'Entrada e resposta conferidas.':'Resultado retido. Atualize o histórico.'}catch{$('#status').textContent='Resultado incerto. Consulte o histórico; nenhuma nova tentativa foi feita.'}finally{lock(false)}};history();
