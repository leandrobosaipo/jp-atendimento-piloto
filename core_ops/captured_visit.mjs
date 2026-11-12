import http from 'node:http';
import fs from 'node:fs';
import {createHash, timingSafeEqual} from 'node:crypto';

const digest = value => createHash('sha256').update(value).digest('hex');
const hashPattern = /^[a-f0-9]{64}$/;

export function matchVisit(event, state, helpers) {
  const dispatches = state.individual_dispatches || [];
  const byDestination = dispatches.filter(d => {
    if (!d.destination_e164 || !d.message_id) return false;
    const jid = d.destination_e164.replace(/^\+/, '') + '@s.whatsapp.net';
    return event.pn === digest(jid) || event.delivered.includes(d.message_id);
  });
  if(new Set(byDestination.map(d=>d.destination_e164)).size!==1)return {status:'identity_review'};
  const phone = byDestination[0]?.destination_e164.replace(/^\+/, '');
  if (!phone) return {status:'identity_review'};
  const target = helpers.target(state, {phone, at:event.at, quoted_message_id:event.quote || null}, false);
  if (!target) return {status:'visit_review'};
  const {candidate, dispatch, members} = target;
  if (!candidate || !dispatch || !members?.length || !byDestination.includes(dispatch)
      || !event.delivered.includes(dispatch.message_id)
      || !helpers.window(dispatch, candidate, event.at)
      || event.quote && event.quote !== dispatch.message_id
      || members.some(c => c.client_code !== candidate.client_code || c.destination_e164 !== candidate.destination_e164
        || c.source_active === false || c.official_status === 'realized')) return {status:'visit_review'};
  const snapshot=dispatch.conversation_snapshot,review=(state.reviews||[]).find(r=>r.review_id===dispatch.review_id && r.revision===dispatch.revision && r.content_sha256===dispatch.content_sha256);
  if (!snapshot || snapshot.client_code!==candidate.client_code || snapshot.text_sha256!==dispatch.content_sha256
    || (!dispatch.visit_group && (!review || review.appointment_id!==candidate.appointment_id || review.appointment_date!==candidate.appointment_date || review.appointment_time!==candidate.appointment_time
      || snapshot.professional_code!==candidate.professional_code || snapshot.service_code!==candidate.service_code)))return {status:'visit_review'};
  const held = (event.humanNotices||[]).includes(dispatch.message_id) || helpers.blocked(state, candidate.destination_e164) || members.some(c =>
    c.opt_out || helpers.support(state,c.appointment_id) || helpers.feedback(c,state)?.reminder_hold);
  return {status:held?'human_hold':'matched',candidate,dispatch,members};
}

