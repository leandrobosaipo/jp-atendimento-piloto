"""Deterministic source patch for reply-execute stage revision 4414148.

This is deliberately a source transformer, not a runtime deployer.  It fails
closed when the exact stage anchors are absent, so it cannot silently attach to
the legacy reply-plan path.
"""
IMPORT='import {runCapturedReschedule} from "/ops-app/captured_reschedule_pipeline.mjs";\nimport {executeRescheduleFeedback} from "/ops-app/reply_reschedule_feedback_executor.mjs";\n'
ANCHOR="const confirmation=await runCapturedConfirmation(capturedVisitHelpers);"
INSERT="""const confirmation=await runCapturedConfirmation(capturedVisitHelpers);
      const reschedule=await runCapturedReschedule(capturedVisitHelpers,rescheduleHelpers);
      const rescheduleFeedback=await executeRescheduleFeedback(capturedVisitHelpers);
"""
def inject(source):
    if source.count(ANCHOR)!=1 or source.count('stage===\'reply-execute\'')!=1:raise ValueError('reply_execute_4414148_anchor_missing')
    if 'runCapturedReschedule' in source or 'executeRescheduleFeedback' in source:raise ValueError('same_day_reschedule_already_present')
    return IMPORT+source.replace(ANCHOR,INSERT).replace('confirmation, acknowledgement','confirmation, reschedule, rescheduleFeedback, acknowledgement')
