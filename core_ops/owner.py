"""Plan an ownership window or close admissions and enumerate uncertain calls."""
import argparse,datetime as dt,fcntl,hashlib,json,os,tempfile,uuid
from pathlib import Path

def transition(root,mode,roles,owner,cutover,apply):
    state=root/'ops-state';spec=json.loads((root/'ops-config/manifest.json').read_text());keyfile=root/'ops-config/roles.json'
    secret=json.loads(keyfile.read_text())
    # The two transport permissions belong to the bound inbound workflow. Neither
    # is a separate n8n workflow or an independently claimable processing role.
    permitted=set(spec)
    inbound=spec.get('inbound',{})
    if 'inbound' in roles and '/api/n8n/inbound' in inbound.get('nodes',{}).values():
        permitted.update(('public_first_reply','public_art_continuation'))
    if secret['owner']!=owner or not set(roles)<=permitted:raise ValueError('invalid_scope')
    if 'public_art_continuation' in roles and 'public_first_reply' not in roles:raise ValueError('first_reply_required')
    with (state/'lease.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);p=state/'ownership.json';old=json.loads(p.read_text())
        uncertain=sorted(x.stem for x in (state/'admissions').glob('*.intent') if not x.with_suffix('.done').exists())
        now=dt.datetime.now(dt.timezone.utc);epoch=str(uuid.uuid4())
        if mode!='maintenance':
            if old['mode']!='maintenance' or uncertain or not roles:raise ValueError('close_and_reconcile_required')
            if mode=='owned':
                proof=json.loads(cutover.read_text())
                expected=hashlib.sha256((root/'ops-config/manifest.json').read_bytes()).hexdigest()
                if cutover.stat().st_uid!=0 or cutover.stat().st_mode&0o077 or proof.get('manifest_sha256')!=expected or proof.get('owner')!=owner or set(proof.get('roles',[]))!=set(roles):raise ValueError('ownership_evidence_required')
                if not all(proof.get(k) is True for k in ['source_clocks_fenced','source_writers_drained','receiver_owner_verified','queues_reconciled','state_restore_verified']):raise ValueError('handover_incomplete')
                until=dt.datetime.fromisoformat(proof['until'])
                if not now<until<=now+dt.timedelta(days=1):raise ValueError('handover_expiry_invalid')
            else:until=now+dt.timedelta(minutes=15)
        else:roles=[];until=now;epoch=old.get('epoch',epoch)
        target={'mode':mode,'epoch':epoch,'owner':owner,'roles':sorted(set(roles)),'until':until.isoformat()}
        if apply:
            fd,tmp=tempfile.mkstemp(dir=state)
            with os.fdopen(fd,'w') as f:
                os.fchown(f.fileno(),p.stat().st_uid,p.stat().st_gid);os.fchmod(f.fileno(),p.stat().st_mode&0o777);json.dump(target,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,p);directory=os.open(state,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        return {'mode':mode,'applied':apply,'uncertain':uncertain,'roles':target['roles'],'epoch':epoch}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('root',type=Path);a.add_argument('mode',choices=['maintenance','rehearsal','owned']);a.add_argument('--owner',required=True);a.add_argument('--role',action='append',default=[]);a.add_argument('--handover',type=Path);a.add_argument('--apply',action='store_true')
    x=a.parse_args();print(json.dumps(transition(x.root,x.mode,x.role,x.owner,x.handover,x.apply)))