export async function consumeVisit(event, h, preview=false) {
  if (!event || !hashPattern.test(event.record) || !hashPattern.test(event.content)
      || !Array.isArray(event.delivered) || typeof event.mid!=='string' || !event.mid || event.mid.length>200
      || !Number.isFinite(Date.parse(event.at)) || Date.parse(event.at)>Date.now()+60000)
    throw Error('bad_envelope');
  const key = digest(JSON.stringify([event.mid,event.content]));
  let state=h.load();
  let receipt=state.dell_reply_links?.[key];
  const retry=receipt?.status==='awaiting_official';
  if (receipt && !retry) return {status:receipt.status,members:receipt.appointments.length,duplicate:true};
  let target=matchVisit(event,state,h);
  const appointments=(target.members||[]).map(c=>c.appointment_id);
  const notice=target.dispatch?.message_id;
  const prior=state.jp_responses||[];
  const expired=target.members?.some(c=>Date.parse(c.appointment_date_iso+'T'+c.appointment_time+':00-04:00')<=Date.now());
  let status=target.status;
  const intent=event.disposition==='possible_confirmation' ? 'confirmed' : 'human_review';
  if (status==='matched'){
    if (expired) status='visit_elapsed';
    else if (intent!=='confirmed' || event.conflict || !event.providerTime || event.intervened || prior.some(r=>r.message_id===event.mid && r.source!=='dell_capture')) status='human_review';
    else status='awaiting_official';
  }
  let proofs=[];
  if(status==='awaiting_official' && !preview){
    for (const c of target.members) {
      try { proofs.push(await h.read(c)); } catch { break; }
    }
    state=h.load();
    const check=matchVisit(event,state,h);
    const same=check.status==='matched' && check.dispatch.message_id===notice && JSON.stringify(check.members.map(h.snapshot))===JSON.stringify(target.members.map(h.snapshot));
    if(!same) status='human_review';
    else if(proofs.length===appointments.length && proofs.every((p,i)=>p.confirmed===true && p.status==='1' && p.appointment_id===appointments[i])) status='official_confirmed';
    else status='awaiting_official';
    target=check;
  }
  if(preview)return {status,noticeLinked:!!notice,members:appointments.length,kind:event.kind};
  const seen=state.dell_reply_links||={};
  if (seen[key] && !retry) return {status:seen[key].status,members:seen[key].appointments.length,duplicate:true};
  if (retry && (!notice || notice!==receipt.notice || JSON.stringify(appointments)!==JSON.stringify(receipt.appointments))) status='human_review';
  const previous=Object.values(seen).filter(v=>notice && v.notice===notice && v.key!==key);
  if (previous.some(v=>v.status==='human_review'||v.status==='human_hold'||Date.parse(v.at)>Date.parse(event.at)))status='human_review';
  const human=['human_hold','human_review'].includes(status);
  const grantable=status==='awaiting_official' && target.status==='matched' && !expired
    && proofs.length===appointments.length && proofs.every((p,i)=>p.status==='1' && p.appointment_id===appointments[i])
    && !previous.some(v=>v.mid!==event.mid);
  const confirmRequest=grantable ? {key,client:target.candidate.client_code,phone:target.candidate.destination_e164,
    ids:appointments,notice,at:event.at} : null;
  if (receipt && receipt.status===status) return {status,members:appointments.length,confirmRequest};
  seen[key]={key,status,mid:event.mid,at:event.at,kind:event.kind,record:event.record,notice:notice||null,appointments,checkedAt:new Date().toISOString()};
  if(status==='human_review')seen[key].sourceEvent=event;
  if(grantable)seen[key].confirmationEvidence={event,snapshots:target.members.map(h.snapshot)};
  if(status==='official_confirmed')seen[key].ackCandidates=target.members.map(c=>({
    kind:'appointment_confirmation',appointment_id:c.appointment_id,client_code:c.client_code,
    destination_e164:c.destination_e164,appointment_date:c.appointment_date,appointment_date_iso:c.appointment_date_iso,
    appointment_time:c.appointment_time,service_code:c.service_code,professional_code:c.professional_code
  }));
  if (notice && target.candidate && appointments.length) {
    state.messages||=[];
    if(!state.messages.some(m=>m.instance===h.instance && m.message_id===event.mid)){
      state.messages.push({instance:h.instance,conversation_id:target.candidate.destination_e164.replace(/^\+/,''),message_id:event.mid,
        direction:'inbound',text:event.text,content_sha256:digest(event.text),appointment_id:target.candidate.appointment_id,
        quoted_message_id:event.quote||null,at:event.at,intent:human?'human_review':intent,
        source:'dell_capture',status:human?'revisao_humana':'recebida'});
    }
    if(['official_confirmed','awaiting_official','human_review','human_hold'].includes(status)){
      state.jp_responses||=[];
      for(const [index,c] of (target.members||[]).entries()){
        let r=state.jp_responses.find(r=>r.message_id===event.mid && r.appointment_id===c.appointment_id && r.source==='dell_capture');
        if(!r){
          r={appointment_id:c.appointment_id,candidate_id:c.candidate_id,destination_e164:c.destination_e164,
            visit_group:target.dispatch.visit_group||null,outbound_message_id:notice,message_id:event.mid,at:event.at,text:event.text,
            source:'dell_capture',operator:'jp_n8n_tick'};
          state.jp_responses.push(r);
        }
        r.intent=human?'human_review':intent;
        r.official_update=status==='official_confirmed'?{status:'confirmed',readback:proofs[index],write_performed:false}:{status:'pending',write_performed:false};
      }
    }
    if(human)for(const r of state.reminders||[]){
      if((appointments.includes(r.appointment_id)||r.visit_group?.appointment_ids?.some(id=>appointments.includes(id))) && r.enabled && !r.attempted_at && !r.message_id && ['scheduled','awaiting_access'].includes(r.status)){
        r.enabled=false;r.status='paused_customer_request';r.blocked_reason='dell_reply_human_review';
      }
    }
  }
  state.version++; h.save(state);
  return {status,members:appointments.length,noticeLinked:!!notice,confirmRequest};
}

