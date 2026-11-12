import {createHash} from 'node:crypto';
const sha=value=>createHash('sha256').update(value).digest('hex');
const ok=new Set(['combinado','combinada','confirmo','sim','ok','👍','✅']);

// Confirmations after a changed visit refer to the message announcing the
// new appointment. The original notice snapshot still has the old time.
export async function confirmedVisitReturn(event,h){
  const text=String(event.text||'').normalize('NFC').toLowerCase().trim().replace(/[.! ]+$/u,'').replace(/[\u{1f3fb}-\u{1f3ff}\ufe0f]/gu,'');
  const stamp=Date.parse(event.at);
  if(!ok.has(text)||!event.providerTime||event.intervened||event.conflict||!event.latestDelivered ||
     !event.delivered.includes(event.latestDelivered)||event.quote&&event.quote!==event.latestDelivered||
     !Number.isFinite(stamp)||stamp>Date.now()+60000||Date.now()-stamp>3600000)return null;
  const state=h.load(),key=sha(JSON.stringify([event.mid,event.content])),existing=state.dell_reply_links?.[key];
  if(existing&&!['visit_review','identity_review'].includes(existing.status))return null;
  if((state.jp_reply_attempts||[]).some(x=>x.source_message_id===event.mid))return null;
  const rows=(state.jp_reply_ack_outbox||[]).filter(r=>r.state==='provider_accepted'&&
    r.provider_message_id===event.latestDelivered && (r.authorized_exception||r.kind==='reschedule') &&
    /^\+55\d{10,11}$/.test(r.destination_e164) && sha(r.destination_e164.slice(1)+'@s.whatsapp.net')===event.pn);
  if(rows.length!==1)return null;
  const parent=rows[0],c=parent.candidate;
  if(!c||parent.appointment_ids?.length!==1||parent.appointment_ids[0]!==c.appointment_id||
     !parent.accepted_at||!(Date.parse(parent.accepted_at)<=stamp+1000)||
     !(Date.parse(c.appointment_date_iso+'T'+c.appointment_time+':00-04:00')>Date.now())||
     h.blocked(state,c.destination_e164)||h.blocked(state,parent.destination_e164)||h.support(state,c.appointment_id))return null;
  const proof=await h.read(c);
  if(!proof?.confirmed||proof.status!=='1'||proof.appointment_id!==c.appointment_id||proof.client_code!==c.client_code||
     proof.professional_code!==c.professional_code||proof.service_code!==c.service_code||!proof.start?.startsWith(c.appointment_date_iso+'T'+c.appointment_time))return null;
  const latest=h.load();if(latest.version!==state.version)return null;
  const phone=parent.destination_e164;
  if((latest.jp_responses||[]).some(r=>r.message_id===event.mid))return null;
  latest.dell_reply_links||={};
  latest.dell_reply_links[key]={key,mid:event.mid,at:event.at,kind:event.kind,record:event.record,status:'official_confirmed',
    notice:parent.provider_message_id,appointments:[c.appointment_id],ackCandidates:[{...c,destination_e164:phone}],
    checkedAt:new Date().toISOString(),previous_status:existing?.status,source:'delivered_visit_feedback'};
  latest.jp_responses||=[];
  latest.jp_responses.push({appointment_id:c.appointment_id,client_code:c.client_code,destination_e164:phone,
    outbound_message_id:parent.provider_message_id,message_id:event.mid,at:event.at,text:event.text,source:'dell_capture',
    intent:'confirmed',official_update:{status:'confirmed',readback:proof,write_performed:false}});
  latest.version++;h.save(latest);
  return {status:'official_confirmed',members:1,noticeLinked:true};
}
