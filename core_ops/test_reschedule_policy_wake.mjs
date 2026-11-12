import assert from 'node:assert/strict';
import {reschedulePolicyEligible} from './captured_same_day_reschedule.mjs';
const at='2030-01-02T10:00:00.000Z',policy={enabled:true,afterSequence:10,afterUtc:'2030-01-02T09:00:00.000Z'};
const link={status:'human_review',sourceEvent:{capture_sequence:11,at,providerTime:true,intervened:false,conflict:false,text:'Combinamos às 11h.'}};
assert.equal(reschedulePolicyEligible(link,policy,'2030-01-02T09:30:00.000Z'),true);
for(const event of [{...link.sourceEvent,text:'Combinamos às 11h mas quero cancelar.'},{...link.sourceEvent,conflict:true},{...link.sourceEvent,intervened:true},{...link.sourceEvent,capture_sequence:10}])assert.equal(reschedulePolicyEligible({...link,sourceEvent:event},policy,'2030-01-02T09:30:00.000Z'),false);
assert.equal(reschedulePolicyEligible(link,{enabled:true,afterSequence:10},'2030-01-02T09:30:00.000Z'),false);
console.log('reschedule wake: first valid capture only, high-water and holds PASS');
