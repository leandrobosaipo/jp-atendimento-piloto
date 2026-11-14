import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {createHash,randomUUID} from "node:crypto";
import {operational} from "./admission.mjs";
import {acceptedKey,records,resolveAttempt} from "./dispatch.mjs";
const sha=s=>createHash("sha256").update(s).digest("hex");
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
