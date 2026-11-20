"""Public first turns on the existing n8n capture clock. No send transport."""
import datetime as dt
import http.client
import json
import os
import re
import socket
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

CUIABA = ZoneInfo('America/Cuiaba')
PREFERENCE = 'Para qual dia ou período você gostaria de mudar a manutenção?'

def normal(value):
    return unicodedata.normalize('NFKD',value.casefold()).encode('ascii','ignore').decode()

def choose_day(text,today):
    weekdays=['segunda','terca','quarta','quinta','sexta','sabado','domingo']
    if 'depois de amanha' in text:return today+dt.timedelta(days=2)
    if 'amanha' in text:return today+dt.timedelta(days=1)
    if re.search(r'\bhoje\b',text):return today
    match=re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b',text)
    if match:
        try:return dt.date(int(match[3] or today.year),int(match[2]),int(match[1]))
        except ValueError:return None
    days=[i for i,d in enumerate(weekdays) if re.search(r'\b'+d+r'\b',text)]
    if len(days)==1:return today+dt.timedelta(days=(days[0]-today.weekday())%7 or 7)

def plan(text,config,query,today,offer_options=False):
    integration='/usr/local/libexec/atendimento-piloto-integracao'
    if integration not in sys.path:sys.path.append(integration)
    from policy import classify,artwork_quote
    words=normal(text)
    if re.search(r'\b(nao|nunca|cancele?\w*|reclam\w*|ferida|alerg\w*|sangr\w*|dor|coceira|outro numero|pix|estorno|reembolso|atendente|humano|parceria|divulgacao)\b',words):return None
    options=dict(intent='auto',service='',professional='',date='')
    intent,service,prof,_=classify(text,options,config)
    if intent=='sensitive':return None
    if re.search(r'\b(reagendar|remarcar|mudar|trocar)\b',words) and not re.search(r'\b(horario|agendamento|hora|manutencao|visita)\b',words):return None
    if intent=='availability' or re.search(r'\b(reagendar|remarcar|agendamento|encaixe)\b',words):
        price=''
        if service and re.search(r'\b(preco|valor|custa|custo)\b',words):
            fact,amount,decision,_=artwork_quote(text,service,config['catalog'])
            if decision!='answer':return None
            if fact.get('category')=='clarify':return amount
            price=amount+' '
        date=choose_day(words,today)
        if date is not None:
            from provider import salon_windows
            if not salon_windows(date):
                return price+f'O estúdio está fechado em {date:%d/%m}. Qual outro dia fica bom para você?'
        if not service:return 'Qual serviço você gostaria de agendar?'
        if date is None:return price+'Qual dia ou período você prefere para esse serviço?'
        if not prof and offer_options and not re.search(r'\b(com|apenas|somente|preferencia)\b',words):
            options=[]
            for slug in ('pedro','alysson'):
                professional=config['professionals'].get(slug)
                if not professional or professional.get('human_only'):continue
                params=dict(service_key=service,professional_key=slug,date=date.isoformat())
                if 'a tarde' in words:params['period']='afternoon'
                elif 'de manha' in words:params['period']='morning'
                facts=query(params)
                if facts.get('status')!='ok' or facts.get('date')!=date.isoformat():continue
                times=[v for v in facts.get('slots',[]) if isinstance(v,str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v)]
                if 'a tarde' in words:times=[v for v in times if '13:00'<=v<'18:00']
                if 'de manha' in words:times=[v for v in times if v<'12:00']
                if times:options.append(min(times)+' com '+professional['name'])
            if options:return f"Para {date:%d/%m}, tenho "+' ou '.join(options)+'. Qual você prefere?'
        if not prof:return price+'Você tem preferência de profissional para esse dia?'
        if config['professionals'][prof].get('human_only'):return None
        facts=query(dict(service_key=service,professional_key=prof,date=date.isoformat()))
        if facts.get('status')!='ok' or facts.get('date')!=date.isoformat():return None
        slots=facts.get('slots',[])
        if not isinstance(slots,list) or not all(re.fullmatch(r'\d{2}:\d{2}',s) for s in slots):return None
        if 'de manha' in words:slots=[s for s in slots if s<'12:00']
        elif 'a tarde' in words:slots=[s for s in slots if '12:00'<=s<'18:00']
        label=date.strftime('%d/%m')
        return price+((f'Para {label}, encontrei '+', '.join(slots[:3])+'. Qual horário você prefere?') if slots else f'Não encontrei vaga nesse período em {label}. Qual outro dia funciona para você?')
    if intent=='price':
        if not service and not any(set(normal(r['name']).split()) & set(words.split()) for r in config['catalog']['service_art_prices']):
            return 'De qual serviço você quer saber o valor?'
        _,message,decision,_=artwork_quote(text,service,config['catalog'])
        return message if decision=='answer' else None
    if intent=='services_art':
        return 'De qual serviço da tabela você gostaria de saber o valor?'
    if re.search(r'\b(servicos?|oferecem|trabalham|fazem)\b',words):
        if service:return 'Sim, temos '+config['services'][service]['name']+'. Você quer saber o valor ou consultar um horário?'
        return 'Fazemos manutenção de prótese capilar, corte de cabelo e barba, entre outros serviços. Qual deles você procura?'
    return None

