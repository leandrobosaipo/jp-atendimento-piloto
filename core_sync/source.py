"""Fixed AppBarber read contract for the isolated transactional core."""
import datetime as dt
import http.server
import hmac
import re
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
        if path==CALENDAR and params=={'profissional[]':row['pro'],'tipo':'1','dia':br(day)}:
            return [m for m in selection['members'] if m['date']==row['date'] and m['pro']==row['pro']]
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

def selected_calendar(rows, members):
    check_pending(rows, members)
    ids={m['id'] for m in members}
    return [r for r in rows if str(r.get('id')) in ids]

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

def clock_query(path, params, member, today, professionals):
    if set(member)!={'id','client','pro','service','day','time'} or member['pro'] not in professionals:
        return False
    if not all(re.fullmatch(r'[0-9]{1,18}',member[k]) for k in ('id','client','pro','service')):
        return False
    day=dt.datetime.strptime(member['day'],'%d/%m/%Y').date()
    if not today<=day<=today+dt.timedelta(days=13) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',member['time']):
        return False
    if path==AGENDA:
        base=dict(cliente=member['client'],dataini=member['day'],datafim=member['day'],tipo='1')
        return params in (dict(base,profissional=member['pro'],servico=member['service']),dict(base,profissional='',servico=''))
    return path==HISTORY and params==dict(edtDataIni=(today-dt.timedelta(days=364)).strftime('%d/%m/%Y'),
        edtDataFim=today.strftime('%d/%m/%Y'),tipoStatus='2',AgeCliente=member['client'],AgeProfissional='',AgeServico='')

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
            exact = body.get('captureVisit')
            clock = body.get('clockVisit')
            if clock is not None:
                if exact is not None or not hmac.compare_digest((self.server.reader.parent/'reply-ingress').read_text().strip(),self.headers.get('X-JP-Visit-Read','')) or not clock_query(path,params,clock,today,professionals):
                    raise ValueError('invalid notice read')
            elif exact is not None:
                token = (self.server.reader.parent/'reply-ingress').read_text().strip()
                if not hmac.compare_digest(token,self.headers.get('X-JP-Visit-Read','')) or path!=CALENDAR:
                    raise ValueError('unauthorized visit read')
                if set(exact)!={'id','client','service','time'} or not all(re.fullmatch(r'[0-9]{1,18}',exact[k]) for k in ('id','client','service')) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',exact['time']):
                    raise ValueError('invalid visit')
                day=dt.datetime.strptime(params['dia'],'%d/%m/%Y').date()
                if not today<=day<=today+dt.timedelta(days=8) or params!={'profissional[]':params['profissional[]'],'dia':day.strftime('%d/%m/%Y'),'tipo':'1'} or params['profissional[]'] not in professionals:
                    raise ValueError('invalid visit window')
            elif not allowed(path,params,today,professionals):
                selection=self.server.reader.parent/'agenda-selection'
                if selection.is_file():members=selected_read(path,params,today,json.loads(selection.read_bytes()))
                if members is None or path==CALENDAR and params.get('profissional[]') not in professionals:raise ValueError()
            # Exactly one upstream per request. Native jobs own retries and freshness.
            request=urllib.request.Request(ORIGIN+path,data=urllib.parse.urlencode(params).encode(),headers={
                'Cookie':cfg['cookieHeader'],'Origin':ORIGIN,'Referer':ORIGIN+'/index.php',
                'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'})
            if members and path==AGENDA:
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
            if exact is not None:
                rows=json.loads(data)
                if not isinstance(rows,list):raise ValueError('invalid calendar')
                matches=[r for r in rows if str(r.get('id'))==exact['id']]
                if len(matches)!=1:raise ValueError('identity')
                row=matches[0]
                if not all(str(row.get(k))==v for k,v in [('codCliente',exact['client']),('sercodigo',exact['service']),('resources',params['profissional[]'])]) or not str(row.get('start','')).startswith(day.isoformat()+'T'+exact['time']) or str(row.get('codStatus'))!='1' or str(row.get('Age_Confirmado')) not in ('0','1'):
                    raise ValueError('visit changed')
                data=json.dumps([{k:row.get(k) for k in ('id','codCliente','sercodigo','resources','start','end','codStatus','Age_Confirmado','CIt_Codigo')}]).encode()
            if members and path==CALENDAR:
                data=json.dumps(selected_calendar(json.loads(data),members)).encode()
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
