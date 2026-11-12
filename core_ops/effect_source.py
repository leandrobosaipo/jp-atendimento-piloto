"""Single-attempt provider bridge. The default deployment has no official write target."""
import datetime as dt
import hashlib,json,os,re,socket,struct,time,urllib.parse,urllib.request
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path('/var/lib/jp-migracao-revisao/core-operacional')
CF=Path('/etc/jp-task-effects');AUDIT=Path('/var/lib/jp-task-effects')
ORIGIN='https://sistema.appbarber.com.br'
PATH={'confirm':'/pages/actions/atualizaHorario.php','move':'/pages/cadastros/alteraAgendamento.php','create':'/pages/cadastros/insereAgendamentov5.php','calendar':'/pages/actions/buscaAgenda3.php','contact':'/jp/contacts/write','contact_read':'/jp/contacts/read'}
def need(x):
    if not x:raise ValueError('held')
def read(p):return json.loads(p.read_bytes())
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def domain(s):return digest([s[k] for k in ('role','task','source','operation','reference')])
def standing_current(g):
    """Verify the receiver caught up and the answer is still the latest for this contact."""
    policy=secure(Path('/etc/jp-reply-capture/automatic-confirmations.json'))
    need(policy['enabled'] is True and g['operation']=='confirm' and g['capture_sequence']>policy['afterSequence']
         and unix(g['source_at'])>=unix(policy['afterUtc']) and g['source']==digest([g['message_id'],g['fingerprint']]))
    person=hashlib.sha256((g['phone'][1:]+'@s.whatsapp.net').encode()).hexdigest()
    if person!=g['person']:
        with sqlite3.connect('file:/var/lib/jp-dispatch/ledger.sqlite3?mode=ro',uri=True) as ledger:
            destinations=ledger.execute('SELECT destination FROM dispatch_attempts WHERE native=?',(g['notice'],)).fetchall()
        need(len(destinations)==1)
        jid=destinations[0][0]
        need(re.fullmatch(r'55[1-9]\d9?[6-9]\d{7}@s\.whatsapp\.net',jid) and hashlib.sha256(jid.encode()).hexdigest()==g['person'])
        short,long=sorted([jid.split('@')[0],g['phone'][1:]],key=len)
        need(len(short)==12 and len(long)==13 and long==short[:4]+'9'+short[4:])
    person=g['person']
    src=ROOT.parent/'entrada-operacional/data/index/receive.sqlite3'
    with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as queue,sqlite3.connect('file:/var/lib/jp-reply-capture/replies.sqlite3?mode=ro',uri=True) as db:
        high=queue.execute('SELECT max(seq) FROM receipts').fetchone()[0]
        need(db.execute('SELECT seq FROM cursor WHERE slot=1').fetchone()==(high,) and db.execute('SELECT seq FROM team_cursor WHERE slot=1').fetchone()==(high,))
        rows=db.execute('SELECT rowid,seq,content_sha256,address_hashes,disposition FROM captured WHERE message_id=?',(g['message_id'],)).fetchall()
        need(len(rows)==1)
        rid,seq,fp,aliases,disposition=rows[0]
        need(seq==g['capture_sequence'] and fp==g['fingerprint'] and disposition=='possible_confirmation')
        need(db.execute('SELECT status FROM core_links WHERE capture=?',(rid,)).fetchone()==('awaiting_official',))
        aliases=set(json.loads(aliases))
        need(not any(aliases.intersection(json.loads(raw)) for raw, in db.execute('SELECT address_hashes FROM captured WHERE seq>? OR (seq=? AND rowid>?)',(seq,seq,rid))))
        need(not db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=? AND mid<>? LIMIT 1',(person,unix(g['source_at']),g['notice'])).fetchone())
def reschedule_current(g):
    """Last-moment no/hold guard for a captured same-day move grant."""
    need(g['operation']=='move' and g.get('approved_by')=='JP captured affirmative same-day reschedule'
         and g['source']==digest([g['message_id'],g['fingerprint']]) and re.fullmatch(r'[a-f0-9]{64}',g.get('person','')))
    stamp=unix(g['source_at']);need(0<=time.time()-stamp<=7*86400)
    src=ROOT.parent/'entrada-operacional/data/index/receive.sqlite3'
    with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as queue,sqlite3.connect('file:/var/lib/jp-reply-capture/replies.sqlite3?mode=ro',uri=True) as db:
        high=queue.execute('SELECT max(seq) FROM receipts').fetchone()[0]
        need(db.execute('SELECT seq FROM cursor WHERE slot=1').fetchone()==(high,) and db.execute('SELECT seq FROM team_cursor WHERE slot=1').fetchone()==(high,))
        rows=db.execute('SELECT rowid,seq,content_sha256,address_hashes FROM captured WHERE message_id=?',(g['message_id'],)).fetchall();need(len(rows)==1)
        rid,seq,fingerprint,aliases=rows[0];need(seq==g['capture_sequence'] and fingerprint==g['fingerprint'] and db.execute('SELECT status FROM core_links WHERE capture=?',(rid,)).fetchone()==('human_review',))
        aliases=set(json.loads(aliases));need(not any(aliases.intersection(json.loads(raw)) for raw, in db.execute('SELECT address_hashes FROM captured WHERE seq>? OR (seq=? AND rowid>?)',(seq,seq,rid))))
        need(not db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=? LIMIT 1',(g['person'],stamp)).fetchone())
def unix(t):return dt.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
def lockfile(f,obj):
    fd=os.open(f,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as out:out.write(json.dumps(obj).encode());out.flush();os.fsync(out.fileno())
    directory=os.open(f.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
def secure(p):
    st=p.stat();need(p.is_file() and not p.is_symlink() and st.st_uid==0 and not st.st_mode&0o022);return read(p)
def contact_headers(key,body):
    need(isinstance(key,str) and re.fullmatch(r'[a-f0-9]{64}',key))
    path=CF/'contact-permits'/(key+'.json')
    grant=secure(path);need(not path.stat().st_mode&0o077)
    need(grant['task']==key and grant['bodySha']==digest(body) and 0<grant['until']-time.time()<=300)
    need(re.fullmatch(r'[a-f0-9]{64}',grant['secret']))
    return {'X-Contact-Task':key,'X-Contact-Permit':grant['secret']}
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a):return None
def provider(config,op,body):
    contact=op in ('contact','contact_read')
    if contact:
        c=secure(CF/'contact-origin.json');origin=c['origin'];u=urllib.parse.urlsplit(origin)
        need(u.scheme=='https' and origin=='https://'+u.hostname and c['instance']==body['instance'])
        headers={'Authorization':'Bearer '+c['token'],'Content-Type':'application/json'}
        if op=='contact':headers.update(contact_headers(config.get('contact_task'),body))
        if c.get('accessId') and c.get('accessSecret'):headers.update({'CF-Access-Client-Id':c['accessId'],'CF-Access-Client-Secret':c['accessSecret']})
        data=json.dumps(body).encode();url=origin+PATH[op]
    else:
        need(config['origin']==ORIGIN)
        headers={'Cookie':config['cookieHeader'],'Origin':ORIGIN,'Referer':ORIGIN+'/index.php','X-Requested-With':'XMLHttpRequest','Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}
        data=urllib.parse.urlencode(body).encode();url=ORIGIN+PATH[op]
    with urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({})).open(urllib.request.Request(url,data=data,headers=headers),timeout=9) as r:
        need(r.status==200 and r.geturl()==url);data=r.read(2000001);need(len(data)<=2000000)
    return json.loads(data)
def admission(root,s,action):
    for k,pattern in [('workflow',r'[\w-]{1,128}'),('node',r'[\w-]{1,100}'),('execution',r'\d{1,20}'),('epoch',r'[a-f0-9-]{36}'),('client',r'\d{1,16}'),('phone',r'\+55\d{10,11}')]:need(re.fullmatch(pattern,s[k]))
    role=s['role'];stage=s['stage'];want='reply' if action=='calendar' or action=='write' and s.get('operation')!='contact' else 'registration'
    need(role==want and stage in (('reply-execute',) if role=='reply' else ('update',) if action=='write' else ('claim','update')))
    cfg=root/'ops-config';state=root/'ops-state';own=secure(state/'ownership.json')
    spec=secure(cfg/'manifest.json')[role];prefix=('/api/n8n/stages/' if role=='reply' else '/api/contact-registration/')
    need(spec['workflow']==s['workflow'] and spec['nodes'].get(s['node'])==prefix+stage)
    need(own['epoch']==s['epoch'] and own['mode'] in ('rehearsal','owned') and role in own['roles'] and time.time()<unix(own['until']))
    prefix=state/'admissions'/('-'.join(s[k] for k in ('workflow','execution','node')));q=read(prefix.with_suffix('.intent'))
    need(all(q[k]==s[k] for k in ('workflow','execution','node','epoch','stage')) and not prefix.with_suffix('.done').exists())
    return own
def grant(root,s,action):
    own=admission(root,s,action)
    if action=='write':
        key=domain(s);fp=root/'ops-config/grants'/(key+'.json');fields='role workflow node task source client phone operation reference'.split()
    else:
        fields='role workflow node source client phone'.split()+(['task'] if action=='calendar' else ['request_id'])
        key=digest([action]+[s[k] for k in fields]);fp=root/'ops-config/read-grants'/(key+'.json')
    g=secure(fp)
    need(g['schema']==1 and all(g.get(k)==s[k] for k in fields) and g['authorization'] is True and g['approved_by'] and g['epoch']==s['epoch'])
    start,end=unix(g['approved_at']),unix(g['until']);need(start<=time.time()<end<=unix(own['until']) and end-start<=300)
    need(g['target'] in ('fixture','official') and own['mode']==('rehearsal' if g['target']=='fixture' else 'owned'))
    return key,g
def execute(root,audit,cfg,msg,upstream=provider):
    action=msg['action'];need(action in ('write','calendar','contact_source','contact_read'));s=msg['scope'];b=msg.get('body',{})
    key,g=grant(root,s,action);need(cfg['mode']==g['target'])
    if g['target']=='fixture':need(upstream is not provider)
    else:need(upstream is provider)
    if action=='calendar':
        need(b in g['reads'] and set(b)=={'profissional[]','dia','tipo'} and b['tipo']=='1' and b['profissional[]'] in g['professionals'])
        data=upstream(cfg,action,b);need(isinstance(data,list) and all(isinstance(r,dict) and str(r.get('id','')).isdigit() for r in data))
        return [{k:v for k,v in r.items() if k in ('id','codCliente','resources','sercodigo','start','end','codStatus','CIt_Codigo','Age_Confirmado')} for r in data if str(r.get('codCliente'))==s['client']]
    if action=='contact_read':
        need(b=={'instance':g['instance'],'phone':s['phone'][1:]});return upstream(cfg,action,b)
    if action=='contact_source':
        from task_source import official,rows
        day=dt.datetime.now(ZoneInfo('America/Cuiaba')).date();br=lambda d:d.strftime('%d/%m/%Y')
        def load(op,params):return official(cfg,op,params) if upstream is provider else upstream(cfg,op,params)
        envelope=load('clients',{});need('recordsFiltered' in envelope or 'recordsTotal' in envelope)
        catalog=rows(envelope,'data');need(100<=len(catalog)<=12000)
        def phones(row):
            digits=[re.sub(r'\D','',str(row.get(k) or '')) for k in ('Cli_Celular','Celular','Telefone')]
            return {('55'+v if len(v) in (10,11) else v) for v in digits if v}
        def ownership_key(v):
            return v[:4]+v[5:] if re.fullmatch(r'55[1-9]\d9[6-9]\d{7}',v) else v
        matches=[r for r in catalog if ownership_key(s['phone'][1:]) in {ownership_key(n) for n in phones(r)}]
        need(len(matches)==1 and phones(matches[0])=={s['phone'][1:]} and str(matches[0].get('Codigo') or matches[0].get('Cli_Codigo'))==s['client'])
        need(sum(str(r.get('Codigo') or r.get('Cli_Codigo'))==s['client'] for r in catalog)==1)
        row=matches[0];need(str(row.get('PRe_Ativo','')).lower() not in ('0','false','inativo') and not re.search(r'n[aã]o.*(?:contatar|enviar|mandar)|bloquead|opt[ -]?out|descadastr|sem mensagens',str(row.get('Obs') or ''),re.I))
        client={k:row[k] for k in ('Codigo','Cli_Codigo','Nome','Cli_Nome','Cli_Celular','Celular','Telefone','Cadastro') if k in row}
        h=rows(load('history',dict(edtDataIni=br(day-dt.timedelta(days=365)),edtDataFim=br(day),tipoStatus='2',AgeCliente=s['client'],AgeProfissional='',AgeServico='')),'data',True)
        need(len(h)<=1000 and all(str(r.get('CodCliente') or r.get('CliCodigo'))==s['client'] for r in h))
        return {'client':client,'clients':[client],'history':[{k:r[k] for k in ('CodCliente','CliCodigo','Status','DataIni','Servico','Profissional') if k in r} for r in h],'checked_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    op=s['operation'];need(op in ('create','confirm','confirm_created','move','contact') and cfg.get('allowed',{}).get(op) is True)
    normalized=dict(b)
    if op=='create':
        need(set(b)==set('item tipoitem profissional dataagendamento duracao cliente cupom numitens ageorigem observacao lembrete tempolembrete sms whats'.split()))
        need(b['tipoitem']=='1' and b['numitens']=='1' and b['ageorigem']=='2' and b['cupom']=='' and b['tempolembrete']=='60')
        need(b['cliente']==s['client'] and all(b.get(k)=='0' for k in ('sms','whats','lembrete')) and re.fullmatch(r'JP nova visita [a-f0-9-]{36}',b['observacao']))
        normalized['observacao']='JP nova visita {native-uuid}'
    if op=='confirm':need(b=={'tipo':'5','agendamento':s['reference']})
    if op=='confirm_created':
        need(set(b)=={'tipo','agendamento'} and b['tipo']=='5' and re.fullmatch(r'\d{1,16}',b['agendamento']))
        # The first grant is completed before the native consumer can apply this separate capability.
        prior=[{**s,'operation':'create'},{**s,'operation':'move','reference':b['agendamento']}]
        def observed_id(before):
            f=audit/(domain(before)+'.observed')
            return f.is_file() and read(f).get('native_appointment_id')==b['agendamento']
        need(any(observed_id(x) for x in prior))
        normalized['agendamento']='{native-saved-appointment}'
    if op=='move':need(set(b)==set('dataAlteraAgendamento horaAlteraAgendamento tipoAlteraAgendamento codItem profissionalAlteraAgendamento'.split()) and b['tipoAlteraAgendamento']=='4')
    if op=='contact':
        proof=b['sourceProof'];when=unix(proof['observedAt'])
        need(proof['source']=='whatsapp_contact_screen_human' and proof['phone']==b['phone']==s['phone'][1:] and b['instance']==g['instance'] and proof['name']==b['expectedPreviousName'] and proof['proofId'] and 0<=time.time()-when<=300)
    need(digest(normalized)==g['payload_sha256'])
    lockfile(audit/(key+'.intent'),{'key':key,'at':int(time.time()),'execution':s['execution'],'payload_sha256':g['payload_sha256']})
    need(grant(root,s,action)[1]==g)
    if upstream is provider:
        latest=secure(CF/'mode.json');need(latest.get('mode')=='official' and latest.get('allowed',{}).get(op) is True)
        if g.get('approved_by')=='JP standing appointment confirmation':standing_current(g)
        if g.get('approved_by')=='JP captured affirmative same-day reschedule':reschedule_current(g)
        fresh=secure(Path('/etc/atendimento-piloto-integracao/appbarber-readonly.json'))
        need(fresh['source_generation']==cfg['source_generation']==secure(root/'ops-config/effect-generation.json')['generation'])
    result=upstream({**cfg,'contact_task':key} if op=='contact' else cfg,'confirm' if op=='confirm_created' else op,b)
    record={'at':int(time.time()),'result_digest':digest(result),'domain_readback_required':True}
    if op=='create' and isinstance(result,dict):
        rows=result.get('data')
        if isinstance(rows,list) and len(rows)==1 and str(rows[0].get('erro'))=='0' and re.fullmatch(r'\d{1,16}',str(rows[0].get('agecodigo') or '')):record['native_appointment_id']=str(rows[0]['agecodigo'])
    if op=='move' and isinstance(result,dict) and isinstance(result.get('result'),list) and len(result['result'])==1 and str(result['result'][0].get('erro'))=='0':record['native_appointment_id']=s['reference']
    lockfile(audit/(key+'.observed'),record)
    return result

def serve():
    import http.server,socketserver
    need(os.environ.get('LISTEN_PID')==str(os.getpid()) and os.environ.get('LISTEN_FDS')=='1')
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                need(self.path=='/v1' and struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]==64681)
                self.connection.settimeout(5);length=int(self.headers['Content-Length']);need(0<length<=24000)
                msg=json.loads(self.rfile.read(length));policy=secure(CF/'mode.json');need(policy.get('mode')=='official')
                cfg=secure(Path('/etc/atendimento-piloto-integracao/appbarber-readonly.json'))
                need(cfg['source_generation']==secure(ROOT/'ops-config/effect-generation.json')['generation'])
                out=execute(ROOT,AUDIT,{**cfg,**policy},msg);code=200
            except Exception:out={'status':'held_or_unknown','retry':False};code=409
            data=json.dumps(out,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    sock=socket.socket(fileno=3);srv=socketserver.UnixStreamServer.__new__(socketserver.UnixStreamServer)
    socketserver.BaseServer.__init__(srv,sock.getsockname(),Handler);srv.socket=sock;srv.timeout=45
    # Socket activation needs no persistent process between leases.
    import select
    while select.select([sock],[],[],45)[0]:srv.handle_request()
if __name__=='__main__':serve()
