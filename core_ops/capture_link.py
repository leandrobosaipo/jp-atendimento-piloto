"""Deliver verified received records locally to the core on the existing n8n tick."""
import datetime as dt
import hashlib
import http.client
import json
from pathlib import Path
import socket
import sqlite3
import time

import reply_capture as cap

CONF = Path('/etc/jp-reply-capture')
ROOT = Path('/var/lib/jp-migracao-revisao/core-operacional')
SOCKET = ROOT/'state/.jp-reply-ingress.sock'
LEDGER = Path('/var/lib/jp-dispatch/ledger.sqlite3')

def identity(data, pairs):
    key=data.get('key') or {}
    addresses={v for v in (key.get('remoteJid'),key.get('remoteJidAlt'),data.get('remoteJidAlt')) if v}
    if not addresses or not all(isinstance(a,str) and cap.DIRECT.fullmatch(a) for a in addresses):
        return ''
    found=set()
    for a in addresses:
        h=cap.digest(a)
        if a.endswith('@s.whatsapp.net'):
            found.add(h)
        else:
            pns=[p['pn'] for p in pairs if p['lid']==h]
            if len(pns)!=1 or sum(p['pn']==pns[0] for p in pairs)!=1:return ''
            found.add(pns[0])
    return next(iter(found)) if len(found)==1 else ''

def call(action, data=None):
    conn=http.client.HTTPConnection('localhost',timeout=12)
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(12);sock.connect(str(SOCKET));conn.sock=sock
        conn.request('POST','/'+action,body=json.dumps(data or {}).encode(),headers={'Authorization':'Bearer '+(CONF/'bind.key').read_text().strip()})
        response=conn.getresponse()
        if response.status!=200:raise RuntimeError('core_capture_unavailable')
        return json.loads(response.read(2048))

def make_event(row, read, cfg, outbound):
    meta,raw=read(row['record_id'])
    env=(raw if isinstance(raw,list) else [raw])[row['envelope_pos']]
    packet=env['data']
    msg=(packet if isinstance(packet,list) else [packet])[row['item_pos']]
    assert env['instance']==cap.INSTANCE and msg['key']['fromMe'] is False and msg['key']['id']==row['message_id']
    kind,disposition,quote,content=cap.classify(msg.get('message') or {})
    assert kind==row['kind'] and quote==row['quoted_id']
    pn=identity(msg,cfg['pairs'])
    received=dt.datetime.fromisoformat(meta['receivedAt'].replace('Z','+00:00'))
    stamp=msg.get('messageTimestamp');trusted=False;at=received
    if isinstance(stamp,(int,float,str)) and str(stamp).isdigit():
        stamp=int(stamp);stamp=stamp/1000 if stamp>10**12 else stamp
        if 0<stamp<=received.timestamp()+60:
            at=dt.datetime.fromtimestamp(stamp,dt.timezone.utc);trusted=True
    known=[e for e in outbound if e[1]==pn and e[2]<=at.timestamp()+1]
    verified={e[0] for e in known if e[0] in cfg['delivered']}
    last=max((e[2] for e in known),default=None)
    newest=[e[0] for e in known if e[2]==last]
    latest=newest[0] if len(newest)==1 and newest[0] in verified else None
    return dict(mid=row['message_id'],record=row['record_id'],content=row['content_sha256'],capture_sequence=row['seq'],
        pn=pn,at=at.isoformat(),providerTime=trusted,kind=kind,disposition=disposition,latestDelivered=latest,
        text=(content[:1500] if kind in ('text','emoji_text','reaction') else ''),quote=quote,
        delivered=sorted(verified),humanNotices=[m for m in verified if m in cfg.get('humanNotices',[])],intervened=False,conflict=row['disposition']=='changed_review')

def index_team_events(queue, db, read, cfg):
    """Increment the conversation index under the existing timer, including receipts while no customers reply."""
    db.execute('CREATE TABLE IF NOT EXISTS team_cursor(slot INTEGER PRIMARY KEY,seq INTEGER,record TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS team_events(pn TEXT,mid TEXT,stamp REAL,PRIMARY KEY(pn,mid))')
    db.execute('CREATE INDEX IF NOT EXISTS team_time ON team_events(pn,stamp)')
    seq,rid=db.execute('SELECT seq,record FROM team_cursor WHERE slot=1').fetchone() or (cfg['startSeq']-1,None)
    if rid and queue.execute('SELECT id FROM receipts WHERE seq=?',(seq,)).fetchone()!=(rid,):raise ValueError('team_index_source_changed')
    rows=queue.execute('SELECT seq,id FROM receipts WHERE seq>? ORDER BY seq LIMIT 64',(seq,)).fetchall()
    for seq,rid in rows:
        meta,envs=read(rid)
        for env in envs if isinstance(envs,list) else [envs]:
            if env.get('instance')!=cap.INSTANCE or str(env.get('event')).lower().replace('_','.')!='messages.upsert':continue
            batch=env.get('data')
            for msg in batch if isinstance(batch,list) else [batch]:
                if not isinstance(msg,dict):continue
                key=msg.get('key') or {}
                if key.get('fromMe') is not True or not key.get('id'):continue
                pn=identity(msg,cfg['pairs'])
                if pn:
                    stamp=dt.datetime.fromisoformat(meta['receivedAt'].replace('Z','+00:00')).timestamp()
                    db.execute('INSERT OR IGNORE INTO team_events VALUES (?,?,?)',(pn,key['id'],stamp))
    if rows:db.execute('INSERT OR REPLACE INTO team_cursor VALUES(1,?,?)',(seq,rid))
    return not queue.execute('SELECT 1 FROM receipts WHERE seq>? LIMIT 1',(seq,)).fetchone()


