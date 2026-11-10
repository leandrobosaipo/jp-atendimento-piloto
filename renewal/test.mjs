import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {routeDecision,action} from "./guard.mjs";
const o="https://sistema.appbarber.com.br";
assert.equal(routeDecision(o+"/login.php","GET",true),"allow");
assert.equal(routeDecision(o+"/php/auth.php","POST"),"allow");
assert.equal(routeDecision(o+"/index.php","GET",true),"landing");
for(const [path,method] of [["/pages/actions/gravaAgendamento.php","POST"],["/php/auth.php","PUT"],["/login.php","POST"],["/billing/contract","GET"],["/assets/a.js","POST"]])assert.equal(routeDecision(o+path,method,true),"deny");
assert.equal(routeDecision("https://sistema.appbarber.com.br.malicious.invalid/login.php","GET"),"deny");
assert.equal(routeDecision("https://www.google.com/recaptcha/api2/reload","POST"),"allow");
assert.throws(()=>action({action:"click",x:1024,y:20}));
assert.throws(()=>action({action:"press",key:"F12"}));
assert.deepEqual(action({action:"press",key:"Enter"}),{action:"press",key:"Enter"});
console.log("route and interaction restrictions passed");
// The Python client reads through EOF. Cockpit keeps stdin open when the
// optional stream argument is true, so exercise the actual refresh script.
const elements=new Map();
function element(id){
  if(!elements.has(id)) elements.set(id,{removeAttribute(){},textContent:""});
  return elements.get(id);
}
let request,inputClosed=false;
const scope={document:{getElementById:element,addEventListener(){},hidden:true},clearTimeout(){},
  cockpit:{spawn(){
    const p=Promise.resolve('{"state":"idle","attempting":false,"frame":null}');
    p.input=(data,stream)=>{request=JSON.parse(data);inputClosed=!stream;};
    return p;
  }}};
runInNewContext(readFileSync(new URL("./cockpit/index.js",import.meta.url),"utf8"),scope);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(request.op,"status");assert.equal(inputClosed,true);
assert.equal(element("status").textContent,"Aguardando suas credenciais.");
