import test from "node:test";
import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {checkTask,withTask} from "./task_reads.mjs";
test("task must still match the encrypted native queue and an admitted reply request",()=>{
  const t={task_id:randomUUID(),status:"queued",source_sha256:"1".repeat(64),source_message_id:"fake",destination_e164:"+5511988880000",client_code:"3"};
  const state={jp_reply_tasks:[t]};
  assert.equal(checkTask(t,state).client,"3");
  for(const k of ["source_sha256","source_message_id","destination_e164","client_code","task_id"])assert.throws(()=>checkTask({...t,[k]:"wrong"},state));
  for(const status of ["delivery_unknown","human","superseded"])assert.throws(()=>checkTask(t,{jp_reply_tasks:[{...t,status}]}));
  assert.throws(()=>withTask(t,()=>state,()=>true));
});
