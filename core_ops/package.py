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
    return b'import {taskServer as scopedServer} from "/ops-app/task_reads.mjs";\n'+native.replace(original,b'scopedServer(async (req, res) => {')

def wrap_reply(native,expected):
    if hashlib.sha256(native).hexdigest()!=expected:raise ValueError('frozen_reply_drift')
    replacements={
        'fetchImpl=deps.fetchImpl||fetch;':'fetchImpl=scopedFetch(deps.fetchImpl||fetch);',
        'async function readContext(t){':'function readContext(t){return withTask(t,deps.load,()=>readCustomer(t));}\n async function readCustomer(t){',
        'await readReplyClients(deps.agendaConfig,{fetchImpl})':'await readTaskClients(native)',
        "async function slots(t,{dates,period='any',context,selection}={}){":"function slots(t,opts){return withTask(t,deps.load,()=>readSlots(t,opts));}\n async function readSlots(t,{dates,period='any',context,selection}={}){",
    }
    for old,new in replacements.items():
        if native.count(old.encode())!=1:raise ValueError('reply_adapter_drift')
        native=native.replace(old.encode(),new.encode())
    return b'import {withTask,scopedFetch,readTaskClients} from "/ops-app/task_reads.mjs";\n'+native

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('dest',type=Path);p.add_argument('--apply',action='store_true');a=p.parse_args()
    gs,manifest=render(json.loads(a.inputs.read_bytes()))
    if a.apply:
        os.umask(0o077);a.dest.mkdir(mode=0o700)
        for name,obj in {'manifest':manifest,**gs}.items():
            with (a.dest/(name+'.json')).open('x') as f:json.dump(obj,f,ensure_ascii=False)
    print(json.dumps({'roles':len(gs),'authenticated_http_nodes':sum(len(x['nodes']) for x in manifest.values()),'written':a.apply}))
