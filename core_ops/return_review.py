"""Check a fixed salon-approved list in its next contact window."""
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from maintenance_triage import NoRedirect, SETTINGS, _consents, _request, save


def occupied(config,professional,date):
    origin='https://sistema.appbarber.com.br'
    if config['origin']!=origin:raise ValueError('agenda_origin')
    url=origin+'/pages/actions/buscaAgenda3.php'
    body=urllib.parse.urlencode({'profissional[]':professional,'tipo':'1','dia':date.strftime('%d/%m/%Y')}).encode()
    req=urllib.request.Request(url,data=body,headers={'Cookie':config['cookieHeader'],'Origin':origin,
        'Referer':origin+'/index.php','X-Requested-With':'XMLHttpRequest'})
    with urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({})).open(req,timeout=12) as response:
        data=json.load(response)
    if not isinstance(data,list):raise ValueError('agenda_incomplete')
    return [(dt.datetime.fromisoformat(v['start'][:19]),dt.datetime.fromisoformat(v['end'][:19]))
            for v in data if str(v['codStatus'])!='4']


def check(request, rows, now):
    import provider
    if len(rows)!=11 or len({row['client'] for row in rows})!=len(rows):
        raise ValueError('approval_list_changed')
    held=set(json.loads(SETTINGS.read_bytes())['held'])
    if len(held)!=4 or any(row['client'] in held for row in rows):
        raise ValueError('individual_hold_changed')
    date_text=lambda day:day.strftime('%d/%m/%Y')
    end=now.date()+dt.timedelta(days=365)
    visits,_=_request('/pages/relatorios/buscaListaAgendamento.php',dict(
        profissional='',servico='',cliente='',dataini=date_text(now.date()),datafim=date_text(end),tipo='1'))
    future=set()
    for visit in visits:
        start=dt.datetime.strptime(visit['Age_Dat_Inicio'],'%d/%m/%Y %H:%M')
        if not str(visit['CliCodigo']).isdecimal():raise ValueError('incomplete_visit')
        if start>=now.replace(tzinfo=None):future.add(str(visit['CliCodigo']))
    config=json.loads(Path('/etc/atendimento-piloto-integracao/appbarber-readonly.json').read_bytes())
    professionals={p['name'].strip():str(p['id']) for p in config['professionals'].values() if p.get('human_only') is False}
    service=config['services']['maintenance']['id']
    consent=_consents()
    cached={}
    result=[]
    for row in rows:
        cid=row['client']
        if cid in future or consent.get(cid)=='opted_out':
            result.append(dict(client=cid,status='booked' if cid in future else 'opted_out'))
            continue
        pid=professionals[row['professional']]
        available=[]
        for day,hour in row['options']:
            date=dt.date.fromisoformat(request['week_start']).replace(day=day)
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',hour):raise ValueError('invalid_hour')
            if not now.date()<=date<=end or not any(a<=hour<=b for a,b in provider.salon_windows(date)):continue
            key=pid,date
            if key not in cached:
                catalog=provider._read('catalog',dict(profissional=pid,tipo='9'),config)
                if sum(str(x['Ser_Codigo'])==service and str(x['TipoItem'])=='1' and str(x['Ser_Intervalo_Padrao'])=='45' for x in catalog['servicos'])!=1:
                    raise ValueError('service_changed')
                doc=provider._read('availability',dict(servico=service,profissional=pid,dia=str(date.day),mes=str(date.month),ano=str(date.year)),config)
                matches=[r for r in doc['data'] if str(r['PesCodigoPrincipal'])==pid]
                if len(matches)!=1 or str(matches[0].get('tempo',45))!='45':raise ValueError('incomplete_availability')
                cached[key]=({x['DataIni'][:5] for x in matches[0]['horarios']},occupied(config,pid,date))
            slots,busy=cached[key]
            start=dt.datetime.combine(date,dt.time.fromisoformat(hour))
            if hour in slots and not any(a<start+dt.timedelta(minutes=45) and start<b for a,b in busy):
                available.append([day,hour])
        result.append(dict(client=cid,status='slots_available' if available else 'no_slots',options=available))
    return result


def update(root,request_path,execution):
    request=json.loads(request_path.read_bytes())
    now=dt.datetime.now(ZoneInfo('America/Cuiaba'))
    if not dt.datetime.fromisoformat(request['opens'])<=now<dt.datetime.fromisoformat(request['closes']):return
    out=root/'return-window-readback.json'
    if out.exists() and json.loads(out.read_bytes()).get('approval')==request['sha256']:return
    approved=(root/'conferencia-convites-22nov.json').read_bytes()
    if hashlib.sha256(approved).hexdigest()!=request['sha256']:raise ValueError('approval_changed')
    sys.path.append('/usr/local/libexec/atendimento-piloto-integracao')
    rows=check(request,json.loads(approved)['items'],now)
    save(out,dict(checked=now.isoformat(),execution=execution,approval=request['sha256'],rows=rows))
