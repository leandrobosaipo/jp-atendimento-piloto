import assert from 'node:assert/strict';
import {matchMoveSource,reschedulePolicyEligible} from './captured_same_day_reschedule.mjs';
import {createHash} from 'node:crypto';
const at='2030-01-02T10:00:00.000Z',policy={enabled:true,afterSequence:10,afterUtc:'2030-01-02T09:00:00.000Z'};
const link={status:'human_review',sourceEvent:{capture_sequence:11,at,providerTime:true,intervened:false,conflict:false,text:'Combinamos às 11h.'}};
assert.equal(reschedulePolicyEligible(link,policy,'2030-01-02T09:30:00.000Z'),true);
for(const event of [{...link.sourceEvent,text:'Combinamos às 11h mas quero cancelar.'},{...link.sourceEvent,conflict:true},{...link.sourceEvent,intervened:true},{...link.sourceEvent,capture_sequence:10}])assert.equal(reschedulePolicyEligible({...link,sourceEvent:event},policy,'2030-01-02T09:30:00.000Z'),false);
assert.equal(reschedulePolicyEligible(link,{enabled:true,afterSequence:10},'2030-01-02T09:30:00.000Z'),false);
for(const text of ['Combinamos às 11h?', 'Combinamos às 11h mas ficou muito cedo.', 'Pode remarcar para 11h com outra pessoa?']){
 assert.equal(reschedulePolicyEligible({...link,sourceEvent:{...link.sourceEvent,text}},policy,policy.afterUtc),false);
}
const customer={client_code:'22',appointment_id:'8',service_code:'9',professional_code:'16897547',destination_e164:'+5500000000000',appointment_date:'02/01/2030',appointment_time:'10:00'};
const notice={...customer,message_id:'aviso',review_id:'revision',revision:1,content_sha256:'texto',conversation_snapshot:{...customer,text_sha256:'texto'}};
const request={key:'item',notice:'aviso',appointments:['8'],sourceEvent:{mid:'resposta',pn:createHash('sha256').update('5500000000000@s.whatsapp.net').digest('hex'),delivered:['aviso'],text:'Pode remarcar para 11h?',at}};
const state={dell_reply_links:{item:request},individual_dispatches:[notice],reviews:[notice],jp_responses:[{source:'dell_capture',message_id:'resposta',outbound_message_id:'aviso',appointment_id:'8'}]};
const helpers={target:()=>({candidate:customer,dispatch:notice,members:[customer]}),window:()=>true,support:()=>false,blocked:()=>false,feedback:(c,s)=>({reminder_hold:s.jp_responses.length>0})};
assert.equal(matchMoveSource(request,state,helpers).status,'matched');
assert.equal(matchMoveSource(request,state,{...helpers,blocked:()=>true}).status,'human_hold');
assert.equal(matchMoveSource(request,{...state,jp_responses:[...state.jp_responses,{message_id:'anterior'}]},helpers).status,'human_hold');
console.log('reschedule wake: first valid capture only, high-water and holds PASS');
