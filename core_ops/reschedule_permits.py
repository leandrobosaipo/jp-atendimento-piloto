"""Independent issuer for one captured, affirmative same-day move grant.

The capture side calls `issue` only after the core persisted a `grant_required`
proposal.  It never calls AppBarber and it cannot send a WhatsApp message.
"""
import datetime as dt, hashlib, json, os, re, tempfile
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def issue(root, conf, proposal, *, latest_is_source):
    policy_path=conf/'automatic-reschedules.json'
    if not policy_path.is_file(): return None
    policy=json.loads(policy_path.read_bytes())
    required=('task_id','source','appointment_id','client','phone','before','slot','capture_sequence','message_id','fingerprint')
    if policy.get('enabled') is not True or not all(k in proposal for k in required): return None
    if not re.fullmatch(r'[a-f0-9-]{36}',proposal['task_id']) or not re.fullmatch(r'[a-f0-9]{64}',proposal['source']) or not re.fullmatch(r'\+55\d{10,11}',proposal['phone']): return None
    if not latest_is_source(proposal): return None
    slot,before=proposal['slot'],proposal['before']
    if not (slot.get('date')==before.get('date') and slot.get('professional_code')==before.get('professional_code') and slot.get('service_code')==before.get('service_code') and slot.get('duration_minutes')==before.get('duration_minutes')): return None
    if slot.get('time')==before.get('time') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(slot.get('date'))) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',str(slot.get('time'))): return None
    own=json.loads((root/'ops-state/ownership.json').read_bytes()); now=dt.datetime.now(dt.timezone.utc); until=dt.datetime.fromisoformat(own['until'])
    if own.get('mode')!='owned' or 'reply' not in own.get('roles',[]) or until<=now: return None
    role=json.loads((root/'ops-config/manifest.json').read_bytes())['reply']; node,=[n for n,p in role['nodes'].items() if p=='/api/n8n/stages/reply-execute']
    body={'dataAlteraAgendamento':'/'.join(slot['date'].split('-')[::-1]),'horaAlteraAgendamento':slot['time'],'tipoAlteraAgendamento':'4','codItem':before['item_code'],'profissionalAlteraAgendamento':slot['professional_code']}
    grant={'schema':1,'role':'reply','workflow':role['workflow'],'node':node,'task':proposal['task_id'],'source':proposal['source'],'client':proposal['client'],'phone':proposal['phone'],'operation':'move','reference':proposal['appointment_id'],'epoch':own['epoch'],'approved_by':'JP captured affirmative same-day reschedule','authorization':True,'target':'official','approved_at':now.isoformat(),'until':min(now+dt.timedelta(minutes=4),until).isoformat(),'payload_sha256':digest(body),'capture_sequence':proposal['capture_sequence'],'message_id':proposal['message_id'],'fingerprint':proposal['fingerprint']}
    key=digest(['reply',grant['task'],grant['source'],'move',grant['reference']]); path=root/'ops-config/grants'/(key+'.json')
    if path.exists(): return json.loads(path.read_bytes())
    path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    # Keep the grant readable by the existing reply service group even where
    # the filesystem has not inherited setgid.  Never make it world-readable.
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
      with os.fdopen(fd,'w') as out:
        os.fchmod(out.fileno(),0o640)
        os.fchown(out.fileno(),-1,path.parent.stat().st_gid)
        json.dump(grant,out);out.flush();os.fsync(out.fileno())
      os.replace(tmp,path)
    finally: Path(tmp).unlink(missing_ok=True)
    return grant
