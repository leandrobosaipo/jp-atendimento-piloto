/* The active reply-execute stage calls this companion after confirmation.
 * It prepares a capture-side proposal, then only consumes a grant issued by
 * the independent Python capture process.  It never opens a WA session. */
import {prepareCapturedSameDayReschedule} from './captured_same_day_reschedule.mjs';
import {executePreparedReschedule,queueRescheduleFeedback} from './captured_reschedule_executor.mjs';

export async function runCapturedReschedule(h,{grantPresent,recheck,move,fetchImpl}={}){
 const prepared=await prepareCapturedSameDayReschedule(h,{fetchImpl});
 if(prepared.status!=='grant_required'&&!Object.values(h.load().dell_reply_links||{}).some(link=>link.reschedule?.state==='grant_required'))return prepared;
 return executePreparedReschedule(h,{grantPresent,recheck,move,enqueueFeedback:async(state,p)=>{
   queueRescheduleFeedback(state,p);state.version=(state.version||0)+1;h.save(state);
 }});
}
