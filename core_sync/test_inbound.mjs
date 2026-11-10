import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {inboundRoute,retainInbound} from "./inbound.mjs";
const sha=v=>crypto.createHash("sha256").update(v).digest("hex");
function fixture(t){
 const stateDir=fs.mkdtempSync(path.join(os.tmpdir(),"inbound-test-"));
 for(const n of ["admissions","inbound-receipts"])fs.mkdirSync(path.join(stateDir,n));
 t.after(()=>fs.rmSync(stateDir,{recursive:true,force:true}));
 const body={event:"MESSAGES_UPSERT",instance:"fixture",data:{key:{fromMe:false,id:"test-key-1",remoteJid:"fixture@lid"},messageTimestamp:1,message:{conversation:"Teste sintético"}}};
 const raw=Buffer.from(JSON.stringify(body)),id=sha("record"),workflow="test-workflow";let calls=0,found=[];
 const input={raw,id,workflow,execution:"42",auth:"Bearer fixture-secret",expectedWorkflow:workflow,token:"fixture-secret",grant:{until:new Date(Date.now()+30000).toISOString(),paths:[inboundRoute],records:{[id]:sha(raw)}},instance:"fixture",stateDir,find:()=>found,
   forward:async bytes=>{calls++;assert.deepEqual(bytes,raw);found=[{instance:"fixture",message_id:"test-key-1",direction:"inbound",source:"authenticated_n8n_durable_inbox",at:"1970-01-01T00:00:01.000Z",identity_hash:sha("fixture@lid"),text_sha256:sha(body.data.message.conversation),conversation_id:null}];return {ok:true,body:{ok:true,ignored:true,reason:"before_n8n_activation"}};}};
 return {input,body,calls:()=>calls};
}
test("native readback and prior execution receipt",async t=>{
 const {input,calls}=fixture(t);
 const first=await retainInbound(input),next=await retainInbound({...input,execution:"43"});
 assert.deepEqual(first,{status:200,body:{ok:true,record_id:input.id,workflow:input.workflow,execution:"42",status:"retained"}});
 assert.deepEqual(next,first);assert.equal(calls(),1);
});
test("changed bytes, privilege escalation, active input and bad scope are blocked before native admission",async t=>{
 const {input,body,calls}=fixture(t);
 const invalid=[{...input,raw:Buffer.concat([input.raw,Buffer.from(" ")])},{...input,token:""},{...input,auth:"Bearer other"},{...input,workflow:"other"},{...input,grant:{...input.grant,paths:[]}}];
 for(const o of [{...body,historical_review_import:false},{...body,event:"messages.update"},{...body,instance:"other"},{...body,data:{...body.data,messageTimestamp:2}},{...body,data:{...body.data,key:{...body.data.key,fromMe:true}}}]){
  const raw=Buffer.from(JSON.stringify(o));invalid.push({...input,raw,grant:{...input.grant,records:{[input.id]:sha(raw)}}});
 }
 for(const i of invalid)assert.ok((await retainInbound(i)).status>=400);
 assert.equal(calls(),0);
});
test("lost native result or missing durable readback is held across executions",async t=>{
 const {input}=fixture(t);let calls=0;
 const uncertain={...input,forward:async()=>{calls++;throw Error("unknown");}};
 assert.equal((await retainInbound(uncertain)).status,409);assert.equal((await retainInbound({...uncertain,execution:"43"})).status,409);assert.equal(calls,1);
 const a=fixture(t).input;
 const result=await retainInbound({...a,find:()=>[]});assert.equal(result.status,409);
});
test("overlapping record submissions call native handler once",async t=>{
 const {input,calls}=fixture(t);let open;const gate=new Promise(done=>{open=done;});
 const first=retainInbound({...input,forward:async bytes=>{await gate;return input.forward(bytes);}});
 assert.equal((await retainInbound({...input,execution:"43"})).status,409);open();assert.equal((await first).status,200);assert.equal(calls(),1);
});
