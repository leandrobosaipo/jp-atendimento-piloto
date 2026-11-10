"""Stage, arm, or roll back a single owned draft while the Central is in maintenance."""
import argparse,fcntl,json,os,re
from pathlib import Path
from uuid import uuid4
def subset(actual,wanted):
    if isinstance(wanted,dict):return isinstance(actual,dict) and all(k in actual and subset(actual[k],v) for k,v in wanted.items())
    if isinstance(wanted,list):return isinstance(actual,list) and len(actual)==len(wanted) and all(subset(a,b) for a,b in zip(actual,wanted))
    return actual==wanted
def spec(row):return {k:row[k] for k in ('name','nodes','connections','settings')}
def run(api,root,role,action,expected_version,apply):
    read=lambda f:json.loads(f.read_bytes())
    manifest=read(root/'ops-config/manifest.json');row=manifest[role];wid=row['workflow']
    doc=root/'documents';original=read(doc/('baseline-'+role+'.json'));desired=read(doc/(role+'.json'));before=spec(original)
    if original['id']!=wid or original['active'] or original.get('activeVersionId'):raise ValueError('changed_baseline')
    get=lambda:api.req('GET','/rest/workflows/'+wid)['data']
    with (root/'ops-state/lease.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if read(root/'ops-state/ownership.json')['mode']!='maintenance':raise ValueError('maintenance_required')
        current=get()
        if current['versionId']!=expected_version:raise ValueError('version_conflict')
        active=current['active'];expected=before if action=='stage' else desired
        if (action=='close')!=active or not subset(spec(current),expected):raise ValueError('workflow_drift')
        if action=='arm' and any(n.get('disabled') for n in current['nodes']):raise ValueError('disabled_nodes')
        record={'action':action,'workflow':wid,'role':role,'previous_version':expected_version,'applied':False}
        if not apply:return record
        marker=doc/('transition-'+str(uuid4()))
        def save(suffix,obj):
            with marker.with_suffix(suffix).open('x') as f:os.fchmod(f.fileno(),0o600);json.dump(obj,f);f.flush();os.fsync(f.fileno())
            directory=os.open(doc,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        prior=doc/('pending-'+role+'.json')
        with prior.open('x') as file:json.dump({'transition':marker.name,'expected_version':expected_version},file);file.flush();os.fsync(file.fileno())
        save('.intent',record)
        if action in ('stage','restore'):
            target=desired if action=='stage' else before
            api.req('PATCH','/rest/workflows/'+wid,{**target,'versionId':expected_version})
        elif action=='arm':
            api.req('POST','/rest/workflows/'+wid+'/activate',{'versionId':expected_version});target=desired
        elif action=='close':
            api.req('POST','/rest/workflows/'+wid+'/deactivate',{});target=desired
        else:raise ValueError('unknown_action')
        after=get()
        if bool(after['active'])!=(action=='arm') or not subset(spec(after),target):raise ValueError('reconcile_native_state')
        record.update(applied=True,version=after['versionId'],active=after['active']);save('.done',record);prior.unlink();return record
if __name__=='__main__':
    import sys
    a=argparse.ArgumentParser();a.add_argument('root',type=Path);a.add_argument('role');a.add_argument('action',choices=['stage','arm','close','restore']);a.add_argument('--version',required=True);a.add_argument('--apply',action='store_true');x=a.parse_args()
    from configure_flows import n8n_login
    client=n8n_login()
    try:print(json.dumps(run(client,x.root,x.role,x.action,x.version,x.apply)))
    finally:client.req('POST','/rest/logout',{})
