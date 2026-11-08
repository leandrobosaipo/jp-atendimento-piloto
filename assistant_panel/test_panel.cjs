'use strict';
// Exercise rendering and reset locally, without Cockpit, browser access or a provider.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(__dirname+'/cockpit/panel.js','utf8');
const base={status:'ok',model:'fixture-model',kb_version:'synthetic',day_reserved_micro_usd:42,daily_micro_usd:50000,trial_micro_usd:250000};
async function mount(response){
  const nodes={};const requests=[];
  const document={querySelector(selector){
    return nodes[selector]??=( {textContent:'',value:'',disabled:false,events:{},addEventListener(e,f){this.events[e]=f},append(){},replaceChildren(){},focus(){}} );
  },querySelectorAll(){return []}};
  const cockpit={spawn(){const result=Promise.resolve(JSON.stringify(response));result.input=text=>requests.push(JSON.parse(text));return result}};
  vm.runInNewContext(source,{document,cockpit,crypto:webcrypto});
  await new Promise(resolve=>setImmediate(resolve));
  return {nodes,requests};
}
(async()=>{
  const monthly={...base,budget_month:'2026-11',monthly_micro_usd:10000000,month_admission_micro_usd:8006000,prior_uncertain_micro_usd:6000,budget_alerts:[{threshold_micro_usd:5000000},{threshold_micro_usd:8000000}]};
  let {nodes,requests}=await mount(monthly);
  assert.match(nodes['#info'].textContent,/Orçamento 2026-11 \(UTC\)/);
  assert.match(nodes['#info'].textContent,/\$8\.0060 \/ \$10\.0000/);
  assert.match(nodes['#info'].textContent,/retidos de meses anteriores/);
  assert.match(nodes['#info'].textContent,/Alertas locais registrados: \$5\.0000, \$8\.0000 \(reservas, não fatura\)/);
  const before=nodes['#info'].textContent;
  nodes['#reset'].events.click();
  assert.equal(nodes['#info'].textContent,before);
  assert.match(nodes['#status'].textContent,/orçamento do teste foi preservado/);
  assert.equal(nodes['#send'].disabled,false);
  assert.deepEqual(requests,[{op:'info'}]);
  ({nodes}=await mount({...monthly,prior_uncertain_micro_usd:0,budget_alerts:[]}));
  assert.doesNotMatch(nodes['#info'].textContent,/Alertas|retidos/);
  ({nodes}=await mount(base));
  assert.doesNotMatch(nodes['#info'].textContent,/Orçamento|undefined|NaN/);
  assert.equal(nodes['#send'].disabled,false);
  ({nodes}=await mount({status:'error'}));
  assert.equal(nodes['#send'].disabled,true);
  assert.match(nodes['#info'].textContent,/indisponível/);
  console.log('4 panel scenarios passed: alerts/reset, zero alerts, legacy, unavailable');
})().catch(error=>{console.error(error);process.exitCode=1});
