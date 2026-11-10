"""Single-customer AppBarber reads for an authenticated native reply task."""
import datetime as dt
import hashlib,hmac,json,os,re,socket,struct,time,urllib.parse,urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ORIGIN='https://sistema.appbarber.com.br'
PAGES={'clients':'/pages/cadastros/buscaClientes.php','visits':'/pages/relatorios/buscaListaAgendamento.php','history':'/pages/relatorios/buscaAgendamentos.php','professionals':'/pages/actions/buscaProfissionais.php','services':'/pages/actions/buscaServicos.php','slots':'/pages/cadastros/buscaHorariosDisponiveisv2.php'}
FIELDS={
 'clients':'Codigo Cli_Codigo Cli_Celular Celular Telefone PRe_Ativo DDI PAF_DDI Cli_DDI Cli_Nome Nome',
 'visits':'Age_Codigo CliCodigo DataAgendamento HoraAgendamento Ser_Descricao ProNome ProCodigo Ser_Codigo Status',
 'history':'CodCliente Status DataIni Servico Profissional ProCodigo Pes_Codigo ProfissionalCodigo ProNome ProfissionalNome',
 'professionals':'Pes_Codigo Pes_Nome ProCodigo ProNome Codigo Nome',
 'services':'Ser_Codigo Ser_Descricao TipoItem Ser_Intervalo_Padrao'}
class Held(ValueError):pass
def need(v):
    if not v:raise Held('unverified_scoped_read')
def number(v):
    n=re.sub(r'\D','',str(v or ''))
    return ('55'+n if len(n) in (10,11) else n)
def project(row,op):
    return {k:row[k] for k in FIELDS[op].split() if k in row and isinstance(row[k],(str,int,float,type(None)))}
def rows(body,name,empty_error=False):
    need(isinstance(body,dict) and body.get('erro') in (None,0,'0',''))
    raw=body.get(name)
    if empty_error and raw=={'erro':''} and str(body.get('recordsTotal'))=='0' and str(body.get('recordsFiltered'))=='0':return []
    need(isinstance(raw,list) and all(isinstance(x,dict) and x.get('erro') in (None,0,'0','') for x in raw))
    for k in ('recordsFiltered','recordsTotal'):
        if k in body:need(str(body[k])==str(len(raw)))
    return raw
def list_data(data,key='data'):
    return {key:data,'recordsFiltered':len(data),'recordsTotal':len(data)}
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None
def official(cfg,operation,params):
    need(cfg['origin']==ORIGIN and operation in PAGES)
    url=ORIGIN+PAGES[operation]
    req=urllib.request.Request(url,data=urllib.parse.urlencode(params).encode(),headers={'Cookie':cfg['cookieHeader'],'Origin':ORIGIN,'Referer':ORIGIN+'/index.php','Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'})
    with urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({})).open(req,timeout=12) as response:
        need(response.status==200 and response.geturl()==url);raw=response.read(8*1024*1024+1)
    need(len(raw)<=8*1024*1024);return json.loads(raw)

