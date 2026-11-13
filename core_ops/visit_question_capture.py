"""Associate two operator-selected visits with actual recipient receipts on the Dell tick."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def update(root, conf, ledger, db, queue, read, cfg):
    file=root/'ops-config/visit-questions.json'
    if not file.is_file():
        return
    cases=json.loads(file.read_bytes())['cases']
    now=time.time()
    path=root/'ops-config/read-grants/visit-questions-observed.json'
    previous=json.loads(path.read_bytes()) if path.is_file() else {}
    if all(now>dt.datetime.fromisoformat(p['until']).timestamp()+86400 for p in cases):
        return
    high=queue.execute('select max(seq) from receipts').fetchone()[0]
    if db.execute('select seq from cursor where slot=1').fetchone()!=(high,):
        return
    from dispatch_receipt import match_event, delivery_state
    from bridge import Held
    scope=json.loads(Path('/etc/jp-dispatch/receipt-scope.json').read_bytes())['instance_hash']
    start=previous.get('cursor',min(p['water'] for p in cases))
    records=queue.execute('select seq,id from receipts where seq>? order by seq limit 100',(start,)).fetchall()
    rows=[]
    ledger.row_factory=sqlite3.Row
    for p in cases:
        old=next((x for x in previous.get('rows',[]) if x['case']==p['case']),{})
        jid=p['phone'][1:]+'@s.whatsapp.net'
        xs=ledger.execute('select * from dispatch_attempts where destination=? and attempted>=? order by attempted,rowid',(jid,p['sourceStamp'])).fetchall()
        inbound=[(mid,seq) for mid,seq,encoded in db.execute('select message_id,seq,address_hashes from captured where seq>? order by seq',(p['seq'],)) if set(json.loads(encoded)).intersection(p['aliases'])]
        team=db.execute('select mid from team_events where pn=? and stamp>=?',(p['person'],p['sourceStamp'])).fetchall()
        out={'case':p['case'],'original':p['mid'],'untouched':not xs and not inbound and not team,'proofs':old.get('proofs',{})}
        for a in xs:
            if not a['native'] or a['state'] not in ('accepted','delivered','read'):
                continue
            pair=next((v for v in cfg['pairs'] if v['pn']==p['person']),None)
            mp=dict(instance=scope,attempt=a['attempt'],native=a['native'],pn=p['person'],reverse_pn=p['person'],lid=pair['lid'],forward_lid=pair['lid']) if pair else None
            if a['state'] in ('delivered','read'):
                out['proofs'][a['native']]=a['state']
            for seq,rid in records:
                record=read(rid)
                try:
                    result=delivery_state(match_event(record,a,'messages.update',scope,mp))
                except (Held,ValueError,KeyError,TypeError):
                    continue
                if out['proofs'].get(a['native'])!='read':
                    out['proofs'][a['native']]=result
        if xs:
            q=xs[0]
            if q['source']==digest(['central-event-v1','reply',p['mid']]) and q['body_hash']==digest(dict(number=p['phone'][1:],text=p['question'])):
                out['attempted']=True
            if q['source']==digest(['central-event-v1','reply',p['mid']]) and q['body_hash']==digest(dict(number=p['phone'][1:],text=p['question'])) and q['native'] in out['proofs']:
                out['native']=q['native']
                cfg['delivered'].add(q['native'])
                if len(inbound)==1 and len(xs)==1 and all(mid==q['native'] for mid, in team):
                    out['answer']=inbound[0][0]
                    cfg.setdefault('questionAnswers',{})[out['answer']]=(q['native'],len(p['members']))
                if inbound:
                    out['responded']=True
                if len(inbound)>=1 and len(xs)>=2:
                    for final in xs[1:]:
                        if final['source']==digest(['central-event-v1','reply',inbound[0][0]]) and final['native'] in out['proofs'] and final['body_hash'] in [digest(dict(number=p['phone'][1:],text=t)) for t in ('👍','Confirmado.')]:
                            out['completed']=True
        rows.append(out)
    cursor=records[-1][0] if records else start
    if cursor<high:
        for o in rows:
            o['untouched']=False
            o.pop('answer',None)
    for o in rows:
        if o.get('answer') and o.get('answer')!=next((x.get('answer') for x in previous.get('rows',[]) if x['case']==o['case']),None):
            # A verified receipt can arrive one tick later than its response.
            db.execute("""delete from core_links where status in ('human_review','human_hold','visit_review','identity_review')
                          and capture in (select rowid from captured where message_id=?)""",(o['answer'],))
    local=dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))
    refresh=any(r.get('answer') or (r['untouched'] and 7<=local.hour<19 and local.weekday()!=6) for r in rows)
    if previous.get('rows')==rows and start==cursor and (not refresh or now*1000-previous['at']<45000):
        return
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchown(f.fileno(),0,(root/'ops-config/reply-ingress.key').stat().st_gid)
            os.fchmod(f.fileno(),0o440)
            json.dump(dict(at=int(now*1000),rows=rows,cursor=cursor),f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)
