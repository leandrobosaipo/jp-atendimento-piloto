"""Read-only checks for the JP directory and the private worker's visit anchors."""
import datetime as dt
import json
import urllib.parse
import urllib.request

ORIGIN = 'https://sistema.appbarber.com.br'
CLIENTS = '/pages/cadastros/buscaClientes.php'
AGENDA = '/pages/relatorios/buscaListaAgendamento.php'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def report(reader, path, params):
    if reader['origin'] != ORIGIN or path not in (CLIENTS, AGENDA):
        raise ValueError('unexpected_reader_origin')
    url = ORIGIN + path
    req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), headers={
        'Cookie': reader['cookieHeader'], 'Origin': ORIGIN, 'Referer': ORIGIN+'/index.php',
        'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'})
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=12) as response:
        raw = response.read(8*1024*1024+1)
        if response.status != 200 or response.geturl() != url or len(raw)>8*1024*1024:
            raise ValueError('incomplete_official_response')
    return json.loads(raw)


def empty_directory(doc):
    row = doc.get('data')
    return (doc.get('recordsTotal') in (0,'0') and doc.get('recordsFiltered') in (0,'0')
            and not doc.get('erro') and isinstance(row,dict) and row.get('DT_RowId') == 0
            and {'Codigo','Cli_Codigo','Celular'} <= row.keys()
            and all(v in ('',0,None,'0') for v in row.values()))


def identity(reader, anchors):
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).date()
    br = lambda d: d.strftime('%d/%m/%Y')
    params = dict(profissional='',servico='',cliente='',dataini=br(today),
                  datafim=br(today+dt.timedelta(days=13)),tipo='1')
    doc = report(reader, AGENDA, params)
    rows = doc['data']
    if doc.get('erro') or not isinstance(rows,list):
        raise ValueError('invalid_agenda')
    fields = ('Age_Codigo','CliCodigo','ProCodigo','Ser_Codigo','DataAgendamento','HoraAgendamento')
    def key(values):
        return tuple(str(x) for x in values[:-1]) + (str(values[-1])[:5],)
    live = {key([r[k] for k in fields]) for r in rows}
    if len({r[0] for r in live}) != len(rows):
        raise ValueError('ambiguous_agenda')
    names = ('appointment_id','client_code','professional_code','service_code','appointment_date','appointment_time')
    matches = live.intersection(key([a[k] for k in names]) for a in anchors)
    if len({r[1] for r in matches}) < 2:
        raise ValueError('jp_identity_unverified')
    return {r[1] for r in matches}


def partial_failure(reader, anchors):
    if not empty_directory(report(reader,CLIENTS,{})):
        return False
    identity(reader,anchors)
    return empty_directory(report(reader,CLIENTS,{}))


def verified_directory(reader, anchors):
    clients = identity(reader,anchors)
    doc = report(reader,CLIENTS,{})
    rows = doc.get('data')
    if not isinstance(rows,list) or len(rows)<100 or doc.get('erro'):
        raise ValueError('directory_unverified')
    if any(str(doc.get(k))!=str(len(rows)) for k in ('recordsTotal','recordsFiltered')):
        raise ValueError('incomplete_directory')
    ids = {str(r['Codigo']) for r in rows}
    if len(ids)!=len(rows) or not all(i.isdecimal() for i in ids) or not clients <= ids:
        raise ValueError('directory_identity_mismatch')
    return len(rows)
