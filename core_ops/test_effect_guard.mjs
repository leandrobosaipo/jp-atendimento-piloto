import assert from "node:assert/strict";
import test from "node:test";
import {randomUUID} from "node:crypto";
import {nativeScope,wireOperation,approveBody} from "./effect_guard.mjs";
const t={task_id:randomUUID(),client_code:"12",destination_e164:"+5511999999999",source_sha256:"c".repeat(64),status:"planned",plan:{kind:"book"}};
const a={attempt_id:randomUUID(),task_id:t.task_id,client_code:t.client_code,destination_e164:t.destination_e164,source_sha256:t.source_sha256,kind:"create",status:"write_unknown",attempted_at:new Date().toISOString(),option:{date:"2030-01-10",time:"10:00",service_code:"51",professional_code:"71",duration_minutes:30}};
const body={cliente:"12",profissional:"71",item:"51",dataagendamento:"2030-01-10 10:00",duracao:"30",sms:"0",whats:"0",lembrete:"0",observacao:"JP nova visita "+randomUUID()};
test("native write binds only its persisted matching task, option and one intent",()=>{
  const call=(rows,b=body)=>nativeScope({task:t.task_id},{jp_reply_tasks:[t],jp_reply_attempts:rows},"create",b);
  assert.equal(call([a]).client,"12");assert.throws(()=>call([]));assert.throws(()=>call([a,a]));assert.throws(()=>call([a],{...body,item:"other"}));
  assert.equal(approveBody("create",body),approveBody("create",{...body,observacao:"JP nova visita "+randomUUID()}));
  assert.throws(()=>approveBody("create",{...body,whats:"1"}));
  assert.throws(()=>wireOperation("https://sistema.appbarber.com.br/pages/actions/cancelaHorario.php",{method:"POST",redirect:"manual",body:"agendamento=10"}));
});
