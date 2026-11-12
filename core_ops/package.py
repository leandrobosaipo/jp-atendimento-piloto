"""Prepare operator-reviewed, unpublished n8n graphs and a private role manifest."""
import argparse,hashlib,json,os,re
from pathlib import Path
from graphs import PATHS,compile_graph

def render(inputs):
    if set(inputs)!=set(PATHS):raise ValueError('ten_roles_required')
    role_creds=[x['credential']['id'] for x in inputs.values()]
    hooks=[x['hook']['id'] for x in inputs.values() if x.get('hook')]
    ids=[x['workflow'] for x in inputs.values()]
    if (len(set(ids))!=len(ids) or not all(re.fullmatch(r'[\w-]{1,128}',w) for w in ids)
        or len(set(role_creds+hooks))!=len(role_creds+hooks)):raise ValueError('distinct_identities_required')
    graphs={};manifest={}
    for role,x in inputs.items():
        graphs[role],manifest[role]=compile_graph(x['source'],role,x['workflow'],x['credential'],x.get('hook'),inputs['error']['workflow'])
    return graphs,manifest

def wrap_native(native,expected):
    if hashlib.sha256(native).hexdigest()!=expected:raise ValueError('frozen_native_drift')
    original=b'http.createServer(async (req, res) => {'
    if native.count(original)!=1:raise ValueError('native_listener_drift')
    result=b'import {taskServer as scopedServer} from "/ops-app/task_reads.mjs";\n'+native.replace(original,b'scopedServer(async (req, res) => {')
    if b'async function contactOperation(operation,row)' in result:
        edits={
            b'async function contactOperation(operation,row){':b'function contactOperation(operation,row){return contactScope(operation,row,load,()=>nativeContactOperation(operation,row));}\nasync function nativeContactOperation(operation,row){',
            b'const target={baseUrl:cfg.adapter_url,scopedCredential:cfg.adapter_token,instance,phone:row.destination_e164}':b'const target={baseUrl:"http://task-contact.invalid",scopedCredential:"isolated-unix-peer-credential",instance,phone:row.destination_e164,fetchImpl:effectFetch(fetch)}',
            b'source:row=>readOfficialContactSource(agendaConfig(),row.client_code)':b'source:row=>contactSource(row,load)',
            b'const contactRuntime=createContactRuntime({':b'const contactRuntime=registrationPreflight(createContactRuntime({',
            b"writeContact:row=>contactOperation('write',row)});":b"writeContact:row=>contactOperation('write',row)}),load,contactConfig);",
            b'writeEnabled:n8nStageAgendaWriteEnabled});':b'writeEnabled:process.env.JP_REPLY_SCOPED_WRITE_ENABLED==="1"});',
        }
        for old,new in edits.items():
            if result.count(old)!=1:raise ValueError('native_contact_contract_changed')
            result=result.replace(old,new)
        health=b"    if (req.method === 'GET' && req.url === '/healthz')"
        if result.count(health)!=1:raise ValueError('native_health_anchor_changed')
        hook=b"    if(req.url==='/api/contact-registration/manual')return await handleContactManual(req,res,{load,save,body,reply,config:contactConfig});\n"
        result=b'import {handleContactManual} from "/ops-app/manual_contact.mjs";\n'+result.replace(health,hook+health)
        result=b'import {contactScope,contactSource,effectFetch,registrationPreflight} from "/ops-app/effect_client.mjs";\n'+result
    return result

def wrap_reply(native,expected):
    if hashlib.sha256(native).hexdigest()!=expected:raise ValueError('frozen_reply_drift')
    replacements={
        'fetchImpl=deps.fetchImpl||fetch;':'fetchImpl=effectFetch(scopedFetch(deps.fetchImpl||fetch));',
        'return createReplyAutomation({...deps,readContext,slots,send,':'const engine=createReplyAutomation({...deps,readContext,slots,send,',
        ' });\n}':' });\n return wrapEngine(wrapReplyPlanning(wrapSameDayReschedule(engine,deps),deps,withTask),deps.load);\n}',
        'async function readContext(t){':'function readContext(t){return withTask(t,deps.load,()=>readCustomer(t));}\n async function readCustomer(t){',
        "rows(await native('/pages/cadastros/buscaClientes.php',{}),'data')":'await readTaskClients(native)',
        "async function slots(t,{dates,period='any',context,selection}={}){":"function slots(t,opts){return withTask(t,deps.load,()=>readSlots(t,opts));}\n async function readSlots(t,{dates,period='any',context,selection}={}){",
    }
    for old,new in replacements.items():
        if native.count(old.encode())!=1:raise ValueError('reply_adapter_drift')
        native=native.replace(old.encode(),new.encode())
    return b'import {effectFetch,wrapEngine} from "/ops-app/effect_client.mjs";\nimport {withTask,scopedFetch,readTaskClients} from "/ops-app/task_reads.mjs";\nimport {wrapReplyPlanning,wrapSameDayReschedule} from "/ops-app/same_day_reschedule_integration.mjs";\n'+native

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('dest',type=Path);p.add_argument('--apply',action='store_true');a=p.parse_args()
    gs,manifest=render(json.loads(a.inputs.read_bytes()))
    if a.apply:
        os.umask(0o077);a.dest.mkdir(mode=0o700)
        for name,obj in {'manifest':manifest,**gs}.items():
            with (a.dest/(name+'.json')).open('x') as f:json.dump(obj,f,ensure_ascii=False)
    print(json.dumps({'roles':len(gs),'authenticated_http_nodes':sum(len(x['nodes']) for x in manifest.values()),'written':a.apply}))
