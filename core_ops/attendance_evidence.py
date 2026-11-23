"""Private, read-only reconciliation for the existing n8n maintenance monitor."""
import collections
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/Cuiaba')
HOME = Path('/var/lib/jp-reply-capture')
INTAKE = Path('/var/lib/jp-migracao-revisao/entrada-operacional')


def read_db(path):
    return sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)


def iso(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZONE).isoformat(timespec='seconds')


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def addresses(row):
    k = row.get('key') or {}
    return {fingerprint(re.sub(r':\d+(?=@)', '', jid)) for jid in
            (k.get('remoteJid'), k.get('remoteJidAlt'), row.get('remoteJidAlt'))
            if isinstance(jid,str) and re.fullmatch(r'\d+(?::\d+)?@(lid|s\.whatsapp\.net)',jid)}


def scan(queue, index, read, instance):
    if index['last'] and queue.execute('select id from receipts where seq=?',(index['last'],)).fetchone() != (index['anchor'],):
        raise ValueError('origem alterada')
    for seq, anchor in queue.execute('select seq,id from receipts where seq>? order by seq limit 2048',(index['last'],)):
        meta, raw = read(anchor)
        for env in raw if isinstance(raw,list) else [raw]:
            if env.get('instance') != instance:
                continue
            event = str(env.get('event')).lower().replace('_','.')
            data = env.get('data')
            for row in data if isinstance(data,list) else [data]:
                if not isinstance(row,dict):
                    continue
                at = iso(meta['receivedAt'])
                if event=='connection.update':
                    index['connections'].append([seq,at,str(row.get('state','desconhecido'))[:30]])
                if event!='messages.upsert':
                    continue
                k = row.get('key') or {}
                mid = k.get('id')
                addr = addresses(row)
                if not mid or not addr or k.get('participant') or not re.fullmatch(
                        r'\d+(?::\d+)?@(lid|s\.whatsapp\.net)',str(k.get('remoteJid',''))):
                    continue
                if k.get('fromMe') is False:
                    name = str(row.get('pushName') or '').strip()[:90]
                    if re.search(r'\d{5}',name):
                        name=''
                    index['incoming'].setdefault(mid,dict(at=at,name=name,seq=seq,addr=sorted(addr)))
                if k.get('fromMe') is True:
                    for ident in addr:
                        index.setdefault('latest_outgoing',{})[ident]=max(at,index.get('latest_outgoing',{}).get(ident,''))
                    for part in (row.get('message') or {}).values():
                        ctx = part.get('contextInfo') if isinstance(part,dict) else None
                        if ctx and ctx.get('stanzaId'):
                            index['quotes'].setdefault(ctx['stanzaId'],{})[mid]=dict(at=at,addr=sorted(addr))
        index.update(last=seq,anchor=anchor)


def team_cases():
    sql = """select json_build_array(c.display_id,u.name,c.status) from conversations c
             join inboxes i on i.id=c.inbox_id left join users u on u.id=c.assignee_id
             where c.account_id=2 and i.account_id=2 and i.id=3 and i.name='Revisão privada da manutenção'"""
    output=subprocess.check_output(['docker','exec','atendimento-piloto-postgres-1','psql','-X','-v','ON_ERROR_STOP=1',
                                    '-U','postgres','-d','chat','-Atc',sql],text=True,timeout=10)
    return {i:(name,status) for i,name,status in map(json.loads,output.splitlines())}


def reply_index(state):
    linked=collections.defaultdict(list)
    for table,origin,native in [('jp_reply_attempts','source_message_id','message_id'),
                                ('jp_care_reply_outbox','origin_message_id','message_id'),
                                ('jp_reply_ack_outbox','inbound_message_id','provider_message_id')]:
        for a in state.get(table,[]):
            if a.get(origin) and a.get('attempted_at'):
                linked[a[origin]].append((a.get(native),a.get('destination_e164'),a['attempted_at']))
    return linked


