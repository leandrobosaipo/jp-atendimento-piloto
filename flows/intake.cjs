// Executed with the SDK bundled in the exact n8n image. Input contains only local resource IDs.
const { createRequire } = require('node:module');
const sdk = createRequire('/usr/local/lib/node_modules/n8n/package.json')('@n8n/workflow-sdk');
const fs = require('node:fs');
const runtime = JSON.parse(fs.readFileSync(0,'utf8'));
const {workflow,trigger,node,ifElse,newCredential} = sdk;
const name = 'Fixture Intake - local only';
const cred = (type, key) => ({[type]:newCredential(runtime[key].name,runtime[key].id)});
const step = (type,version,title,position,parameters,extras={}) => node({type:'n8n-nodes-base.'+type,version,config:{name:title,position,parameters,...extras}});
const code = (title,x,y,jsCode) => step('code',2,title,[x,y],{mode:'runOnceForAllItems',jsCode});
const pg = (title,x,query,args) => step('postgres',2.6,title,[x,0],{resource:'database',operation:'executeQuery',query,options:{queryReplacement:args}}, {credentials:cred('postgres','dbCredential')});
const http = (title,x,url,credential,body) => step('httpRequest',4.3,title,[x,0],{
  method:'POST',url,authentication:'genericCredentialType',genericAuthType:'httpHeaderAuth',
  sendBody:true,specifyBody:'json',jsonBody:body,
  options:{timeout:25000,redirect:{redirect:{followRedirects:false}},response:{response:{fullResponse:true,neverError:true}}}
},{credentials:cred('httpHeaderAuth',credential),onError:'continueRegularOutput',retryOnFail:false});
const hook=trigger({type:'n8n-nodes-base.webhook',version:2.1,config:{name:'Synthetic inbox',position:[0,0],
  credentials:cred('httpHeaderAuth','inboundCredential'),parameters:{httpMethod:'POST',path:'pilot-synthetic-v1',authentication:'headerAuth',responseMode:'lastNode',responseData:'firstEntryJson',options:{}}}});
const normalize=code('Identify and classify',220,0,`
const b=$input.first().json.body||{};
const allowedQuestions=['Qual é o horário do piloto?','Tenho uma reclamação.','Preciso de ajuda.'];
if(b.source!=='fixture' || !/^demo-[a-z0-9-]{1,48}$/.test(b.event_id||'') || !['fixture-001','unknown','ambiguous'].includes(b.actor) || !['text','media'].includes(b.kind) || !allowedQuestions.includes(b.text) || !['none','tool','uncertain'].includes(b.fault||'none')) throw Error('Invalid synthetic event');
const route=b.actor!=='fixture-001'?'identity':b.kind==='media'?'media':b.text===allowedQuestions[1]?'complaint':b.text===allowedQuestions[0]?'faq':'human';
return [{json:{event_id:b.event_id, actor:b.actor, kind:b.kind, text:b.text, fault:b.fault||'none', route}}];`);
const claim=pg('Reserve event once',440,`INSERT INTO pilot_events (event_id,payload) VALUES ($1,$2::jsonb)
ON CONFLICT (event_id) DO UPDATE SET attempts=pilot_events.attempts+1
RETURNING event_id,payload,attempts,state,message_id,(attempts=1) AS fresh,(payload=$2::jsonb) AS same`,
 '={{ [$json.event_id, JSON.stringify($json)] }}');
const fresh=ifElse({version:2.2,config:{name:'New event?',position:[660,0],parameters:{conditions:{options:{caseSensitive:true,leftValue:'',typeValidation:'strict',version:2},conditions:[{id:'fresh',leftValue:'={{ $json.fresh }}',rightValue:true,operator:{type:'boolean',operation:'true',singleValue:true}}],combinator:'and'},options:{}}}});
const previous=code('Return previous outcome',900,275,`
const r=$input.first().json;
return [{json:{event_id:r.event_id,status:!r.same?'conflict':r.state==='reserved'||r.state==='uncertain'?'held':'duplicate',prior_state:r.state,message_id:r.message_id,simulated:false}}];`);
const flowId=runtime.flowId;
if(!/^[a-f0-9-]{36}$/.test(flowId)) throw Error('Expected local flow ID');
const lf=http('Run offline FAQ graph',900,`={{ 'http://flow:7860/api/v1/run/' + ($json.payload.fault === 'tool' ? '00000000-0000-4000-8000-000000000000' : '${flowId}') }}`,'flowCredential',
 '={{ {input_value: $json.payload.route, input_type:"chat", output_type:"chat", session_id:$json.event_id} }}');
const plan=code('Choose reply or human review',1120,0,`
const item=$input.first().json, e=$('Identify and classify').first().json;
const text=item.body?.outputs?.[0]?.outputs?.[0]?.results?.message?.text;
const expected="FAQ do piloto fictício: atendimento de segunda a sexta, das 09h às 17h. Categoria: "+e.route;
const toolOk=item.statusCode===200 && text===expected;
const route=toolOk?e.route:'tool_error';
const answer=route==='faq'?text:'';
return [{json:{event_id:e.event_id,route,answer,actor:e.actor,fault:e.fault,note:'[fixture '+e.event_id+'] '+(answer||'Revisão humana: '+route)+'. Apenas registro privado de laboratório.'}}];`);
for(const k of ['accountId','knownConversation','reviewConversation']) if(!Number.isSafeInteger(runtime[k])||runtime[k]<1) throw Error('Invalid Chatwoot ID');
const note=http('Record private note',1340,`={{ 'http://chat:3000/api/v1/accounts/${runtime.accountId}/conversations/' + ($json.actor==='fixture-001' ? '${runtime.knownConversation}' : '${runtime.reviewConversation}') + '/messages' }}`,'chatCredential',
 '={{ {content:$json.note, message_type:"outgoing", private:true} }}');
const reconcile=code('Reconcile note acknowledgment',1560,0,`
const r=$input.first().json, p=$('Choose reply or human review').first().json;
const ok=r.statusCode>=200 && r.statusCode<300 && Number.isInteger(r.body?.id) && r.body.private===true;
const state=(!ok||p.fault==='uncertain')?'uncertain':p.route==='faq'?'simulated':'handoff';
return [{json:{event_id:p.event_id,state,route:p.route,message_id:ok?r.body.id:null,reply:state==='simulated'?p.answer:null}}];`);
const finish=pg('Commit local outcome',1780,`UPDATE pilot_events SET state=$2, route=$3, message_id=$4::bigint, reply=$5, completed_at=now()
WHERE event_id=$1 AND state='reserved' RETURNING event_id,state AS status, route,message_id,reply,(state='simulated') AS simulated`,
'={{ [$json.event_id,$json.state,$json.route,$json.message_id,$json.reply] }}');
const w=workflow('synthetic-intake-v1',name).add(hook).to(normalize).to(claim)
  .to(fresh.onTrue(lf.to(plan.to(note.to(reconcile.to(finish))))).onFalse(previous));
const out=w.toJSON();
out.settings={executionOrder:'v1',timezone:'America/Cuiaba',saveDataErrorExecution:'none',saveDataSuccessExecution:'none',saveManualExecutions:false,saveExecutionProgress:false};
process.stdout.write(JSON.stringify({name,nodes:out.nodes,connections:out.connections,settings:out.settings}));