// Transport-free projection. The reply workflow is the only allowed consumer.
export function projectOfficialAcknowledgements(state, cutoff) {
  if(!Number.isFinite(Date.parse(cutoff)))throw Error('ack_cutoff_required');
  const links=Object.values(state.dell_reply_links||{}), responses=state.jp_responses||[];
  const out=state.jp_reply_ack_outbox||=[]; let created=0;
  for(const link of links){
    // The cutoff is the activation high-water of inbound events.  A later
    // official re-read must never turn an older client reply into a new send.
    if(link.status!=='official_confirmed'||Date.parse(link.at)<Date.parse(cutoff)||!link.key||!Array.isArray(link.appointments)||!link.appointments.length)continue;
    const related=responses.filter(r=>r.message_id===link.mid&&r.source==='dell_capture'&&link.appointments.includes(r.appointment_id));
    const destinations=[...new Set(related.map(r=>r.destination_e164).filter(Boolean))];
    if(destinations.length!==1||related.length!==link.appointments.length
       ||new Set(related.map(r=>r.appointment_id)).size!==link.appointments.length
       ||!link.appointments.every(id=>related.some(r=>r.appointment_id===id&&r.outbound_message_id===link.notice
          &&r.official_update?.status==='confirmed'&&r.official_update.readback?.confirmed===true)))continue;
    const id=digest(JSON.stringify([link.key,link.appointments]));
    if(out.some(x=>x.id===id))continue;
    out.push({id,link_key:link.key,appointment_ids:[...link.appointments],destination_e164:destinations[0],
      inbound_message_id:link.mid,source_at:link.at,kind:(link.kind==='reaction'||link.kind==='emoji_text')?'emoji':'text',
      text:(link.kind==='reaction'||link.kind==='emoji_text')?'👍':'Confirmado.',state:'queued',created_at:new Date().toISOString()});created++;
  }
  return {created,queued:out.filter(x=>x.state==='queued').length};
}

export function readRescheduleProposal(input,h){
  const key=digest(JSON.stringify([input?.mid,input?.content]));
  const proposal=h.load().dell_reply_links?.[key]?.reschedule;
  if(!proposal||proposal.state!=='grant_required'||proposal.message_id!==input?.mid||proposal.fingerprint!==input?.content)return {status:'idle'};
  return {status:'grant_required',proposal:structuredClone(proposal)};
}

export function serveCapture(h){
  const file='/state/.jp-reply-ingress.sock',token=fs.readFileSync('/ops-config/reply-ingress.key','utf8').trim();
  if(!/^[a-f0-9]{64}$/.test(token)) throw Error('invalid_capture_key');
  if(fs.existsSync(file)){if(!fs.lstatSync(file).isSocket())throw Error('socket_path_conflict');fs.unlinkSync(file);}
  let busy=false;
  const server=http.createServer(async(req,res)=>{
    const answer=(s,o)=>{res.writeHead(s,{'content-type':'application/json'});res.end(JSON.stringify(o));};
    const auth=Buffer.from(String(req.headers.authorization||'')),expected=Buffer.from('Bearer '+token);
    if(req.method!=='POST'||!['/consume','/preview','/check','/pending','/reschedule-proposal'].includes(req.url)||auth.length!==expected.length||!timingSafeEqual(auth,expected))return answer(403,{error:'denied'});
    if(req.url==='/check')return answer(200,{scope:'jp_new_replies'});
    if(busy)return answer(503,{error:'busy'});
    busy=true;
    try{
      let raw='';for await(const b of req){raw+=b;if(raw.length>16384)throw Error('too_large');}
      const input=JSON.parse(raw);
      if(req.url==='/pending'){
        const {pendingCapturedWork}=await import('./captured_confirmation.mjs');
        return answer(200,{actionPending:pendingCapturedWork(h,input.cutoff)});
      }
      if(req.url==='/reschedule-proposal'){
        // Capture may only observe a proposal prepared in admitted reply-execute.
        // It never invokes task reads or changes core state from this socket.
        return answer(200,readRescheduleProposal(input,h));
      }
      answer(200,await consumeVisit(input,h,req.url==='/preview'));
    }catch{answer(503,{error:'reply_held'});}finally{busy=false;}
  });
  server.listen(file,()=>fs.chmodSync(file,0o666));
}

export function officialFetchFor(c){
  return async(url,opts)=>{
    const u=new URL(url),params=Object.fromEntries(new URLSearchParams(opts.body));
    if(u.origin!=='https://sistema.appbarber.com.br'||u.pathname!=='/pages/actions/buscaAgenda3.php'||opts.method!=='POST'||params['profissional[]']!==c.professional_code||params.dia!==c.appointment_date)throw Error('visit_read_scope');
    const body=JSON.stringify({path:u.pathname,params,captureVisit:{id:c.appointment_id,client:c.client_code,service:c.service_code,time:c.appointment_time}});
    const auth=fs.readFileSync('/ops-config/reply-ingress.key','utf8').trim();
    return new Promise((resolve,reject)=>{
      const q=http.request({socketPath:'/source/official.sock',path:'/read',method:'POST',signal:opts.signal,
        headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),'X-JP-Visit-Read':auth}},r=>{
          const parts=[];let size=0;r.on('error',reject);r.on('data',p=>{size+=p.length;if(size>32768)r.destroy(Error('read_limit'));else parts.push(p);});
          r.on('end',()=>resolve(new Response(Buffer.concat(parts),{status:r.statusCode,headers:{'Content-Type':'application/json'}})));
        });q.on('error',reject);q.end(body);
    });
  };
}
