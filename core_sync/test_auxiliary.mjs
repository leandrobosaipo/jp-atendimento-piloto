import test from "node:test";
import assert from "node:assert/strict";
import {scenarios,seed,checkRequest,checkResult} from "./auxiliary.mjs";

function fixture(scenario){
  const plan={scenario,workflow:"fixture",nodes:scenarios[scenario].map(path=>({path})),capability:"z".repeat(40)};
  const state={jp_jobs:[],reactivation:{}};const files={};const env=seed(plan,state,(k,v)=>{files[k]=v;},"/fixture");
  return {plan,state,env,files};
}
test("batch denial preserves the capability and rejects a different execution",()=>{
  const {plan,state}=fixture("batch"),r="/api/confirmation-batch/run",body={execution_id:"10",job_id:"fixture-batch",capability:plan.capability};
  checkRequest(plan,r,body,"10");assert.throws(()=>checkRequest(plan,r,body,"11"));
  assert.deepEqual(checkResult(plan,r,{status:409},{error:"n8n_send_gate_disabled"},state),{gate:"n8n_send_gate_disabled",batch_unconsumed:true});
  state.jp_jobs[0].capability_consumed_at="unexpected";assert.throws(()=>checkResult(plan,r,{status:409},{error:"n8n_send_gate_disabled"},state));
});
test("registration keeps unresolved writes and requires a disabled update in the fresh case",()=>{
  const past=fixture("contact-queue"),fresh=fixture("contact-hook"),p="/api/contact-registration/";
  assert.equal(past.state.contact_registration_tasks[0].write_attempts,1);
  assert.deepEqual(checkResult(past.plan,p+"claim",{ok:true},{ready:false,status:"unknown"},past.state),{ready:false,status:"unknown"});
  assert.throws(()=>checkResult(past.plan,p+"result",{ok:true},{ok:true,status:"unknown",consumer_verified:true},past.state));
  assert.equal(fresh.files["contact-config.json"].enabled,false);
  assert.throws(()=>checkResult(fresh.plan,p+"update",{ok:true},{status:"updated"},fresh.state));
});
test("brand rules distinguish an empty approved queue from commercial priority",()=>{
  const off=fixture("brand-paused"),on=fixture("brand-priority");
  assert.equal(off.env.JP_BRAND_SEND_ENABLED,"0");assert.equal(on.env.JP_BRAND_SEND_ENABLED,"1");
  assert.deepEqual(checkResult(off.plan,"/brand-due",{ok:true},[],off.state),{pending:0});
  assert.throws(()=>checkResult(on.plan,"/brand-send",{ok:true},{ok:true,attempted:1,message_sent:true},on.state));
});
