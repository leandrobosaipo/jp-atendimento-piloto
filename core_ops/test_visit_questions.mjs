import assert from 'node:assert/strict';
import {askOne,answeredQuestion} from './visit_questions.mjs';
const realNow=Date.now;
const start=Date.parse('2030-08-16T07:00:00-04:00');
const members=['111','222'].map((id,i)=>({appointment_id:id,client_code:'9',service_code:id,professional_code:'1',appointment_date_iso:'2030-08-16',appointment_time:i?'10:00':'09:00',destination_e164:'+5511999999999'}));
const p={case:3,mid:'original',record:'record',notice:'notice',person:'hash',phone:members[0].destination_e164,members,question:'Confirmar ambos?',after:new Date(start).toISOString(),until:new Date(start+7200000).toISOString()};
let state={version:0,dell_reply_links:{k:{mid:p.mid,record:p.record,notice:p.notice,status:'human_hold',appointments:members.map(c=>c.appointment_id)}},individual_dispatches:members.map(c=>({...c,message_id:p.notice}))};
let o={untouched:true},confirmed=false,submitted=0,blocked=false;
const h={load:()=>structuredClone(state),save:s=>{state=s},instance:'fixture',blocked:()=>blocked,support:()=>false,questionObservation:()=>o,
  read:async c=>({...c,status:'1',confirmed,start:c.appointment_date_iso+'T'+c.appointment_time})};
const submit=async()=>{submitted++;return new Response(JSON.stringify({status:'provider_accepted',key:{fromMe:true,remoteJid:p.phone.slice(1)+'@s.whatsapp.net',id:'ask'}}),{status:201})};
try{
 Date.now=()=>start-1;assert.equal(await askOne(h,submit,[p]),null);
 Date.now=()=>start;confirmed=true;assert.equal(await askOne(h,submit,[p]),null);
 confirmed=false;blocked=true;assert.equal(await askOne(h,submit,[p]),null);
 blocked=false;assert.equal((await askOne(h,submit,[p])).status,'provider_accepted');
 assert.equal(await askOne(h,submit,[p]),null);assert.equal(submitted,1);
 const event={mid:'answer',at:new Date().toISOString(),pn:'hash',providerTime:true,latestDelivered:'ask',delivered:['ask']};
 o={answer:'answer',native:'ask'};
 assert.equal(answeredQuestion(event,state,h,[p]).members.length,2);
 for(const change of [{intervened:true},{pn:'wrong'},{quote:'old'},{delivered:[]},{mid:'other'}]){
   assert.equal(answeredQuestion({...event,...change},state,h,[p]),null);
 }
 o={};assert.equal(answeredQuestion(event,state,h,[p]),null);
}finally{Date.now=realNow;}
