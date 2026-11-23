"""Read back only this salon-approved list from the normal Dell monitor."""
import hashlib,json,sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from fixed_return_inbox import captured_snapshot

def key(*parts):
    return hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()

def read_results(items,baseline,state,ledger,capture,pairs):
    attempts={a['request_id']:a for a in state.get('fixed_return_dispatches',[])}
    notes=state.get('fixed_return_reviews',{})
    closed={r['key']:r for r in state.get('fixed_return_closed',{}).get('held',[])}
    result=[]
    for item in items:
        k=item['approval_key']
        row={'approval_key':k,'client':item['client'],'status':notes.get(k,{}).get('status') or baseline[k],
             'attempted':None,'delivered':False,'read':False,'response':None}
        if k in closed:row['status']='window_closed'
        result.append(row)
        a=attempts.get(k)
        if not a:continue
        row.update(status='delivery_unknown',attempted=a['attempted_at'],execution=a['execution'])
        phys=ledger.execute('select source,destination,state,native,attempted from dispatch_attempts where attempt=?',
                            (key('central-attempt-v1','fixed-return',a['attempt_id'],0),)).fetchone()
        if not phys:continue
        source,destination,status,mid,stamp=phys
        if (source!=key('central-event-v1','fixed-return',k) or not mid or
                a.get('message_id') not in (None,mid) or status not in ('accepted','delivered','read')):
            continue
        row.update(status=status,delivered=status in ('delivered','read'),read=status=='read')
        pn=hashlib.sha256(destination.encode()).hexdigest()
        aliases={pn}|{p['lid'] for p in pairs if p['pn']==pn}
        stamp_iso=datetime.fromtimestamp(stamp,timezone.utc).isoformat().replace('+00:00','')
        for seq,quote,hs,received in capture.execute(
            'select seq,quoted_id,address_hashes,received_at from captured where received_at>=? order by seq',(stamp_iso,)):
            if datetime.fromisoformat(received.replace('Z','+00:00')).timestamp()<stamp:continue
            if not aliases.intersection(json.loads(hs)):continue
            row['response']={'seq':seq,'at':received,'kind':'reply_to_invitation' if quote==mid else 'new_message'}
            if quote==mid:break
    return result

def update(root,core,execution,state,save):
    cfgdir=core/'ops-config/fixed-return'
    if not (cfgdir/'identities.json').is_file():return
    raw=(cfgdir/'approved.json').read_bytes()
    cfg=json.loads((cfgdir/'scope.json').read_bytes())
    if hashlib.sha256(raw).hexdigest()!=cfg['digest']:raise ValueError('approval_changed')
    items=json.loads(raw)['items']
    if len(items)!=11 or {r['approval_key'] for r in items}!=set(cfg['keys']):raise ValueError('scope_changed')
    baseline=json.loads((root/'return-window-readback.json').read_bytes())
    if baseline['approval']!=cfg['digest']:raise ValueError('review_changed')
    pairs=json.loads((cfgdir/'identities.json').read_bytes())['pairs']
    with closing(sqlite3.connect('file:/var/lib/jp-dispatch/ledger.sqlite3?mode=ro',uri=True)) as ledger, \
         captured_snapshot(Path('/var/lib/jp-reply-capture/replies.sqlite3')) as db:
        rows=read_results(items,{r['approval_key']:r['status'] for r in baseline['rows']},state,ledger,db,pairs)
    save(root/'approved-invitation-results.json',dict(approval=cfg['digest'],execution=execution,
         checked=datetime.now(timezone.utc).isoformat(),rows=rows))
