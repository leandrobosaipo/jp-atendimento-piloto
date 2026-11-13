"""Apply the notice integration to the deployed, hash-pinned Dell application."""
import hashlib

BASE='6a4ef4f1439b7372b7ded097cf36f08250429de8a0a45559042d39700cda0519'

def patched(text):
    if hashlib.sha256(text.encode()).hexdigest()!=BASE:
        raise ValueError('changed_dell_source')
    def once(old,new):
        nonlocal text
        if text.count(old)!=1:raise ValueError('changed_anchor: '+old[:45])
        text=text.replace(old,new)
    once('import {serveCapture,officialFetchFor}', 'import {noticeFetch,clarificationFor} from "/ops-app/agenda_notice.mjs";\nimport {serveCapture,officialFetchFor}')
    once("preflightIndividualConfirmation(member,cfg));","preflightIndividualConfirmation(member,cfg,automatic?{fetchImpl:noticeFetch(member)}:{}));")
    once("readOfficialConfirmation(members[i],session,fetch,true)","readOfficialConfirmation(members[i],session,automatic?noticeFetch(members[i]):fetch,true)")
    once("const proof=await preflightIndividualConfirmation(candidate,cfg);\n    if(job.initial_plan)", "const proof=await preflightIndividualConfirmation(candidate,cfg,job.initial_plan?{fetchImpl:noticeFetch(candidate)}:{});\n    if(job.initial_plan)")
    once("agenda?.session?.expires_at},fetch,true);if(proof.official_readback.confirmed)", "agenda?.session?.expires_at},noticeFetch(candidate),true);if(proof.official_readback.confirmed)")
    once("await readInitialVisitMembership(plan,agenda,{ignoredAppointmentIds:ignored})", "await readInitialVisitMembership(plan,agenda,{fetchImpl:noticeFetch(plan.appointments[0]),ignoredAppointmentIds:ignored})")
    once("let proof;try{for(const member of row.visit_group?dispatchMembers(row):[candidate]){const readback=await readOfficialConfirmation(member,agendaConfig(),fetch,true);if(!readback.confirmed)throw Error('official_not_confirmed');await preflightIndividualConfirmation(member,readManualConfig(individualConfigFile));", """let proof;try{
        const members=row.visit_group?dispatchMembers(row):[candidate];
        if(!members.length||row.visit_group&&members.length!==row.visit_group.appointment_ids.length)throw Error('visit_changed');
        await readInitialVisitMembership({appointments:members,appointment_ids:members.map(c=>c.appointment_id)},agendaConfig(),{fetchImpl:noticeFetch(candidate)});
        for(const member of members){const reader=noticeFetch(member),readback=await readOfficialConfirmation(member,agendaConfig(),reader,true);
          const previous=responseRecord(state,member.appointment_id)?.official_update?.readback;
          if(!readback.confirmed||!previous?.item_code||!['appointment_id','item_code','start','end','client_code','professional_code','service_code'].every(k=>previous[k]===readback[k]))throw Error('official_not_confirmed');
          await preflightIndividualConfirmation(member,readManualConfig(individualConfigFile),{fetchImpl:reader});""")
    once("if(Object.values(state.dell_reply_links||{}).some(r=>r.appointments.includes(candidate.appointment_id)&&['human_hold','human_review','awaiting_official'].includes(r.status)))", """const clarified=clarificationFor(state,responseRecord(state,candidate.appointment_id),candidate.appointment_id);
  if(Object.values(state.dell_reply_links||{}).some(r=>r.mid!==clarified?.original&&r.appointments.includes(candidate.appointment_id)&&['human_hold','human_review','awaiting_official'].includes(r.status)))""")
    once("if(!sameDestination(record.destination_e164,candidate.destination_e164)||!dispatches.some(d=>d.message_id===record.outbound_message_id&&sameDestination(d.destination_e164,candidate.destination_e164)))", """const sourceNotice=clarificationFor(state,record,id)?.notice||record.outbound_message_id;
  if(!sameDestination(record.destination_e164,candidate.destination_e164)||!dispatches.some(d=>d.message_id===sourceNotice&&sameDestination(d.destination_e164,candidate.destination_e164)))""")
    return text
