"""Issue short-lived native confirm grants only for a newly captured, linked affirmative answer."""
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def issue(root, conf, row, event, request, db, queue):
    policy_file = conf/'automatic-confirmations.json'
    if not policy_file.is_file():
        return
    read = lambda file: json.loads(file.read_bytes())
    policy = read(policy_file)
    if (policy.get('enabled') is not True or row['seq'] <= policy['afterSequence']
            or dt.datetime.fromisoformat(event['at']) < dt.datetime.fromisoformat(policy['afterUtc']) or event['disposition'] != 'possible_confirmation'
            or not event['providerTime'] or event['intervened'] or event['conflict']
            or request['notice'] not in event['delivered'] or request['notice'] in event['humanNotices']
            or not re.fullmatch(r'\+55\d{10,11}', request['phone'])
            or not re.fullmatch(r'\d+', request['client'])
            or request['key'] != digest([event['mid'],event['content']])
            or not request['ids'] or len(set(request['ids'])) != len(request['ids'])
            or not all(isinstance(i,str) and i.isdecimal() for i in request['ids'])):
        return
    if queue.execute('SELECT max(seq) FROM receipts').fetchone()[0] != db.execute('SELECT seq FROM cursor WHERE slot=1').fetchone()[0]:
        return
    if db.execute('SELECT 1 FROM captured WHERE message_id=? AND content_sha256<>?',(row['message_id'],row['content_sha256'])).fetchone():
        return
    aliases=set(json.loads(row['address_hashes']))
    if any(aliases.intersection(json.loads(raw)) for raw, in db.execute('SELECT address_hashes FROM captured WHERE seq>? OR (seq=? AND rowid>?)',(row['seq'],row['seq'],row['rowid']))):
        return
    own=read(root/'ops-state/ownership.json')
    now=dt.datetime.now(dt.timezone.utc)
    if own['mode']!='owned' or 'reply' not in own['roles'] or dt.datetime.fromisoformat(own['until'])<=now:
        return
    role=read(root/'ops-config/manifest.json')['reply']
    node, = [n for n,path in role['nodes'].items() if path=='/api/n8n/stages/reply-execute']
    task=request['key'][:32]
    task='-'.join([task[:8],task[8:12],task[12:16],task[16:20],task[20:]])
    expiry=min(now+dt.timedelta(minutes=4),dt.datetime.fromisoformat(own['until']))
    for appointment in request['ids']:
        body={'tipo':'5','agendamento':appointment}
        grant=dict(schema=1,role='reply',workflow=role['workflow'],node=node,task=task,source=request['key'],
                   client=request['client'],phone=request['phone'],operation='confirm',reference=appointment,
                   epoch=own['epoch'],approved_by='JP standing appointment confirmation',authorization=True,
                   target='official',approved_at=now.isoformat(),until=expiry.isoformat(),payload_sha256=digest(body),
                   capture_sequence=row['seq'],message_id=row['message_id'],fingerprint=row['content_sha256'],
                   person=event['pn'],notice=request['notice'],source_at=event['at'])
        key=digest(['reply',task,request['key'],'confirm',appointment])
        path=root/'ops-config/grants'/(key+'.json')
        if path.exists() and (read(path).get('source')!=request['key'] or read(path).get('approved_by')!=grant['approved_by']):
            raise ValueError('grant_conflict')
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'w') as out:
                os.fchown(out.fileno(),0, (root/'ops-config/reply-ingress.key').stat().st_gid)
                os.fchmod(out.fileno(),0o640);json.dump(grant,out);out.flush();os.fsync(out.fileno())
            os.replace(tmp,path)
        finally:
            Path(tmp).unlink(missing_ok=True)
