"""Prepare the existing private browser for a verified native recovery job.

Plan by default. No credential submission or background timer. The signed-in
operator can continue in the existing private panel after --apply succeeds.
"""
import argparse
import datetime as dt
import fcntl
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
from uuid import UUID
from renewal_lease import reserve

CORE=Path('/var/lib/jp-migracao-revisao/core-transacional')
WORKER=Path('/var/lib/agenda-renovacao-private')
BIN=Path('/usr/local/libexec/atendimento-piloto-integracao/provider.py')
SOURCE=Path('/etc/atendimento-piloto-integracao/appbarber-readonly.json')
SOCK='/run/agenda-renovacao/operator.sock'

def require(ok, reason):
    if not ok:raise ValueError(reason)

def load(path):
    return json.loads(path.read_bytes())

def dump(path, data):
    raw=data if isinstance(data,bytes) else json.dumps(data).encode()
    with path.open('xb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())

def replace_config(path, value):
    """The worker intentionally reads this as its private group, not as root."""
    before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o440 and before.st_uid==0,'unexpected_private_config_acl')
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchown(fd,before.st_uid,before.st_gid);os.fchmod(fd,stat.S_IMODE(before.st_mode))
            json.dump(value,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

class LocalOperator(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX)
        self.sock.settimeout(3);self.sock.connect(SOCK)

def operator():
    c=LocalOperator('localhost')
    try:
        c.request('POST','/operate',b'{"op":"status"}',{'Content-Type':'application/json'})
        r=c.getresponse();require(r.status==200,'operator_unavailable');s=json.loads(r.read(1800000))
        return s['state'],s['attempting'] or bool(s.get('frame'))
    finally:c.close()

def shell(*args, data=None):
    return subprocess.check_output(args,input=data,stderr=subprocess.DEVNULL,timeout=30)

def docker_js(cid, code):
    raw=shell('docker','exec','-i',cid,'node','--input-type=module','-',data=code.encode())
    return json.loads(raw)

def native_job(cid):
    return docker_js(cid,"""import fs from 'node:fs';import {decodeStateEnvelope} from '/legacy/scripts/jp-state-envelope.mjs';
        const cfg=JSON.parse(fs.readFileSync('/config/runtime.json'));
        const s=JSON.parse(decodeStateEnvelope(JSON.parse(fs.readFileSync('/state/state.json')),Buffer.from(cfg.stateKey,'hex')));
        const j=s.access_renewals?.at(-1);console.log(JSON.stringify({job_id:j?.job_id,status:j?.status}));""")

def refused_reads(reader):
    require(reader.get('origin')=='https://sistema.appbarber.com.br','unexpected_reader_origin')
    spec=importlib.util.spec_from_file_location('installed_read_only',BIN)
    p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
    prof=next(v['id'] for v in reader['professionals'].values() if not v.get('human_only'))
    result=[]
    for role,params,key in [('prices',{},'data'),('catalog',{'profissional':prof,'tipo':'9'},'servicos')]:
        rows=p._read(role,params,reader).get(key)
        result.append(isinstance(rows,list) and len(rows)==1 and str(rows[0].get('erro'))=='1')
    return result

def run(args):
    os.umask(0o077)
    require(os.geteuid()==0 and str(UUID(args.job))==args.job,'root_and_native_job_required')
    lease=(CORE/'install.lock').open('rb');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cid=load(CORE/'install.json')['container'];native=native_job(cid)
    require(native=={'job_id':args.job,'status':'requested'},'not_native_requested_job')
    intent=load(CORE/'state/renewal-intents'/(args.job+'.json'));execution=intent.get('execution','')
    require(re.fullmatch(r'[0-9]{1,20}',execution),'native_execution_required')
    a=CORE/'state/admissions'/(execution+'-renewal-run.json')
    require(load(a)['workflow']==load(CORE/'config/runtime.json')['renewalId'] and
            load(a.with_name(a.name+'.done'))['result'].get('worker_result')=='awaiting_human','renewal_execution_incomplete')
    receipt=load(WORKER/'provider-v2-receipt.json');wid=receipt['cid']
    wi=json.loads(shell('docker','inspect',wid))[0]
    require(wi['State']['Running'] and not wi['HostConfig']['PortBindings'] and
            wi['Config']['Labels'].get('pilot.renewal.source')==receipt['source'],'worker_identity_changed')
    w=WORKER/'worker';reader=load(SOURCE);gen=reader['source_generation'];st,human=operator()
    require(st=='verified_candidate' and not human,'worker_or_operator_in_use')
    f=w/'config.json';cfg=load(f)
    require(load(w/'verified-candidate.sealed.json')['generation']==gen and cfg['generation']+1==gen,'prior_generation_unreconciled')
    require(not f.is_symlink() and f.stat().st_gid==int(wi['Config']['User'].split(':')[1])
            and stat.S_IMODE(f.stat().st_mode)==0o440,'worker_acl_drift')
    events=[json.loads(row) for row in (w/'official-auth-events.ndjson').read_text().splitlines()]
    sent=[j for j in events if j['event']=='credential_dispatching']
    require(len(sent)==1 and any(j['event'] in ('credential_response','otp_response') and j.get('success') is True for j in events),'prior_auth_uncertain')
    now=dt.datetime.now(dt.timezone.utc);last=dt.datetime.fromisoformat(sent[0]['at'].replace('Z','+00:00'))
    refusals=refused_reads(reader)
    require(refusals==[True,True] and now-last>=dt.timedelta(minutes=30),'reader_or_cooldown_held')
    summary={'generation':gen,'execution':execution,'job':args.job,'official_refusals':2}
    if not args.apply:return {**summary,'result':'plan'}
    box=CORE/'renewal-leases';box.mkdir(mode=0o700,exist_ok=True)
    reserve(box,intent,native,gen,last,now,refusals,human)
    bundle=box/args.job;bundle.mkdir(mode=0o700)
    names=('verified-candidate.sealed.json','official-auth-attempt.json','official-auth-events.ndjson')
    for n in (*names,'config.json'):dump(bundle/n,(w/n).read_bytes())
    claim=docker_js(cid,"""import fs from 'node:fs';const c=JSON.parse(fs.readFileSync('/config/runtime.json'));
        const r=await fetch('http://127.0.0.1:8792/api/access/renew-work',{headers:{authorization:'Bearer '+c.worker}});
        const j=(await r.json()).work;if(!r.ok||!j?.job_id)throw Error();
        console.log(JSON.stringify({job:j.job_id,ids:j.known_appointments.map(v=>String(v.appointment_id))}));""")
    require(claim['job']==args.job,'claim_changed')
    anchors=[x for x in cfg['known_appointments'] if str(x['appointment_id']) in set(claim['ids'])]
    require(len(anchors)>=2 and operator()==('verified_candidate',False),'review_identity_or_operator')
    shell('docker','stop','-t','8',wid)
    for n in names:
        require((bundle/n).read_bytes()==(w/n).read_bytes(),'archive_changed')
        dst=w/f'archived-g{gen}-{n}'
        require(not dst.exists(),'archive_conflict');(w/n).rename(dst)
    replace_config(f,{**cfg,'known_appointments':anchors,'generation':gen})
    shell('docker','start',wid)
    for _ in range(25):
        try:
            if operator()==('idle',False):break
        except (OSError,ValueError):pass
        time.sleep(.3)
    else:raise ValueError('worker_rearm_needs_reconciliation')
    result={**summary,'status':'ready_for_private_operator','anchor_count':len(anchors)}
    dump(bundle/'ready.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True);parser.add_argument('--apply',action='store_true')
    try:print(json.dumps(run(parser.parse_args())))
    except Exception as exc:
        brief=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z_]+',str(exc)) else 'operation_held_check_native_receipts'
        raise SystemExit(brief) from None