class Reader:
    def __init__(self,cfg,secret,upstream=official):
        self.cfg,self.secret,self.upstream=cfg,secret,upstream;self.sessions={};self.nonces={}
    def invoke(self,envelope,now):
        raw=envelope['body'];need(isinstance(raw,str) and len(raw)<3800 and hmac.compare_digest(hmac.new(self.secret,raw.encode(),hashlib.sha256).hexdigest(),envelope['mac']))
        m=json.loads(raw);scope=m['scope']
        need(set(m)=={'scope','generation','at','nonce','op','params'} and m['generation']==self.cfg['source_generation'] and abs(now.timestamp()-m['at'])<20)
        need(set(scope)=={'workflow','node','execution','epoch','task','source','phone','client','stage','role'})
        need(scope['role']=='reply' and self.cfg['workflow']==scope['workflow'] and self.cfg['nodes'].get(scope['node'])==scope['stage'] and scope['stage'] in ('reply-next','reply-plan','reply-execute'))
        need(re.fullmatch(r'[0-9]{1,20}',scope['execution']) and re.fullmatch(r'[a-f0-9-]{36}',scope['epoch']) and re.fullmatch(r'[a-f0-9-]{36}',scope['task']) and re.fullmatch(r'[a-f0-9]{64}',scope['source']))
        need(re.fullmatch(r'55[1-9][0-9]{9,10}',scope['phone']) and (scope['client'] is None or re.fullmatch(r'\d{1,14}',scope['client'])))
        self.nonces={k:v for k,v in self.nonces.items() if v>now.timestamp()};self.sessions={k:s for k,s in self.sessions.items() if s['until']>now.timestamp()}
        nonce=m['nonce'];need(re.fullmatch(r'[a-f0-9]{32}',nonce) and nonce not in self.nonces and len(self.nonces)<1024)
        self.nonces[nonce]=now.timestamp()+40
        sid=tuple(scope[k] for k in ('workflow','node','execution','epoch','task','source','phone','client'))
        need(sid in self.sessions or len(self.sessions)<32)
        session=self.sessions.setdefault(sid,{'until':now.timestamp()+180,'uses':0,'catalog':{}})
        session['uses']+=1;need(session['uses']<=64)
        op,params=m['op'],m['params'];need(op in PAGES and isinstance(params,dict) and all(isinstance(v,str) for v in params.values()))
        if op=='clients':need(not params)
        if op in ('visits','history'):
            requested=params.get('cliente' if op=='visits' else 'AgeCliente')
            need(isinstance(requested,str) and re.fullmatch(r'\d{1,14}',requested) and (not scope['client'] or requested==scope['client']))
        if op in ('services','slots'):need(params.get('profissional') in {v['id'] for v in self.cfg['professionals'].values() if not v.get('human_only')})
        if op=='professionals':need(not params)
        if 'customer' not in session:
            response=self.upstream(self.cfg,'clients',{})
            need(isinstance(response,dict) and ('recordsFiltered' in response or 'recordsTotal' in response))
            catalog=rows(response,'data');need(100<=len(catalog)<=12000)
            matches=[r for r in catalog if scope['phone'] in {number(r.get(k)) for k in ('Celular','Cli_Celular','Telefone')}]
            need(len(matches)==1);r=matches[0];code=str(r.get('Codigo') or r.get('Cli_Codigo') or '')
            need(re.fullmatch(r'\d{1,14}',code) and (not scope['client'] or scope['client']==code) and sum(str(x.get('Codigo') or x.get('Cli_Codigo'))==code for x in catalog)==1)
            need(str(r.get('DDI') or r.get('PAF_DDI') or r.get('Cli_DDI'))=='55')
            need({number(r[k]) for k in ('Cli_Celular','Celular','Telefone') if r.get(k)}=={scope['phone']})
            need(str(r.get('PRe_Ativo','')).lower() not in ('false','0','inativo','inactive') and not re.search(r'n[aã]o.*(?:contatar|enviar|mandar)|bloquead|opt[ -]?out|descadastr|cancelar mensagens|sem mensagens',str(r.get('Obs') or ''),re.I))
            session.update(customer=project(r,'clients'),code=code)
        if op=='clients':return {**list_data([session['customer']]),'task_customer_verified':True}
        day=now.astimezone(ZoneInfo('America/Cuiaba')).date();utcday=now.astimezone(dt.timezone.utc).date();br=lambda d:d.strftime('%d/%m/%Y');code=session['code']
        if op=='visits':need(params=={'profissional':'','servico':'','cliente':code,'dataini':br(day),'datafim':br(utcday+dt.timedelta(days=365)),'tipo':'1'})
        if op=='history':need(params=={'edtDataIni':br(utcday-dt.timedelta(days=365)),'edtDataFim':br(day),'tipoStatus':'2','AgeCliente':code,'AgeProfissional':'','AgeServico':''})
        if op=='services':need(params=={'profissional':params['profissional'],'tipo':'9'})
        if op=='slots':
            need(set(params)=={'dia','mes','ano','servico','profissional'} and params['servico'] in session['catalog'].get(params['profissional'],set()))
            selected=dt.date(int(params['ano']),int(params['mes']),int(params['dia']));need(day<=selected<=day+dt.timedelta(days=14))
        key={'services':'servicos','professionals':'profissionais'}.get(op,'data')
        data=rows(self.upstream(self.cfg,op,params),key,empty_error=op in ('history','visits'));need(len(data)<=1000)
        if op in ('history','visits'):need(all(str(r.get('CliCodigo' if op=='visits' else 'CodCliente'))==code for r in data))
        if op=='professionals':
            permitted={v['id'] for v in self.cfg['professionals'].values()}
            data=[r for r in data if str(r.get('Pes_Codigo') or r.get('ProCodigo') or r.get('Codigo')) in permitted]
        if op=='services':
            data=[r for r in data if str(r.get('TipoItem'))=='1' and re.fullmatch(r'\d{1,14}',str(r.get('Ser_Codigo') or ''))]
            session['catalog'][params['profissional']]={str(r['Ser_Codigo']) for r in data}
        if op=='slots':
            if not data:return list_data([])
            choices=[r for r in data if str(r.get('PesCodigoPrincipal'))==params['profissional']]
            need(len(choices)==1 and isinstance(choices[0].get('horarios'),list) and len(choices[0]['horarios'])<=144)
            r=choices[0];hours=[{'DataIni':h['DataIni']} for h in r['horarios'] if isinstance(h,dict) and re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?',str(h.get('DataIni','')))]
            need(len(hours)==len(r['horarios']))
            return list_data([{'PesCodigoPrincipal':params['profissional'],'horarios':hours,**({'tempo':r['tempo']} if 'tempo'in r else {})}])
        return list_data([project(r,op) for r in data],key)

def serve():
    need(os.getenv('LISTEN_PID')==str(os.getpid()) and os.getenv('LISTEN_FDS')=='1')
    c=Path(os.environ['CREDENTIALS_DIRECTORY']);conf=json.loads((c/'reader').read_bytes());conf.update(json.loads((c/'role').read_bytes()))
    reader=Reader(conf,(c/'signing').read_bytes());server=socket.socket(fileno=3)
    server.settimeout(45)
    while True:
        try:conn,_=server.accept()
        except TimeoutError:return
        with conn:
            try:
                need(struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]==64681);conn.settimeout(4)
                with conn.makefile('rb') as stream:raw=stream.readline(6001)
                need(len(raw)<=6000 and raw.endswith(b'\n'));out=reader.invoke(json.loads(raw),dt.datetime.now(dt.timezone.utc));reply={'status':200,'body':out}
            except Exception:reply={'status':403,'body':{'error':'scoped_read_held'}}
            try:conn.sendall(json.dumps(reply,ensure_ascii=False).encode()+b'\n')
            except OSError:pass
if __name__=='__main__':serve()
