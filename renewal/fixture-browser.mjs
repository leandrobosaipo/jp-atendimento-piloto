import assert from "node:assert/strict";
import {chromium} from "playwright-core";
import {loginWithProvider,recordReply} from "./login.mjs";
const origin="https://sistema.appbarber.com.br";
const form='<form id="frmLogin"><input id="login-name"><input type=password id="login-pass"><button>Acessar</button></form><form id="frm2fa" hidden><input id="login-2fa-code"><button>Confirmar</button></form>';
const script=`<script>window.jQuery=()=>{};jQuery._data=()=>({submit:[1]});window.grecaptcha={execute(){},ready(){}};
frmLogin.onsubmit=async e=>{e.preventDefault();await fetch('/php/auth_v2.php',{method:'POST',body:'fixture'});frm2fa.hidden=false;document.getElementById('login-2fa-code').focus()};
frm2fa.onsubmit=async e=>{e.preventDefault();await fetch('/php/auth_2FA.php',{method:'POST',body:'test'})};</script>`;
for(const ready of [false,true]){
 const trace={code:null,sent:false};let posts=0,challenge=false;
 const browserFactory={launch:async()=>{
  const b=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true});
  return {close:()=>b.close(),newContext:async opts=>{
   const c=await b.newContext(opts);await c.addCookies([{name:'test',value:'fixture',url:origin}]);
   await c.route('**/*',async r=>{
    const q=r.request();const u=new URL(q.url());assert.equal(u.origin,origin);assert.equal(u.search,'');
    if(u.pathname==='/login.php')return r.fulfill({contentType:'text/html',body:form+(ready?script:'')});
    assert.equal(q.method(),'POST');posts++;const otp=u.pathname==='/php/auth_2FA.php';assert.equal(u.pathname,posts===1?'/php/auth_v2.php':'/php/auth_2FA.php');
    trace.sent=true;const response=otp?{error:false,auth:[{redirect:'/index.php'}]}:{error:true,error_action_code:8};recordReply(trace,response);await r.fulfill({json:response});
   });return c;
  }};
 }};
 const args={credentials:{userid:'synthetic',password:'fixture'},browserFactory,trace,handoff:{report:async x=>{assert.equal(x.reason_code,'otp_required');challenge=true;},frame:async()=>{},poll:async()=>({action:{action:'press',key:'Enter'}})}};
 if(!ready){await assert.rejects(loginWithProvider(args),/login_script_unavailable/);assert.equal(posts,0);}
 else{const s=await loginWithProvider(args);assert.equal(s.cookieHeader,'test=fixture');assert.equal(posts,2);assert(challenge);}
}
console.log("native browser: missing JavaScript sends nothing; visible OTP follows one accepted credential submission");
