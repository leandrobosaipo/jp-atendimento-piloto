"""Private, read-only JP maintenance cohort.  It never sends or queues messages."""
import base64, datetime as dt, fcntl, gzip, hashlib, json, os, pathlib, tempfile, urllib.parse, urllib.request
from zoneinfo import ZoneInfo
from collections import defaultdict, Counter
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT=pathlib.Path('/var/lib/jp-migracao-revisao/followups-review-20261118')
CORE=pathlib.Path('/var/lib/jp-migracao-revisao/core-operacional')
SETTINGS=pathlib.Path('/etc/jp-maintenance-triage/scope.json')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None

def save(path,value):
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(value,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)

def _date(row):
    for key in ('DataIni','DataAgendamento','Age_Dat_Inicio'):
        raw=str(row.get(key) or '')[:19]
        for fmt in ('%d/%m/%Y %H:%M','%d/%m/%Y','%Y-%m-%d','%Y-%m-%d %H:%M:%S'):
            try:return dt.datetime.strptime(raw,fmt).date()
            except ValueError:pass

def _code(row):
    return str(row.get('CodCliente') or row.get('CliCodigo') or row.get('Codigo') or '').strip()

def _request(path, params):
    cfg=json.loads(pathlib.Path('/etc/atendimento-piloto-integracao/appbarber-readonly.json').read_bytes())
    origin='https://sistema.appbarber.com.br'
    if cfg['origin']!=origin:raise ValueError('agenda_origin')
    req=urllib.request.Request(origin+path,data=urllib.parse.urlencode(params).encode(),headers={'Cookie':cfg['cookieHeader'],'Origin':origin,'Referer':origin+'/index.php','Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'})
    with urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({})).open(req,timeout=20) as r:
        if r.geturl()!=origin+path or r.status!=200:raise ValueError('agenda_origin')
        raw=r.read(8*1024*1024+1)
    data=json.loads(raw)
    if not isinstance(data.get('data'),list) or not data['data'] or len(raw)>8*1024*1024:raise ValueError('agenda_incomplete')
    if any(int(data[k])!=len(data['data']) for k in ('recordsTotal','recordsFiltered') if k in data):raise ValueError('agenda_truncated')
    return data['data'],hashlib.sha256(raw).hexdigest()

def _consents():
    cfg=json.loads((CORE/'config/runtime.json').read_bytes()); enc=json.loads((CORE/'state/state.json').read_bytes())
    aad=(enc['format']+':gzip:'+str(enc['plain_bytes'])).encode()
    plain=AESGCM(bytes.fromhex(cfg['stateKey'])).decrypt(base64.b64decode(enc['iv']),base64.b64decode(enc['ciphertext'])+base64.b64decode(enc['tag']),aad)
    state=json.loads(gzip.decompress(plain)); return {str(x.get('client_code')):str(x.get('status')) for x in state['reactivation']['consents']}

def run(execution_id):
    if not isinstance(execution_id,str) or not execution_id.isdecimal():raise ValueError('execution_id_required')
    with (ROOT/'triage.lock').open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        return calculate(execution_id)

def calculate(execution_id):
    target=ROOT/'cohort-maintenance-15-90-n8n.json'
    today=dt.datetime.now(ZoneInfo('America/Cuiaba')).date()
    holds=set(json.loads(SETTINGS.read_bytes())['held'])
    if target.exists():
        prior=json.loads(target.read_bytes())
        if prior['day']==str(today) and prior.get('held')==sorted(holds):
            return {**prior['summary'],'refreshed':False,'source_execution':prior['execution_id']}
    clients,ch=_request('/pages/cadastros/buscaClientes.php',{})
    hist,hh=_request('/pages/relatorios/buscaAgendamentos.php',{'edtDataIni':(today-dt.timedelta(days=90)).strftime('%d/%m/%Y'),'edtDataFim':(today-dt.timedelta(days=1)).strftime('%d/%m/%Y'),'tipoStatus':'','AgeCliente':'','AgeProfissional':'','AgeServico':''})
    future,fh=_request('/pages/relatorios/buscaListaAgendamento.php',{'profissional':'','servico':'','cliente':'','dataini':today.strftime('%d/%m/%Y'),'datafim':(today+dt.timedelta(days=365)).strftime('%d/%m/%Y'),'tipo':'1'})
    all_dates=defaultdict(list); maintenance=defaultdict(list)
    for row in hist:
        key,when=_code(row),_date(row)
        if not key or not when: raise ValueError('history_row_incomplete')
        all_dates[key].append(when)
        service=str(row.get('Servico') or '').lower()
        if "title='Realizado'" in str(row.get('Status') or '') and 'manuten' in service and ('prótese' in service or 'protese' in service): maintenance[key].append(when)
    booked={_code(x) for x in future}; names={_code(x):str(x.get('Nome') or x.get('Cli_Nome') or '').strip() for x in clients}; consent=_consents(); rows=[]
    if '' in booked or '' in names or len(names)<100 or not set(holds)<=names.keys():raise ValueError('directory_incomplete')
    for key,dates in maintenance.items():
        last=max(dates); age=(today-last).days
        if key in booked or not 15<=age<=90 or any((today-d).days<15 for d in all_dates[key]): continue
        status=consent.get(key,'unknown')
        reason='pending_individual' if key in holds else 'review_conversation_and_capacity' if status=='opted_in' else 'opted_out' if status=='opted_out' else 'consent_unverified'
        rows.append({'client_code':key,'name':names.get(key),'last_maintenance':str(last),'days_since':age,'consent':status,'reason':reason})
    rows.sort(key=lambda x:(x['days_since'],x['name'] or ''))
    counts=dict(Counter(x['reason'] for x in rows))
    summary={'candidates':len(rows),'customers':len(names),'reasons':counts,'history_rows':len(hist),'future_rows':len(future)}
    save(target,{'day':str(today),'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'execution_id':execution_id,'held':sorted(holds),'sources':{'clients':ch,'history':hh,'future':fh},'summary':summary,'rows':rows})
    return {**summary,'refreshed':True,'source_execution':execution_id}
