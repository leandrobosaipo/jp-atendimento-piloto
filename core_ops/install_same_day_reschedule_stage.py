"""Deterministic source patch for reply-execute stage revision 4414148.

This is deliberately a source transformer, not a runtime deployer.  It fails
closed when the exact stage anchors are absent, so it cannot silently attach to
the legacy reply-plan path.
"""
IMPORT='import {runCapturedReschedule} from "/ops-app/captured_reschedule_pipeline.mjs";\nimport {executeRescheduleFeedback} from "/ops-app/reply_reschedule_feedback_executor.mjs";\nimport {createSameDayRescheduleHelpers} from "/ops-app/same_day_reschedule_helpers.mjs";\n'
ANCHOR="const confirmation=await runCapturedConfirmation("
INSERT="""let reschedule={status:'idle'};
        try{if(dispatchReady)reschedule=await runCapturedReschedule(capturedVisitHelpers,rescheduleHelpers);}catch{reschedule={status:'held'};}
        let rescheduleFeedback={status:'idle'};
        try{if(dispatchReady)rescheduleFeedback=await executeRescheduleFeedback(capturedVisitHelpers);}catch{rescheduleFeedback={status:'held'};}
        const confirmation=await runCapturedConfirmation("""
HELPER_ANCHOR='serveCapture(capturedVisitHelpers);'
HELPER_INSERT='const rescheduleHelpers=createSameDayRescheduleHelpers({load,agendaConfig,moveOfficialAppointment,fetchImpl:fetch});\nserveCapture(capturedVisitHelpers);'
STAGE_MARKER="const dispatchReady=dispatchSyncProof(load(),identity.executionId,true);"
def inject(source):
    if source.count(ANCHOR)!=1 or source.count(HELPER_ANCHOR)!=1 or source.count(STAGE_MARKER)!=1:raise ValueError('reply_execute_4414148_anchor_missing')
    if 'runCapturedReschedule' in source or 'executeRescheduleFeedback' in source:raise ValueError('same_day_reschedule_already_present')
    return IMPORT+source.replace(HELPER_ANCHOR,HELPER_INSERT).replace(ANCHOR,INSERT).replace('confirmation:confirmation.status,attempted:', 'confirmation:confirmation.status,reschedule:reschedule.status,rescheduleFeedback:rescheduleFeedback.status,attempted:')