def summarize(index, capture, ledger, state, cases, reviewed):
    links=reply_index(state)
    statuses={r[0]:r[1:] for r in capture.execute('''select c.message_id,l.status,t.conversation,c.kind,c.received_at
        from captured c left join core_links l on l.capture=c.rowid left join triage_cases t on t.capture=c.rowid
        order by c.rowid''')}
    physical={mid:(dest,status) for mid,dest,status in ledger.execute('select native,destination,state from dispatch_attempts where native is not null')}
    rows=[]
    for mid,entry in index['incoming'].items():
        review=reviewed.get(mid,{})
        name=review.get('name') or entry['name'] or 'Identidade a conferir'
        link,ticket,kind,_=statuses.get(mid,(None,None,None,None))
        assignee,case_status=cases.get(ticket,(None,None))
        status='Aguarda conferência' if mid in statuses else 'Histórico sem registro de tratamento'
        if ticket:
            status='Com a equipe' if assignee else 'Aguarda responsável'
        if case_status==1:
            status='Chamado encerrado sem desfecho comprovado'
        if link=='official_confirmed':
            status='Confirmação registrada na agenda'
        evidence=[]
        ids=set()
        def record(native, at, result):
            if native and native in ids:return
            ids.add(native)
            evidence.append(dict(at=at,result=result))
        for native,dest,stamp in links.get(mid,[]):
            if iso(stamp)<entry['at']:continue
            match=physical.get(native)
            result=match[1] if match and match[0].split('@')[0]==str(dest).lstrip('+') else 'incerto'
            record(native,iso(stamp),result)
        for native,out in index['quotes'].get(mid,{}).items():
            if out['at']>=entry['at'] and set(entry['addr']).intersection(out['addr']):
                record(native,out['at'],physical.get(native,(None,'observado'))[1])
        labels={'read':'Resposta lida','delivered':'Resposta entregue','accepted':'Aguardando recibo',
                'observado':'Resposta observada no WhatsApp','incerto':'Envio incerto','delivery_unknown':'Envio incerto'}
        outcomes={x['result'] for x in evidence}
        result=next((labels[k] for k in labels if k in outcomes),None)
        later=max((index.get('latest_outgoing',{}).get(ident,'') for ident in entry['addr']),default='')
        rows.append(dict(seq=entry['seq'],at=entry['at'],name=name,kind=kind or 'entrada a conferir',
                         status=status,reply=result,proof=evidence,ticket=ticket,owner=assignee,
                         later=later if later>entry['at'] else None,captured=mid in statuses))
    return sorted(rows,key=lambda r:r['at'],reverse=True)


def refresh(execution):
    sys.path[:0]=['/opt/jp-maintenance-triage','/opt/jp-reply-capture','/opt/jp-dispatch/current']
    from maintenance_triage import save,central_state
    from dispatch_service import read_record
    from reply_capture import INSTANCE
    folder=HOME/'attendance-evidence'
    folder.mkdir(mode=0o700,exist_ok=True)
    cache=folder/'source.json'
    index=json.loads(cache.read_bytes()) if cache.exists() else dict(last=0,anchor=None,incoming={},quotes={},connections=[])
    reviewed_file=folder/'reviewed.json'
    reviewed=json.loads(reviewed_file.read_bytes()) if reviewed_file.exists() else {}
    with closing(read_db(INTAKE/'data/index/receive.sqlite3')) as queue, closing(read_db(HOME/'replies.sqlite3')) as db, closing(read_db(Path('/var/lib/jp-dispatch/ledger.sqlite3'))) as ledger:
        scan(queue,index,lambda i:read_record(INTAKE,i,65534),INSTANCE)
        save(cache,index)
        rows=summarize(index,db,ledger,central_state(),team_cases(),reviewed)
        high=queue.execute('select coalesce(max(seq),0) from receipts').fetchone()[0]
        cursor=db.execute('select seq from cursor where slot=1').fetchone()[0]
    now=dt.datetime.now(ZONE)
    report=dict(at=now.isoformat(timespec='seconds'),execution=execution,high=high,processed=cursor,
                inspected=index['last'],origin='Sem comprovação independente na origem',
                connections=index['connections'][-30:],rows=rows)
    save(folder/(now.date().isoformat()+'.json'),report)
    save(folder/'latest.json',report)
    display=Path('/var/lib/jp-atendimento-evidencia/latest.json')
    save(display,report)
    os.chmod(display,0o640)
    return len(rows)