def slot_query(params):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(7)
        sock.connect('/run/atendimento-piloto-integracao/public-slots.sock')
        sock.sendall(json.dumps(dict(op='public-slots',params=params)).encode()+b'\n')
        return json.loads(sock.makefile('rb').readline(8192))

def operator_available(seq):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect('/run/jp-reply-triage.sock')
        conn=http.client.HTTPConnection('localhost')
        conn.sock=sock
        conn.request('POST','/ownership/'+str(seq),body=b'')
        res=conn.getresponse()
        return res.status==200 and json.loads(res.read(1024)).get('available') is True

def publish(root,conf,db,queue,ledger,read,execution):
    import reply_capture as cap
    config_file=conf/'public-requests.json'
    if not config_file.exists():return
    config=json.loads(config_file.read_bytes())
    case=config['case']
    now=time.time()
    high=queue.execute('SELECT max(seq) FROM receipts').fetchone()[0]
    if db.execute('SELECT seq FROM cursor WHERE slot=1').fetchone()!=(high,) or db.execute('SELECT seq FROM team_cursor WHERE slot=1').fetchone()!=(high,):return
    sql="""SELECT c.seq,c.message_id,c.record_id,c.envelope_pos,c.item_pos,c.content_sha256,c.address_hashes,c.received_at
      FROM captured c JOIN core_links l ON l.capture=c.rowid
      WHERE c.kind='text' AND c.disposition!='changed_review' AND l.status IN ('identity_review','visit_review')
        AND (c.seq=? OR (c.seq>? AND ?)) ORDER BY c.seq DESC LIMIT 30"""
    candidates=[]
    for seq,mid,rid,ei,mi,fp,addresses,received in db.execute(sql,(case['seq'],config['after'],int(config['general']))):
        stamp=dt.datetime.fromisoformat(received.replace('Z','+00:00')).timestamp()
        exception=seq==case['seq'] and rid==case['record'] and mid==case['mid']
        if not 0<=now-stamp<=(86400 if exception else 3600) or exception and now>case['until']:continue
        source=cap.digest(json.dumps(['central-event-v1','reply',mid],separators=(',',':')))
        if ledger.execute('SELECT 1 FROM dispatch_attempts WHERE source=? LIMIT 1',(source,)).fetchone():continue
        _,raw=read(rid)
        env=(raw if isinstance(raw,list) else [raw])[ei]
        batch=env['data'];msg=(batch if isinstance(batch,list) else [batch])[mi]
        key=msg.get('key') or {};jid=key.get('remoteJid') or ''
        if env.get('instance')!=cap.INSTANCE or key.get('fromMe') is not False or key.get('id')!=mid or key.get('participant'):continue
        if not re.fullmatch(r'55[1-9]\d{9,10}@s\.whatsapp\.net',jid):continue
        if exception and jid!=case['jid']:continue
        kind,disp,quote,text=cap.classify(msg.get('message') or {})
        aliases=set(json.loads(addresses))
        if kind!='text' or quote or not isinstance(text,str) or len(text)>500 or cap.digest(jid) not in aliases:continue
        if cap.digest(json.dumps([kind,disp,quote,text,sorted(aliases)],ensure_ascii=False))!=fp:continue
        ts=msg.get('messageTimestamp')
        if not str(ts).isdigit() or abs(int(ts)-stamp)>180:continue
        if exception:aliases.update(case['aliases'])
        number=jid.split('@')[0]
        alternate=(number[:4]+'9'+number[4:]) if len(number)==12 else number[:4]+number[5:]
        if ledger.execute('SELECT 1 FROM dispatch_attempts WHERE attempted>=? AND destination IN (?,?) LIMIT 1',(stamp-1,jid,alternate+'@s.whatsapp.net')).fetchone():continue
        if any(aliases.intersection(json.loads(x[0])) for x in db.execute('SELECT address_hashes FROM captured WHERE seq>?',(seq,))):continue
        for pair in config.get('pairs',[]):
            if pair['lid'] in aliases or pair['pn'] in aliases:aliases.update((pair['lid'],pair['pn']))
        if any(db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=? LIMIT 1',(a,stamp-1)).fetchone() for a in aliases):continue
        # Inspect the live ingress for native LIDs not mapped by the historical team index.
        if high-seq>600:continue
        intervened=False
        for (other,) in queue.execute('SELECT id FROM receipts WHERE seq>? AND seq<=?',(seq,high)):
            for e in as_list(read(other)[1]):
                if e.get('instance')!=cap.INSTANCE or str(e.get('event')).lower().replace('_','.')!='messages.upsert':continue
                for row in as_list(e.get('data')):
                    if not isinstance(row,dict):continue
                    k=row.get('key') or {}
                    addrs=[k.get('remoteJid'),k.get('remoteJidAlt'),row.get('remoteJid'),row.get('remoteJidAlt')]
                    if k.get('id')!=mid and row.get('message') and any(cap.digest(a) in aliases for a in addrs if isinstance(a,str)):intervened=True
        if intervened:continue
        learned=None
        try:
            from published_answers import candidate,contextual_question,resolve
            if not exception and (conf/'published-answers.json').is_file() and resolve(text,conf):
                if not operator_available(seq):continue
                learned=candidate(text,seq,execution,db,conf)
                if not learned:continue
                answer=learned['answer']
            else:
                question=contextual_question(text,seq,stamp,jid,aliases,db,ledger) if not exception and (conf/'published-answers.json').is_file() else text
                if not exception and question==text:
                    question=booking_continuation(text,seq,stamp,jid,aliases,db,ledger,read,config)
                answer=PREFERENCE if exception else plan(question,config,slot_query,dt.datetime.now(CUIABA).date(),offer_options=question!=text)
            if not answer or not operator_available(seq):continue
        except (OSError,ValueError,http.client.HTTPException):
            # The private case remains open. Continue processing unrelated agenda replies.
            continue
        if queue.execute('SELECT max(seq) FROM receipts').fetchone()!=(high,):break
        candidates.append(dict(seq=seq,mid=mid,record=rid,fp=fp,number=jid.split('@')[0],answer=answer,exception=exception,received=received))
        if learned:
            candidates[-1]['knowledge']={k:learned[k] for k in ('revision','topic','source')}
            db.execute('CREATE TABLE IF NOT EXISTS kb_proposals(seq INTEGER PRIMARY KEY,revision TEXT,topic TEXT,source TEXT,execution TEXT)')
            db.execute('INSERT OR IGNORE INTO kb_proposals VALUES(?,?,?,?,?)',(seq,learned['revision'],learned['topic'],learned['source'],execution))
        break
    path=root/'ops-config/read-grants/public-requests.json'
    if not candidates and not path.exists():return
    if not candidates and not json.loads(path.read_bytes()).get('items'):return
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchown(f.fileno(),0,(root/'ops-config/reply-ingress.key').stat().st_gid);os.fchmod(f.fileno(),0o440)
            json.dump(dict(items=candidates,at=time.time(),execution=execution),f)
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

def as_list(value):
    return value if isinstance(value,list) else [value]

def booking_continuation(text,seq,stamp,jid,aliases,db,ledger,read,config):
    """A short service answer belongs only to our preceding scheduling question."""
    if normal(text).strip(' .!') not in ('manutencao','manutencao de protese','manutencao de protese capilar','corte','barba'):
        return text
    import hashlib
    previous=next((r for r in db.execute('SELECT message_id,record_id,envelope_pos,item_pos,address_hashes FROM captured WHERE seq<? ORDER BY seq DESC LIMIT 60',(seq,)) if aliases.intersection(json.loads(r[4]))),None)
    if not previous:return text
    mid,rid,ei,mi,_=previous
    source=hashlib.sha256(json.dumps(['central-event-v1','reply',mid],separators=(',',':')).encode()).hexdigest()
    attempts=ledger.execute('SELECT source,native,attempted,state FROM dispatch_attempts WHERE destination=? AND attempted BETWEEN ? AND ? ORDER BY attempted DESC LIMIT 2',(jid,int(stamp)-1800,int(stamp))).fetchall()
    if not attempts or attempts[0][0]!=source or not attempts[0][1] or attempts[0][3] not in ('accepted','delivered','read'):return text
    if any(db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=? AND mid<>? LIMIT 1',(a,attempts[0][2],attempts[0][1])).fetchone() for a in aliases):return text
    env=as_list(read(rid)[1])[ei];row=as_list(env['data'])[mi];key=row.get('key') or {}
    if key.get('id')!=mid or key.get('remoteJid')!=jid or key.get('fromMe') is not False:return text
    msg=row.get('message') or {};prior=msg.get('conversation') or (msg.get('extendedTextMessage') or {}).get('text')
    if not isinstance(prior,str) or len(prior)>300:return text
    today=dt.datetime.fromtimestamp(stamp,CUIABA).date()
    if plan(prior,config,lambda _: {},today)!='Qual serviço você gostaria de agendar?':return text
    return prior+' '+text
