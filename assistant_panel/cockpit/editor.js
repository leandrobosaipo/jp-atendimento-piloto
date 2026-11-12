'use strict';
const $=s=>document.querySelector(s);
let revision=null,validated=false,busy=false;
function run(payload){
  const p=cockpit.spawn(['/usr/local/libexec/atendimento-piloto-ai/knowledge_editor.py'],{superuser:'require',err:'message'});
  p.input(JSON.stringify(payload));return Promise.resolve(p).then(JSON.parse);
}
function documentValue(){
  return {knowledge:JSON.parse($('#knowledge').value),instructions:{version:$('#instruction-version').value.trim(),text:$('#instructions').value.trim()}};
}
function state(message,error=false){$('#status').textContent=message;$('#status').className=error?'error':'';}
function setBusy(value){busy=value;for(const id of ['#reload','#validate','#publish'])$(id).disabled=value||(id==='#publish'&&!validated);}
async function load(){
  setBusy(true);validated=false;
  try{const r=await run({op:'view'});if(r.status!=='ok')throw Error(r.code);
    revision={knowledge_sha256:r.knowledge_sha256,instructions_sha256:r.instructions_sha256};
    $('#knowledge').value=JSON.stringify(r.document.knowledge,null,2);$('#instruction-version').value=r.document.instructions.version;$('#instructions').value=r.document.instructions.text;
    state('Revisão ativa carregada. Edite, valide e publique somente quando estiver pronta.');
  }catch(e){revision=null;state('Não foi possível ler a revisão privada. Nenhuma alteração foi feita.',true);}finally{setBusy(false);}
}
function changed(){validated=false;$('#publish').disabled=true;}
$('#knowledge').addEventListener('input',changed);$('#instruction-version').addEventListener('input',changed);$('#instructions').addEventListener('input',changed);
$('#reload').addEventListener('click',load);
$('#validate').addEventListener('click',async()=>{if(busy)return;setBusy(true);try{const r=await run({op:'validate',document:documentValue()});if(r.status!=='ok')throw Error(r.code);validated=true;state(`Rascunho válido: ${r.entry_count} fichas. Revise o texto e use “Publicar revisão” para aplicar.`);}catch(e){validated=false;state('O rascunho não foi validado. Corrija o JSON ou as diretrizes; nada foi publicado.',true);}finally{setBusy(false);}});
$('#editor-form').addEventListener('submit',async event=>{event.preventDefault();if(busy||!validated||!revision)return;if(!window.confirm('Publicar esta revisão privada? O gateway será reiniciado; nenhum envio ou agenda será alterado.'))return;setBusy(true);try{const r=await run({op:'publish',document:documentValue(),expected_knowledge_sha256:revision.knowledge_sha256,expected_instructions_sha256:revision.instructions_sha256});if(r.status!=='ok')throw Error(r.code);revision={knowledge_sha256:r.knowledge_sha256,instructions_sha256:r.instructions_sha256};validated=false;state(`Revisão ${r.knowledge_version} publicada com backup privado. Agora teste com uma conversa fictícia antes de qualquer uso operacional.`);}catch(e){validated=false;state('A publicação não foi confirmada; a revisão anterior foi preservada ou o sistema reteve a alteração. Recarregue antes de tentar novamente.',true);}finally{setBusy(false);}});
load();
