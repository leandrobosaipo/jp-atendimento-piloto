import fs from "node:fs";
import http from "node:http";
import crypto from "node:crypto";
import { chromium } from "playwright-core";
import { action, routeDecision } from "./guard.mjs";
import { loginWithProvider, recordReply } from "./login.mjs";
import { verifyCandidateSession } from "/legacy/jp-appbarber-session-renewal.mjs";

process.umask(0o077);
const origin="https://sistema.appbarber.com.br";
const cfg=JSON.parse(fs.readFileSync("/private/config.json","utf8"));
const known=cfg.known_appointments;
if (!Array.isArray(known) || known.length<2 || !Number.isInteger(cfg.generation)) throw new Error("identity_configuration_required");
const output="/private/verified-candidate.sealed.json";
const candidatePresent=()=>fs.existsSync(output);
let state=candidatePresent()?"verified_candidate":"idle", attempting=false, frame=null, pending=null;
const permittedReason=new Set(["otp_required","captcha_required"]);
const safeFailures=new Set(["credentials_rejected","human_timeout","appbarber_identity_unverified","appbarber_source_incomplete","account_contract_action_required","account_plan_action_required","login_not_proven","human_review_required","browser_route_held","candidate_exists","login_script_unavailable","login_not_submitted","provider_result_unknown","establishment_selection_required","provider_account_verification","provider_rejected"]);
const sentMarker="/private/official-auth-attempt.json";
if(!candidatePresent() && fs.existsSync(sentMarker))state="prior_submission_held";
function writeCandidate(session,identity) {
  if(candidatePresent())throw new Error("candidate_exists");
  const data=Buffer.from(JSON.stringify({origin:session.origin,cookieHeader:session.cookieHeader,source_generation:cfg.generation+1,source_verified_at:identity.verified_at,captured_at:new Date().toISOString()}));
  const key=crypto.randomBytes(32),iv=crypto.randomBytes(12),cipher=crypto.createCipheriv("aes-256-gcm",key,iv);
  const enc=Buffer.concat([cipher.update(data),cipher.final()]);
  const wrapped=crypto.publicEncrypt({key:cfg.public_key,oaepHash:"sha256"},key);
  key.fill(0);data.fill(0);
  const body=JSON.stringify({format:"v1-rsa-oaep-aes256gcm",generation:cfg.generation+1,identity_matches:identity.matched_count,
    key:wrapped.toString("base64"),iv:iv.toString("base64"),tag:cipher.getAuthTag().toString("base64"),data:enc.toString("base64")});
  const tmp=output+"."+crypto.randomUUID();
  const fd=fs.openSync(tmp,"wx",0o600);
  try{fs.writeFileSync(fd,body);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  try{fs.linkSync(tmp,output);}finally{fs.unlinkSync(tmp);}
}
let lastStart=0;
async function authenticate(credentials){
  let browser;
  let phase="login";
  const trace={sent:false,otpSent:false,code:null,hold:null,success:false};
  const receipt=(event,metadata={})=>{
    const fd=fs.openSync("/private/official-auth-events.ndjson","a",0o600);
    try{fs.writeSync(fd,JSON.stringify({at:new Date().toISOString(),event,...metadata})+"\n");fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  };
  try{
    const browserFactory={launch:async()=>{
      browser=await chromium.launch({executablePath:"/usr/bin/chromium",headless:true,args:["--disable-background-networking","--disable-component-update","--disable-sync"]});
      return {close:()=>browser.close(),newContext:async(options)=>{
        const ctx=await browser.newContext({...options,serviceWorkers:"block"});
        await ctx.route("**/*",async r=>{
          const q=r.request(),d=routeDecision(q.url(),q.method(),q.isNavigationRequest());
          if(d==="auth"){
            const otp=new URL(q.url()).pathname==="/php/auth_2FA.php";
            if(otp ? trace.code!==8||trace.otpSent : trace.sent||fs.existsSync(sentMarker))return r.abort();
            if(otp)trace.otpSent=true;
            else{
              const fd=fs.openSync(sentMarker,"wx",0o600);
              try{fs.writeSync(fd,JSON.stringify({at:new Date().toISOString(),state:"dispatching"}));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
              trace.sent=true;
            }
            receipt(otp?"otp_dispatching":"credential_dispatching");
            try{
              const response=await r.fetch({maxRedirects:0,timeout:20000,maxRetries:0});
              if(response.status()===200)recordReply(trace,await response.json());else trace.hold="provider_result_unknown";
              receipt(otp?"otp_response":"credential_response",{http:response.status(),code:Number.isInteger(trace.code)?trace.code:null,success:trace.success,hold:trace.hold});
              return await r.fulfill({response});
            }catch{trace.hold="provider_result_unknown";receipt("unknown");return r.abort().catch(()=>{});}
          }
          if(d==="allow"){
            // Inspect the first response so a permitted request cannot follow
            // an unreviewed redirect to a different origin or application.
            try{const response=await r.fetch({maxRedirects:0,timeout:20000});
              if(response.status()>=300&&response.status()<400)return r.abort();
              return await r.fulfill({response});}catch{return r.abort().catch(()=>{});}
          }
          if(d==="landing")return r.fulfill({status:200,contentType:"text/html",body:"<!doctype html><title>Verificação do acesso</title>"});
          return r.abort();
        });
        if(ctx.routeWebSocket) await ctx.routeWebSocket("**/*",ws=>ws.close());
        return ctx;
      }};
    }};
    const handoff={report:async x=>{
      if(!permittedReason.has(x.reason_code))throw new Error("human_review_required");
      state=x.reason_code;
    },frame:async x=>{if(typeof x.png_base64!=="string"||x.png_base64.length>1600000)throw new Error("human_review_required");frame=x.png_base64;},
    poll:async()=>{const a=pending;pending=null;return {action:a};}};
    const session=await loginWithProvider({credentials,browserFactory,handoff,trace});
    frame=null;pending=null;state="checking_identity";
    phase="read_official_appointments";
    const proof=await verifyCandidateSession({session,knownAppointments:known});
    if(!proof?.identity||proof.identity.matched_count<2)throw new Error("appbarber_identity_unverified");
    phase="save_verified_session";
    writeCandidate(proof.session,proof.identity);state="verified_candidate";
  }catch(e){
    state=safeFailures.has(e?.message)?e.message:"verification_failed";
    const readErrors=["session_network_error","session_auth_redirect","session_auth_rejected",
      "session_auth_html","session_response_format_invalid","session_cookie_domain_invalid"];
    const storageErrors=["EACCES","ENOSPC","EROFS","EEXIST"];
    let detail=state;
    if(phase==="read_official_appointments"&&readErrors.includes(e?.message))detail=e.message;
    if(phase==="save_verified_session"&&storageErrors.includes(e?.code))detail=e.code;
    receipt("held",{state,phase,detail,credential_dispatching:trace.sent});
  }
  finally{frame=null;pending=null;attempting=false;if(browser)await browser.close().catch(()=>{});}
}
const sock="/ipc/operator.sock";
if(fs.existsSync(sock)){
  const old=fs.lstatSync(sock);
  if(!old.isSocket()||old.uid!==process.getuid())throw new Error("unexpected_ipc_file");
  fs.unlinkSync(sock);
}
const server=http.createServer(async(req,res)=>{
 const reply=(code,obj)=>{res.writeHead(code,{"Content-Type":"application/json","Cache-Control":"no-store"});res.end(JSON.stringify(obj));};
 try{
  if(req.method!=="POST"||req.url!=="/operate")return reply(404,{status:"rejected"});
  const b=[];let n=0;for await(const c of req){n+=c.length;if(n>4096)return reply(413,{status:"rejected"});b.push(c);}
  const x=JSON.parse(Buffer.concat(b));
  if(x.op==="status")return reply(200,{state,attempting,submission_locked:fs.existsSync(sentMarker),frame:permittedReason.has(state)?frame:null});
  if(x.op==="action"&&attempting&&permittedReason.has(state)&&!pending){pending=action(x);return reply(200,{state:"accepted"});}
  if(x.op!=="start")return reply(400,{state:"invalid_request"});
  if(attempting||candidatePresent()||fs.existsSync(sentMarker)||Date.now()-lastStart<60000)return reply(409,{state:candidatePresent()?"verified_candidate":"busy_or_recent"});
  if(typeof x.userid!=="string"||x.userid.length<1||x.userid.length>254||typeof x.password!=="string"||x.password.length<1||x.password.length>512)return reply(400,{state:"credentials_required"});
  lastStart=Date.now();attempting=true;state="authenticating";
  void authenticate({userid:x.userid,password:x.password});return reply(202,{state});
 }catch{return reply(400,{state:"invalid_request"});}
});
server.listen(sock,()=>{fs.chmodSync(sock,0o660);});
