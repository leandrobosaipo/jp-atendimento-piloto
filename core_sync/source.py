"""Fixed AppBarber read contract for the isolated transactional core."""
import datetime as dt
import http.server
import json
import os
import socket
import socketserver
import struct
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ORIGIN = 'https://sistema.appbarber.com.br'
CLIENTS = '/pages/cadastros/buscaClientes.php'
AGENDA = '/pages/relatorios/buscaListaAgendamento.php'
HISTORY = '/pages/relatorios/buscaAgendamentos.php'
SERVICES = '/pages/actions/buscaServicos.php'
CALENDAR = '/pages/actions/buscaAgenda3.php'
MAX_BYTES = 20_000_000
def selected_read(path, params, today, selection):
    if not selection or selection.get('day') != today.isoformat():
        return None
    br = lambda day: day.strftime('%d/%m/%Y')
    for row in selection['members']:
        day = dt.date.fromisoformat(row['date'])
        if not today <= day <= today+dt.timedelta(days=7):
            continue
        if path==AGENDA and params=={'profissional':row['pro'],'servico':row['service'],'cliente':row['client'],
                                      'dataini':br(day),'datafim':br(day),'tipo':'1'}:
            return [r for r in selection['members'] if all(r[k]==row[k] for k in ('date','pro','client','service'))]
        if path==HISTORY and params=={'edtDataIni':br(today-dt.timedelta(days=364)),'edtDataFim':br(today),
                                     'tipoStatus':'2','AgeCliente':row['client'],'AgeProfissional':'','AgeServico':''}:
            return []
    return None

def check_pending(rows, members):
    if not isinstance(rows,list): raise ValueError('calendar_unverified')
    for m in members:
        hits=[r for r in rows if str(r.get('id'))==m['id']]
        if len(hits)!=1: raise ValueError('appointment_changed')
        r=hits[0]
        if (not all(str(r.get(key))==m[field] for key,field in [('resources','pro'),('sercodigo','service'),('codCliente','client')])
                or not str(r.get('start','')).replace('T',' ').startswith(m['date']+' '+m['time'])
                or str(r.get('codStatus'))!='1' or str(r.get('Age_Confirmado'))!='0'):
            raise ValueError('appointment_changed')

def allowed(path, params, today, professionals):
    if not isinstance(params, dict) or not all(isinstance(v,str) for v in params.values()):
        return False
    br=lambda d:d.strftime('%d/%m/%Y')
    monday=today-dt.timedelta(days=today.weekday())
    if path==CLIENTS:
        return params=={}
    if path==AGENDA:
        return any(params=={'profissional':'','servico':'','cliente':'','dataini':br(a),'datafim':br(b),'tipo':'1'}
                   for a,b in [(today,today),(monday,monday+dt.timedelta(days=13))])
    if path==HISTORY:
        cursor=today-dt.timedelta(days=364)
        while cursor<=today:
            end=min(cursor+dt.timedelta(days=13),today)
            if params=={'edtDataIni':br(cursor),'edtDataFim':br(end),'tipoStatus':'2','AgeCliente':'','AgeProfissional':'','AgeServico':''}:
                return True
            cursor=end+dt.timedelta(days=1)
        return False
    if path==SERVICES:
        return params.get('profissional') in professionals and params=={'profissional':params['profissional'],'tipo':'9'}
    if path==CALENDAR and set(params)=={'profissional[]','tipo','dia'} and params['profissional[]'] in professionals and params['tipo']=='1':
        try:
            date=dt.datetime.strptime(params['dia'],'%d/%m/%Y').date()
        except ValueError:
            return False
        return today-dt.timedelta(days=365)<=date<=today and br(date)==params['dia']
    return False

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None

class Source(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_POST(self):
        try:
            peer=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
            if struct.unpack('3i',peer)[1]!=self.server.peer_uid or self.path!='/read':
                raise ValueError()
            size=int(self.headers.get('content-length','0'))
            if not 0<size<=2048:raise ValueError()
            body=json.loads(self.rfile.read(size));path=body['path'];params=body['params']
            cfg=json.loads(self.server.reader.read_bytes())
            if cfg['origin']!=ORIGIN:raise ValueError()
            today=dt.datetime.now(ZoneInfo('America/Cuiaba')).date()
            professionals={v['id'] for v in cfg['professionals'].values()}
            members=None
            if not allowed(path,params,today,professionals):
                selection=self.server.reader.parent/'agenda-selection'
                if selection.is_file():members=selected_read(path,params,today,json.loads(selection.read_bytes()))
                if members is None:raise ValueError()
            # Exactly one upstream per request. Native jobs own retries and freshness.
            request=urllib.request.Request(ORIGIN+path,data=urllib.parse.urlencode(params).encode(),headers={
                'Cookie':cfg['cookieHeader'],'Origin':ORIGIN,'Referer':ORIGIN+'/index.php',
                'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'})
            if members:
                check=urllib.request.Request(ORIGIN+CALENDAR,headers=dict(request.header_items()),data=urllib.parse.urlencode({
                    'profissional[]':params['profissional'],'dia':params['dataini'],'tipo':'1'}).encode())
                with self.server.opener.open(check,timeout=8) as current:
                    if current.status!=200 or current.geturl()!=ORIGIN+CALENDAR:raise ValueError()
                    raw=current.read(MAX_BYTES+1)
                    if len(raw)>MAX_BYTES:raise ValueError()
                    check_pending(json.loads(raw),members)
            with self.server.opener.open(request,timeout=12) as r:
                if r.status!=200 or r.geturl()!=ORIGIN+path:raise ValueError()
                data=r.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES or not isinstance(json.loads(data),(dict,list)):raise ValueError()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except (ValueError,KeyError,TypeError,OSError):
            self.send_error(503,'official read unavailable')

if __name__=='__main__':
    # systemd owns socket mode/lifetime. Only this service can read the cookie.
    if int(os.environ.get('LISTEN_PID',0))!=os.getpid() or os.environ.get('LISTEN_FDS')!='1':raise SystemExit(2)
    with socketserver.UnixStreamServer('',Source,bind_and_activate=False) as server:
        server.socket.close();server.socket=socket.socket(fileno=3)
        server.peer_uid=int(os.environ['CORE_UID'])
        server.reader=Path(os.environ['CREDENTIALS_DIRECTORY'])/'reader'
        server.opener=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
        server.timeout=45
        # Exits on idle so the next activation loads the latest reader generation.
        last=time.monotonic()
        import select
        while select.select([server.socket],[],[],45)[0]:
            server.handle_request()
