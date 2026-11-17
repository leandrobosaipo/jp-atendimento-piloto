"""Release stale full-state snapshots before awaiting a confirmation send.

No records, eligibility checks, dispatch permissions or retry rules are changed.
The production transform is bound to the exact inspected runtime.
"""
import hashlib

SOURCE_SHA256 = '97367a1dd04edcffb0ddb661df2229092f6443fa4c508984ac805b31cdc5a4cb'


def transform(source):
    def once(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError('confirmation_memory_anchor_changed')
        source = source.replace(old, new, 1)

    once('const cfg=readManualConfig(individualConfigFile),before=load(),candidate=candidates().rows.find(c=>c.candidate_id===item.candidate_id);',
         'const cfg=readManualConfig(individualConfigFile);let before=load();const candidate=candidates().rows.find(c=>c.candidate_id===item.candidate_id);')
    once("  if(individualConfirmationBusy||rescheduleOffers.isBusy()||job.initial_plan&&(appointmentMoveBusy||agendaJobBusy))throw Error('individual_send_busy');",
         "  before=null; // Keep only the validated candidate across official reads.\n  if(individualConfirmationBusy||rescheduleOffers.isBusy()||job.initial_plan&&(appointmentMoveBusy||agendaJobBusy))throw Error('individual_send_busy');")
    once('    const latest=load(),live=candidates().rows.find(c=>c.candidate_id===item.candidate_id);',
         '    let latest=load();const live=candidates().rows.find(c=>c.candidate_id===item.candidate_id);')
    once("audit(latest,'batch_individual_confirmation_attempted',review.review_id,job.operator);latest.version++;save(latest);attempted=true;",
         "audit(latest,'batch_individual_confirmation_attempted',review.review_id,job.operator);latest.version++;save(latest);attempted=true;latest=null; // The durable attempt is reloaded after delivery.")
    once(",state=load(),sync=state.initial_confirmation_source_sync;\n  const controlled=state.dispatch_sync_policy?.enabled===true,dailyDue=",
         ";\n  const {sync,controlled}=(()=>{const state=load();return {sync:state.initial_confirmation_source_sync,controlled:state.dispatch_sync_policy?.enabled===true};})();\n  const dailyDue=")
    lines=source.splitlines()
    matches=[line for line in lines if line.startswith('      initialAttemptGuard(plan);const latest=load(),candidate=')]
    if len(matches)!=1 or not matches[0].endswith("status:'queued'};"):
        raise ValueError('confirmation_memory_plan_anchor_changed')
    line=matches[0]
    once(line,line.replace('initialAttemptGuard(plan);const latest=',
                          'initialAttemptGuard(plan);const {text,item}=(()=>{const latest=',1)+
         'return {text,item};})();')
    once('const state=load();let due=true;try{due=dispatchDueWithin(state,Date.now())}catch{}const ready=dispatchSyncProof(state,identity.executionId,due),result=await runReminderTick(identity.executionId,ready,stage);',
         'const {due,ready}=(()=>{const state=load();let due=true;try{due=dispatchDueWithin(state,Date.now())}catch{}return {due,ready:dispatchSyncProof(state,identity.executionId,due)};})();const result=await runReminderTick(identity.executionId,ready,stage);')
    return source


def patch(source):
    if hashlib.sha256(source.encode()).hexdigest()!=SOURCE_SHA256:
        raise ValueError('confirmation_runtime_changed')
    return transform(source)
