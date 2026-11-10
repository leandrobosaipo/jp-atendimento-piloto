"""Prepare one short-lived grant in a new private transfer directory, reading an approved JSON envelope on stdin."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def issue(envelope, dest):
    task, body = envelope['task'], envelope['body']
    assert re.fullmatch('[a-f0-9]{64}',task) and envelope.get('role')=='registration' and envelope.get('operation')=='contact'
    assert envelope.get('approved') is True and envelope['operator'].strip() and type(envelope['cacheExists']) is bool
    now=int(time.time()); until=envelope['until']
    assert type(until) is int and now < until <= now+300
    proof=body['sourceProof']
    age=time.time()-dt.datetime.fromisoformat(proof['observedAt'].replace('Z','+00:00')).timestamp()
    assert 0<=age<=300 and proof['source']=='whatsapp_contact_screen_human' and proof['phone']==body['phone']
    assert proof['name']==body['expectedPreviousName'] and proof['proofId'].strip()
    assert body['instance'] and re.fullmatch('[1-9][0-9]{7,14}',body['phone']) and body['name'].strip()==body['name'] and body['name']
    raw=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    secret=secrets.token_hex(32)
    mac={'schema':1,'role':'registration','operation':'contact','task':task,'operator':envelope['operator'],
         'cacheExists':envelope['cacheExists'],'bodySha':sha(raw),'secretSha':sha(secret.encode()),'issued':now,'until':until}
    dest.mkdir(mode=0o700)
    for machine,doc in [('mac',mac),('dell',{'task':task,'bodySha':sha(raw),'secret':secret,'until':until})]:
        path=dest/machine;path.mkdir(mode=0o700)
        with (path/(task+'.json')).open('x') as f:
            os.fchmod(f.fileno(),0o600);json.dump(doc,f);f.flush();os.fsync(f.fileno())
    return mac


if __name__=='__main__':
    if not __debug__:raise RuntimeError('validation required')
    os.umask(0o077)
    try: issue(json.load(sys.stdin),Path(sys.argv[1]))
    except Exception:sys.exit('permit refused; inspect private inputs')
