import fs from 'node:fs';
import http from 'node:http';
import {admitted} from './task_reads.mjs';
import {officialFetchFor} from './captured_visit.mjs';

// The native preflight retains its schedule, identity, consent and budget checks.
export function noticeFetch(c){
  admitted('clock',['send-initial','send-reminders']);
  return async(url,opts={})=>{
    const u=new URL(url);
    if(u.origin!=='https://sistema.appbarber.com.br')return fetch(url,opts);
    if(u.pathname==='/pages/actions/buscaAgenda3.php')return officialFetchFor(c)(url,opts);
    if(u.pathname==='/pages/cadastros/buscaClientes.php')return fetch(url,opts);
    if(u.search||u.hash||opts.method!=='POST')throw Error('notice_read_denied');
    const params=Object.fromEntries(new URLSearchParams(opts.body));
    const body=JSON.stringify({path:u.pathname,params,clockVisit:{id:c.appointment_id,client:c.client_code,pro:c.professional_code,service:c.service_code,day:c.appointment_date,time:c.appointment_time}});
    return new Promise((resolve,reject)=>{
      const q=http.request({socketPath:'/source/official.sock',method:'POST',path:'/read',signal:opts.signal,headers:{
        'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),'X-JP-Visit-Read':fs.readFileSync('/ops-config/reply-ingress.key','utf8').trim()
      }},r=>{
        const parts=[];let size=0;
        r.on('error',reject);r.on('data',p=>{size+=p.length;if(size>4000000)r.destroy(Error('notice_read_limit'));else parts.push(p);});
        r.on('end',()=>resolve(new Response(Buffer.concat(parts),{status:r.statusCode,headers:{'Content-Type':'application/json'}})));
      });q.on('error',reject);q.end(body);
    });
  };
}

// A delivered clarification can supersede only its own original hold. The
// original response, other holds and all historical delivery states remain.
export function clarificationFor(state,record,id){
  if(record?.source!=='dell_capture'||record.intent!=='confirmed')return null;
  const path='/ops-config/notice-clarifications.json';
  if(!fs.existsSync(path))return null;
  const hits=JSON.parse(fs.readFileSync(path)).filter(p=>p.answer===record.message_id&&p.question===record.outbound_message_id&&p.ids.includes(id));
  if(hits.length!==1)return null;
  const p=hits[0],links=Object.values(state.dell_reply_links||{});
  const match=(l,mid,notice,status)=>l.mid===mid&&l.notice===notice&&l.status===status&&JSON.stringify(l.appointments)===JSON.stringify(p.ids);
  if(links.filter(l=>match(l,p.original,p.notice,'human_hold')).length!==1||links.filter(l=>match(l,p.answer,p.question,'official_confirmed')).length!==1)return null;
  const previous=(state.individual_dispatches||[]).filter(d=>d.message_id===p.notice);
  if(previous.length!==p.ids.length||!p.ids.every(member=>previous.some(d=>d.appointment_id===member&&d.destination_e164===record.destination_e164)))return null;
  return p;
}