def notify_triage():
    endpoint=Path('/run/jp-reply-triage.sock')
    if not endpoint.is_socket():return {'ready':False}
    conn=http.client.HTTPConnection('localhost',timeout=8)
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(8);sock.connect(str(endpoint));conn.sock=sock
            conn.request('POST','/tick',body=b'')
            response=conn.getresponse()
            if response.status!=200:return {'ready':True,'pending':True}
            result=json.loads(response.read(2048))
            return {'ready':True,'processed':int(result['processed'])}
    except (OSError,ValueError,KeyError,http.client.HTTPException):
        return {'ready':True,'pending':True}

def has_newer_identity(db, row, person):
    """The capture schema stores aliases, never a plaintext/hash pn column."""
    rows=db.execute('SELECT address_hashes FROM captured WHERE seq>? OR (seq=? AND rowid>?)',(row['seq'],row['seq'],row['rowid'])).fetchall()
    return any(person in json.loads(raw) for raw, in rows)

def issue_ready_reschedules(db, read, cfg, outbound):
    """Poll persisted human-review captures; never depend on the consume tick.

    The core returns a proposal only after its official reads.  A later capture
    or team event makes the issuer reject it, so this is safe to repeat.
    """
    policy_file=CONF/'automatic-reschedules.json'
    if not policy_file.is_file():return 0
    policy=json.loads(policy_file.read_bytes())
    if policy.get('enabled') is not True or not isinstance(policy.get('afterSequence'),int):return 0
    issued=0
    rows=db.execute("""SELECT c.rowid,c.* FROM captured c JOIN core_links l ON l.capture=c.rowid
      WHERE l.status='human_review' AND c.seq>? AND c.kind='text' AND l.checked>=?
      ORDER BY c.seq DESC LIMIT 32""",(policy['afterSequence'],int(time.time())-600)).fetchall()
    cols=[d[0] for d in db.execute("SELECT c.rowid,c.* FROM captured c JOIN core_links l ON l.capture=c.rowid WHERE 0").description]
    for values in rows:
        row=dict(zip(cols,values));event=make_event(row,read,cfg,outbound)
        prepared=call('reschedule-proposal',event)
        proposal=prepared.get('proposal')
        if not proposal or proposal.get('message_id')!=row['message_id']:continue
        from reschedule_permits import issue as issue_reschedule
        def latest_is_source(p):
            return (int(p.get('capture_sequence',-1))==int(row['seq']) and p.get('message_id')==row['message_id']
              and p.get('fingerprint')==row['content_sha256']
              and not has_newer_identity(db,row,p.get('person'))
              and not db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=?',(event['pn'],dt.datetime.fromisoformat(event['at']).timestamp())).fetchone())
        if issue_reschedule(ROOT,CONF,proposal,latest_is_source=latest_is_source):issued+=1
    return issued

def deliver(queue, db, read, execution):
    db.execute('CREATE TABLE IF NOT EXISTS core_links(capture INTEGER PRIMARY KEY,status TEXT, members INTEGER,checked INTEGER,execution TEXT)')
    cfg=json.loads((CONF/'notice-scope.json').read_bytes())
    with db:
        caught_up=index_team_events(queue,db,read,cfg)
        if not caught_up:return {'processed':0,'index_catching_up':True}
        with sqlite3.connect(LEDGER.as_uri()+'?mode=ro',uri=True) as ledger:
            valid=ledger.execute("""SELECT native,destination,attempted,state FROM dispatch_attempts WHERE
                state IN ('accepted','delivered','read') AND native IS NOT NULL AND attempted>=?""",(int(time.time())-9*86400,)).fetchall()
            cfg['delivered']=set(cfg['delivered'])|{mid for mid,_,_,status in valid if status in ('delivered','read')}
            outbound=[(mid,cap.digest(jid),stamp) for mid,jid,stamp,_ in valid]
            from visit_question_capture import update
            update(ROOT,CONF,ledger,db,queue,read,cfg)
        pending=db.execute("""SELECT c.rowid,c.* FROM captured c LEFT JOIN core_links l ON c.rowid=l.capture
          WHERE l.capture IS NULL OR (l.status='awaiting_official' AND l.checked<?)
          ORDER BY (l.capture IS NOT NULL),c.seq,c.rowid LIMIT 3""",(int(time.time())-600,))
        cols=[d[0] for d in pending.description];rows=[dict(zip(cols,r)) for r in pending.fetchall()]
        if not rows: call('check')
        outcomes={}
        for row in rows:
            event=make_event(row,read,cfg,outbound)
            question=cfg.get('questionAnswers',{}).get(event['mid'])
            addressed=cfg.get('addressedYes',{}).get(event['mid'])==event['content'] and event['disposition']=='review'
            if question and question[0]==event['latestDelivered'] and (addressed or event['disposition'] in ('group_all','group_'+str(question[1]))):
                event['disposition']='possible_confirmation'
            notices=[r[2] for r in outbound if r[0] in event['delivered']]
            if notices:
                latest=db.execute('SELECT mid FROM team_events WHERE pn=? AND stamp>=?',(event['pn'],min(notices)))
                event['intervened']=any(mid not in event['delivered'] for mid, in latest)
            result=call('consume',event)
            if result.get('confirmRequest'):
                from confirmation_permits import issue
                issue(ROOT,CONF,row,event,result['confirmRequest'],db,queue)
            status=result['status'];outcomes[status]=outcomes.get(status,0)+1
            db.execute('INSERT OR REPLACE INTO core_links VALUES(?,?,?,?,?)',(row['rowid'],status,result['members'],int(time.time()),execution))
        issued=issue_ready_reschedules(db,read,cfg,outbound)
    return {'processed':len(rows),'outcomes':outcomes,'reschedule_grants':issued}
