'use strict';
const fs=require('node:fs'), path=require('node:path'), os=require('node:os'), vm=require('node:vm');
const assert=require('node:assert/strict');
const operation=require('./scoped-contact.cjs');
const {createHash,randomBytes,timingSafeEqual}=require('node:crypto');
const hash=b=>createHash('sha256').update(b).digest('hex');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'contact-case-'));
const cfg={permits:path.join(dir,'permits'),ledger:path.join(dir,'ledger'),permitOwner:process.getuid()};
fs.mkdirSync(cfg.permits,{mode:0o700});fs.mkdirSync(cfg.ledger,{mode:0o700});
const exact=process.argv[2] && fs.readFileSync(process.argv[2],'utf8');
const section=(a,b)=>{const i=exact.indexOf(a);assert(i>=0);const j=exact.indexOf(b,i+a.length);assert(j>i);return exact.slice(i,j);};
const base='fixture-tenant', credential=randomBytes(32).toString('hex');
let physical=0, cacheWrites=0, accessRejected=0;
function fixture(id,scenario={}) {
  const phone='1555000'+String(id).padStart(4,'0'), proof={phone,source:'whatsapp_contact_screen_human',name:'Device Old',proofId:'fake-'+id,observedAt:new Date().toISOString()};
  const body={instance:base,phone,name:'Novo Nome',expectedPreviousName:'Device Old',expectedCacheName:'Cache Antigo',sourceProof:proof};
  const task=hash('test-task-'+id),secret=randomBytes(32).toString('hex');
  const grant={schema:1,role:'registration',operation:'contact',task,operator:'fixture',cacheExists:true,secretSha:hash(secret),bodySha:hash(operation.canonical(body)),issued:Math.floor(Date.now()/1000),until:Math.floor(Date.now()/1000)+120};
  const permit=path.join(cfg.permits,task+'.json');
  function issue(overrides) {fs.writeFileSync(permit,JSON.stringify({...grant,...overrides}),{mode:0o600});}
  issue();
  const headers={'x-contact-task':task,'x-contact-permit':secret,'authorization':'Bearer '+credential};
  let cache=scenario.stale?'Terceiro':'Cache Antigo',attempts=0,sideEffects=0;
  const service={instanceId:'fixture-id',instance:{name:base,authState:{state:{keys:{
    get:async(type,[key])=>{assert.equal(type,'lid-mapping');return {[key]:key===phone?'778899':scenario.reverse?'9999':phone};}
  }}}},client:{addOrEditContact:async(jid,contact)=>{
    attempts++;physical++;assert.equal(jid,phone+'@s.whatsapp.net');assert.equal(contact.lidJid,'778899@lid');
    if(scenario.lost)throw Error('fake loss after native request');
    if(scenario.race)cache='Changed concurrently';
  }},prismaRepository:{contact:{
    findUnique:async q=>{assert.equal(q.where.remoteJid_instanceId.remoteJid,phone+'@s.whatsapp.net');return {pushName:cache};},
    updateMany:async q=>{if(q.where.pushName!==cache) return {count:0};cache=q.data.pushName;cacheWrites++;return {count:1};},
    create:async()=>{throw Error('unexpected create');}
  }},sendDataWebhook:()=>{sideEffects++;throw Error('unwanted webhook');}};
  const ext={route:operation.route,write:(svc,request,jid)=>operation.write(svc,request,jid,cfg)};
  const controller={writeJpContactName:(instance,request)=>{assert.equal(instance,base);return ext.write(service,request,p=>p+'@s.whatsapp.net');}};
  let handler, read;
  if(exact) {
    // Execute just the patched write handler and the installed controller/service methods. Never start Evolution or Baileys.
    const ctx=vm.createContext({require:p=>{assert.equal(p,'./scoped-contact.cjs');return ext;},process:{env:{
      JP_CONTACT_ADAPTER_INSTANCE:base,JP_CONTACT_ADAPTER_TOKEN:credential,JP_CONTACT_ADAPTER_WRITE_ENABLED:'0'}},
      Buffer,Cl:{timingSafeEqual},wl:base,Gu:300000,G:p=>p+'@s.whatsapp.net'});
    const evalCode=code=>vm.runInContext(code,ctx);
    evalCode(section('function Hu(','function ju('));
    const serviceMethod=section('async writeJpContactName(e){','async updateProfileStatus(');
    service.writeJpContactName=evalCode('({'+serviceMethod+'})').writeJpContactName;
    service.readJpContactName=evalCode('({'+section('async readJpContactName(e){','async writeJpContactName(e){')+'})').readJpContactName;
    const forward=evalCode('({'+section('async writeJpContactName(s,e){','async getBase64FromMediaMessage(')+'})');
    forward.readJpContactName=evalCode('({'+section('async readJpContactName(s,e){','async writeJpContactName(s,e){')+'})').readJpContactName;
    forward.waMonitor={waInstances:{[base]:service}};ctx.V=forward;
    handler=evalCode('('+section('async(s,e)=>{if(!Tl(s,e))return;return require("./scoped-contact.cjs")',')}};var xn')+')');
    read=evalCode('('+section('async(s,e)=>{if(!Tl(s,e))return;let t=bl(s.body?.phone)',').post("/write"')+')');
  } else {
    handler=(req,res)=>req.get('authorization')==='Bearer '+credential && req.body.instance===base ?
      operation.route(req,res,controller,()=>true) : res.status(401).json({error:'base_auth'});
  }
  async function call(change={},extra={},route=handler) {
    let result;const res={status(code){this.code=code;return this;},json(data){result={code:this.code,data};return result;}};
    const hdr={...headers,...extra};await route({body:{...body,...change},get:k=>hdr[k]},res);
    if(result.code===401) accessRejected++;
    return result;
  }
  function replayWithFreshProcess(){
    const code=`const fs=require('node:fs'), {write}=require(process.argv[1]);
      const p=JSON.parse(fs.readFileSync(0));const s={instance:{name:p.body.instance,authState:{state:{keys:{get:async(t,[k])=>({[k]:k===p.body.phone?'778899':p.body.phone})}}}},prismaRepository:{contact:{}},client:{addOrEditContact:()=>process.exit(6)}};
      write(s,p,q=>q+'@s.whatsapp.net',p.cfg).then(r=>process.exit(r.status==='write_outcome_unknown'?0:7)).catch(()=>process.exit(8));`;
    require('node:child_process').execFileSync(process.execPath,['-e',code,require.resolve('./scoped-contact.cjs')],{input:JSON.stringify({body,auth:{task,secret},cfg}),timeout:4000});
  }
  return {call,read:()=>call({},{},read),replayWithFreshProcess,issue,permit,task,grant,get attempts(){return attempts;},get webhooks(){return sideEffects;}};
}
async function main(){
  const first=fixture(1);
  assert.equal((await first.call({}, {authorization:'Bearer wrong'})).code,401);
  assert.equal((await first.call({}, {'x-contact-permit':'0'.repeat(64)})).code,423);
  assert.equal((await first.call({name:'Changed'})).code,423);
  first.issue({until:first.grant.issued});assert.equal((await first.call()).code,423);
  first.issue({role:'reply'});assert.equal((await first.call()).code,423);
  first.issue();assert.equal(first.attempts,0);
  const concurrent=await Promise.all([first.call(),first.call()]);
  assert.deepEqual(concurrent.map(r=>r.code).sort(),[200,504]);assert.equal(first.attempts,1);
  const receipt=concurrent.find(r=>r.code===200).data;assert.equal(receipt.readback.authoritative,false);assert.equal(receipt.readback.matched,true);
  if(exact){const read=await first.read();assert.equal(read.code,200);assert.equal(read.data.name,'Novo Nome');assert.equal(read.data.authoritative,false);}
  assert.equal((await first.call()).code,504);assert.equal(first.webhooks,0);
  const lost=fixture(2,{lost:true});assert.equal((await lost.call()).code,504);assert.equal((await lost.call()).code,504);assert.equal(lost.attempts,1);
  lost.replayWithFreshProcess();
  const changed=fixture(3,{race:true});assert.equal((await changed.call()).code,504);assert.equal((await changed.call()).code,504);assert.equal(changed.attempts,1);
  const stale=fixture(4,{stale:true});assert.equal((await stale.call()).code,423);assert.equal(stale.attempts,0);
  const mismatch=fixture(5,{reverse:true});assert.equal((await mismatch.call()).code,423);assert.equal(mismatch.attempts,0);
  const absent=fixture(6);fs.unlinkSync(absent.permit);assert.equal((await absent.call()).code,423);assert.equal(absent.attempts,0);
  assert.equal(fs.readdirSync(cfg.ledger).filter(x=>x.endsWith('.observed')).length,1);
  assert.equal(physical,3);assert.equal(cacheWrites,1);
  console.log(JSON.stringify({exact_bundle:!!exact,native_transport:'fixture',fake_native_calls:physical,
    accepted_unverified:1,unknown_native_cases:2,repeat_writes:0,cache_cas_matches:1,external_requests:0,
    no_webhook:true,wrong_bearer_denied:accessRejected===1,expired_denied:true,wrong_role_denied:true,
    stale_cache_denied:true,wrong_lid_denied:true,held_on_fresh_process:true,native_read_route_checked:!!exact,live_device_readback:false}));
}
main().finally(()=>fs.rmSync(dir,{recursive:true,force:true})).catch(e=>{console.error(e);process.exitCode=1;});
