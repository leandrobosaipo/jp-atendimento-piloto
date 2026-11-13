import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {createHash,randomUUID} from "node:crypto";
import {operational} from "./admission.mjs";
import {acceptedKey,records,resolveAttempt} from "./dispatch.mjs";
const sha=s=>createHash("sha256").update(s).digest("hex");
test("busy reply preserves the error source without repeating its operation",async t=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"jp-error-"));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const route="/api/n8n/stages/record-error",workflow="handler",node="record",token="x".repeat(40);
  fs.mkdirSync(path.join(dir,"admissions"));
  fs.writeFileSync(path.join(dir,"ownership.json"),JSON.stringify({mode:"owned",roles:["error"],owner:"dell",epoch:randomUUID(),until:new Date(Date.now()+60000)}));
  let calls=0;
  const handle=operational({manifest:{error:{workflow,nodes:{[node]:route}}},secrets:{owner:"dell",roles:{error:token}},stateDir:dir,
    inner:async()=>{calls++;return {status:409,body:{ok:false,error:"reply_operation_busy"}}}});
  const req={route,workflow,node,execution:"41",authorization:"Bearer "+token,raw:Buffer.from(JSON.stringify({source_workflow_id:"reply",source_execution_id:"40",last_node:"private fixture name"}))};
  assert.equal((await handle(req)).body.storage,"admissions");
  const done=JSON.parse(fs.readFileSync(path.join(dir,"admissions/handler-41-record.done")));
  assert.equal(done.inner_status,409);assert.equal(done.source_execution_id,"40");assert.equal(done.source_workflow_id,"reply");assert.equal(done.last_node,undefined);
  assert.equal((await handle(req)).status,409);assert.equal(calls,1);
  assert.equal((await handle({...req,execution:"42",raw:Buffer.from("{}")})).status,409);
});
test("inbound fences owner, role, exact envelope and duplicate executions",async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"inbound-"));t.after(()=>fs.rmSync(root,{recursive:true}));
  fs.mkdirSync(path.join(root,"admissions"));
  const instance="fixture",epoch=randomUUID(),workflow="fixture01",node="receive",route="/api/n8n/inbound",token="a".repeat(40);
  const raw=Buffer.from(JSON.stringify({instance,event:"messages.upsert",data:{}})),len=Buffer.alloc(4);
  len.writeUInt32BE(Buffer.byteLength(instance));const record=sha(Buffer.concat([len,Buffer.from(instance),raw]));
  const req={epoch,workflow,node,route,raw,record,execution:"71",authorization:"Bearer "+token};
  const lease=mode=>fs.writeFileSync(path.join(root,"ownership.json"),JSON.stringify({mode,epoch,roles:["inbound"],owner:"dell",until:new Date(Date.now()+60000)}));
  let calls=0;
  const handle=operational({manifest:{inbound:{workflow,nodes:{[node]:route}}},secrets:{owner:"dell",roles:{inbound:token}},
    instance,stateDir:root,inner:async input=>{calls++;assert.deepEqual(input.raw,raw);return {status:202,body:{ok:true,attendance:"confirmed"}};}});
  lease("maintenance");assert.equal((await handle(req)).status,423);lease("rehearsal");
  for(const patch of [{epoch:randomUUID()},{record:"0".repeat(64)},{workflow:"other"},{authorization:"bad"},{raw:Buffer.from('{"instance":"foreign"}')}])
    assert.equal((await handle({...req,...patch})).status,403);
  const first=await handle(req);assert.equal(first.body.status,"processed");assert.deepEqual(await handle(req),first);
  assert.equal((await handle({...req,execution:"72"})).status,409);assert.equal(calls,1);
});
test("maintenance, ownership, distinct preflights, immutable attempt and close",async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"central-"));t.after(()=>fs.rmSync(root,{recursive:true}));
  fs.mkdirSync(root+"/admissions");
  const token="a".repeat(40),epoch=randomUUID(),workflow="fixture001",execution="112";
  function gate(mode){fs.writeFileSync(root+"/ownership.json",JSON.stringify({mode,epoch,owner:"fixture",roles:["clock"],until:new Date(Date.now()+60000)}));}
  const prefix="/api/n8n/stages/",nodes={ordinary:prefix+"before-dispatch",forced:prefix+"before-dispatch",send:prefix+"send-initial",reactivate:prefix+"reactivation"};
  let calls=0;const handle=operational({manifest:{clock:{workflow,nodes,forced:["forced"]}},secrets:{owner:"fixture",roles:{clock:token}},stateDir:root,
    inner:async()=>{calls++;return {status:200,body:{execution_id:execution,ready:true,refreshed:true}};}});
  const run=(node,body={},authorization="Bearer "+token)=>handle({workflow,execution,node,authorization,route:nodes[node],raw:Buffer.from(JSON.stringify(body))});
  gate("maintenance");assert.equal((await run("ordinary")).status,423);gate("rehearsal");
  assert.equal((await run("ordinary",{},"wrong")).status,403);assert.equal((await run("send")).status,409);
  assert.equal((await run("ordinary")).status,200);assert.equal((await run("send")).status,200);
  assert.equal((await run("reactivate")).status,409);assert.equal((await run("forced",{force:true})).status,200);
  assert.equal((await run("reactivate")).status,200);assert.equal((await run("reactivate")).status,409);gate("maintenance");
  assert.equal((await run("ordinary")).status,423);assert.equal(calls,4);
});
test("new durable attempt requires exact destination and content, rejecting ambiguity",()=>{
 const text="Fixture",number="1234567890",base={attempted_at:"2026-01-01",status:"delivery_unknown",source_message_id:"fake-in",kind:"send",attempt_id:"fake-a",destination_e164:"+"+number,content_sha256:sha(text)};
 const rows=records({jp_reply_attempts:[base]});assert.equal(rows.length,1);assert.equal(resolveAttempt(new Set(),rows,"sendText",{text,number}),rows[0]);
 for(const bad of [[new Set([rows[0].id]),rows,{text,number}],[new Set(),[...rows,...rows],{text,number}],[new Set(),rows,{text:"other",number}],[new Set(),rows,{text,number:number+"@lid"}]])
   assert.throws(()=>resolveAttempt(bad[0],bad[1],"sendText",bad[2]));
});
test("one selected visit covering two appointments requires its complete, consistent native cohort",()=>{
 const text="Approved visit",number="1234567890",g={group_id:"fixture-group",group_sha256:sha("fixture"),appointment_ids:["1","2"]};
 const a={review_id:"review",visit_group:g,visit_attempt_id:"same-attempt",outbound_budget_reservation_id:"budget",content_sha256:sha(text),destination_e164:"+"+number,status:"delivery_unknown",attempted_at:"2030-01-01T00:00:00Z",appointment_id:"1"};
 const b={...a,appointment_id:"2"};
 function resolve(rows){return resolveAttempt(new Set(),records({individual_dispatches:rows}),"sendText",{number,text});}
 assert.ok(resolve([a,b]).id);
 for(const rows of [[a],[a,a],[a,{...b,review_id:"other"}],[a,{...b,status:"provider_accepted"}],[a,{...b,content_sha256:sha("other")}],[a,{...b,destination_e164:"+1999999999"}]])assert.throws(()=>resolve(rows),/unbound_native_attempt/);
});
test("reactivation intake binds its own durable receipt and source messages",()=>{
 const number="1234567890",text="Fixture reply",a={request_id:"intake",source_sha256:sha("plan"),source_message_ids:["incoming"],attempt_id:"intake-part",destination_e164:"+"+number,text_sha256:sha(text),status:"delivery_unknown",attempted_at:"2030-01-01T00:00:00Z"};
 const rows=x=>records({reactivation:{intake_runtime:{attempts:[x]}}});
 assert.ok(resolveAttempt(new Set(),rows(a),"sendText",{number,text}).id);
 assert.deepEqual(rows({...a,source_message_ids:[]}),[]);
});
test("Brazilian ninth digit only with the provider-bound receipt for this exact attempt",()=>{
 const short="551168000001",long="5511968000001",jid=n=>n+"@s.whatsapp.net";
 const response=(a,b)=>({status:"provider_accepted",key:{id:"fixture-a",fromMe:true,remoteJid:jid(b)},recipient_binding:{submitted:a,canonical_jid:jid(b),basis:"native_lookup"}});
 const check=(a,o)=>acceptedKey({payload:{number:a}},o);
 assert.ok(check(long,response(long,short)));assert.ok(check(short,response(short,long)));
 const exact=response(long,long);delete exact.recipient_binding;assert.ok(check(long,exact));
 const unproven=response(long,short);delete unproven.recipient_binding;assert.equal(check(long,unproven),false);
 for(const bad of [response(short,short),response(long,"552168000001"),response(long,"551168000002"),response(long,"5511368000001")])assert.equal(check(long,bad),false);
 const landline="551135000001",wrong=response("5511935000001",landline);assert.equal(check("5511935000001",wrong),false);
 const lid=response(long,short);lid.key.remoteJid=short+"@lid";assert.equal(check(long,lid),false);
 const altered=response(long,short);altered.recipient_binding.basis="normalized";assert.equal(check(long,altered),false);
});
