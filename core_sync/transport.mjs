import http from "node:http";
const nativeFetch=globalThis.fetch;
const source="https://sistema.appbarber.com.br";
const permit=new Set(["/pages/cadastros/buscaClientes.php","/pages/relatorios/buscaListaAgendamento.php","/pages/relatorios/buscaAgendamentos.php","/pages/actions/buscaServicos.php","/pages/actions/buscaAgenda3.php"]);
function read(path,params,signal){
  const bytes=JSON.stringify({path,params});
  return new Promise((resolve,reject)=>{
    const q=http.request({socketPath:"/source/official.sock",path:"/read",method:"POST",signal,headers:{"Content-Type":"application/json","Content-Length":Buffer.byteLength(bytes)}},r=>{
      const chunks=[];let size=0;
      r.on("error",reject);r.on("data",b=>{size+=b.length;if(size>20000000)r.destroy(Error("source_too_large"));else chunks.push(b);});
      r.on("end",()=>resolve(new Response(Buffer.concat(chunks),{status:r.statusCode,headers:{"Content-Type":"application/json"}})));
    });q.on("error",reject);q.end(bytes);
  });
}
globalThis.fetch=(url,options={})=>{
  const u=new URL(url);const method=options.method||"GET";
  if(u.origin===source&&!u.search&&!u.hash&&permit.has(u.pathname)&&method==="POST"&&options.redirect==="manual"){
    const pairs=new URLSearchParams(options.body);const params=Object.fromEntries(pairs);
    if(pairs.size!==Object.keys(params).length)throw Error("repeated_parameter");
    return read(u.pathname,params,options.signal);
  }
  if(!u.search&&!u.hash&&u.href==="http://jp-appbarber-renewal:8795/run-once"&&method==="POST")return nativeFetch("http://127.0.0.1:8795/run-once",{...options,redirect:"error"});
  if(!u.search&&!u.hash&&method==="POST"){
    if([process.env.CORE_MANUAL_HOOK,process.env.JP_N8N_MANUAL_WEBHOOK_URL].includes(u.href))return nativeFetch(u,{...options,redirect:"error"});
    if(u.href==="http://127.0.0.1:8792/api/agenda-sync/start"&&options.headers?.["X-JP-N8N-Manual-Internal"]&&options.headers?.["X-JP-N8N-Manual-Request-Id"])return nativeFetch(u,{...options,redirect:"error"});
  }
  throw Error("transport_denied");
};
