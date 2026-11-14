import fs from "node:fs";
import net from "node:net";
import {decodeStateEnvelope} from "/legacy/scripts/jp-state-envelope.mjs";
import {deliver,setStateReader} from "./dispatch.mjs";
import "/app/transport.mjs";
const prior=globalThis.fetch;
setStateReader(()=>{
  const envelope=JSON.parse(fs.readFileSync(process.env.JP_LOCAL_API_STATE));
  return JSON.parse(decodeStateEnvelope(envelope,Buffer.from(process.env.JP_STATE_ENCRYPTION_KEY,"hex")));
});
globalThis.fetch=(url,options={})=>{
  const u=new URL(url);
  if(u.origin==="http://owner.invalid"&&(options.method||"GET")==="GET"&&!u.search&&!u.hash){
    const action=u.pathname==="/instance/connectionState/"+process.env.JP_EVOLUTION_INSTANCE?"state":u.pathname==="/instance/fetchInstances"?"identity":null;
    if(!action)throw Error("observer_route_denied");
    return new Promise((resolve,reject)=>{
      const s=net.createConnection({path:"/model/observer.sock",signal:options.signal});let raw="";
      s.setTimeout(8500,()=>s.destroy(Error("observer_timeout")));
      s.on("connect",()=>s.write(JSON.stringify({action})+"\n"));s.on("error",reject);
      s.on("data",b=>{raw+=b;if(raw.length>4096)s.destroy(Error("observer_limit"));});
      s.on("end",()=>{try{const o=JSON.parse(raw);resolve(new Response(JSON.stringify(o.body),{status:o.status}));}catch{reject(Error("observer_response"));}});
    });
  }
  if(options.method==="POST"&&u.origin==="http://clock:5678"&&!u.search&&!u.hash){
    const secrets=JSON.parse(fs.readFileSync("/ops-config/roles.json"));
    const hook=Object.entries(secrets.webhooks).find(([,h])=>h.url===u.href);
    if(!hook||options.redirect!=="error")throw Error("unknown_job_hook");
    return fetchHook(u,options,hook[1].token);
  }
  if(u.origin==="http://owner.invalid"&&/^\/message\/send(Text|Media)\//.test(u.pathname))
    return deliver(u,options,process.env.CORE_DISPATCH_SOCKET,process.env.JP_EVOLUTION_INSTANCE);
  return prior(url,options);
};
// Only the three n8n job callbacks use this low-level HTTP path. No redirects.
import http from "node:http";
function fetchHook(url,options,token){
  const body=Buffer.from(options.body||"");
  if(body.length>16384)throw Error("job_body_limit");
  return new Promise((resolve,reject)=>{
    const q=http.request(url,{method:"POST",signal:options.signal,headers:{"Content-Type":"application/json",Authorization:"Bearer "+token,"Content-Length":body.length}},r=>{
      const chunks=[];let size=0;r.on("error",reject);r.on("data",b=>{if((size+=b.length)>65536)r.destroy(Error("job_response_limit"));else chunks.push(b);});
      r.on("end",()=>resolve(new Response(Buffer.concat(chunks),{status:r.statusCode})));
    });q.on("error",reject);q.end(body);
  });
}
