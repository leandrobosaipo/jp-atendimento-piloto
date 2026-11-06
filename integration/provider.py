"""Read only service prices and offered slots; never fetch client lists or write appointments."""
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ORIGIN = 'https://sistema.appbarber.com.br'
PATHS = {
    'prices': '/pages/cadastros/buscaServicosv2.php',
    'catalog': '/pages/actions/buscaServicos.php',
    'availability': '/pages/cadastros/buscaHorariosDisponiveisv2.php',
}

def _read(kind, params, config):
    if config.get('origin') != ORIGIN or not isinstance(config.get('cookieHeader'), str) or not config['cookieHeader']:
        raise ValueError('missing_session')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args): return None
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    req = urllib.request.Request(ORIGIN + PATHS[kind], data=urllib.parse.urlencode(params).encode(), method='POST', headers={
        'Cookie': config['cookieHeader'], 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': ORIGIN, 'Referer': ORIGIN + '/index.php', 'X-Requested-With': 'XMLHttpRequest',
    })
    with opener.open(req, timeout=10) as response:
        raw = response.read(524289)
        if len(raw) > 524288 or raw.lstrip().startswith(b'<'): raise ValueError('invalid_source')
        return json.loads(raw)

def _rows(value, key):
    if not isinstance(value, dict) or value.get('erro') not in (None, '', 0, '0'):
        raise ValueError('invalid_source')
    rows = value.get(key)
    if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(r, dict) or r.get('erro') not in (None, '', 0, '0') for r in rows):
        raise ValueError('invalid_source')
    if 'recordsFiltered' in value and str(value['recordsFiltered']) != str(len(rows)):
        raise ValueError('incomplete_source')
    return rows

def _money(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}', value.strip()):
        raise ValueError('invalid_price')
    number = Decimal(value.strip().replace('.', '').replace(',', '.')) * 100
    if not number.is_finite() or number < 0 or number > 100_000_000: raise ValueError('invalid_price')
    return int(number)

def query(op, params, config, *, read=None, now=None):
    """Return a small fresh proof or an explicit unavailable result, without provider payloads."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None: raise ValueError('timezone_required')
    observed = now.astimezone(dt.timezone.utc).isoformat()
    failure = {'status':'unavailable', 'source':'appbarber', 'observed_at':observed, 'reason':'official_read_unavailable'}
    if op not in ('prices', 'availability') or not isinstance(params, dict):
        return dict(failure, reason='invalid_operation')
    allowed = {'service_key'} if op == 'prices' else {'service_key', 'date', 'professional_key'}
    if set(params) - allowed: return dict(failure, reason='invalid_parameters')
    if not isinstance(params.get('service_key'), str): return dict(failure, reason='unknown_service')
    service = config.get('services', {}).get(params['service_key'])
    if not isinstance(service, dict) or not re.fullmatch(r'\d{1,20}', str(service.get('id', ''))):
        return dict(failure, reason='unknown_service')
    reader = read or (lambda kind, p: _read(kind, p, config))
    try:
        if op == 'prices':
            rows = _rows(reader('prices', {}), 'data')
            matched = [r for r in rows if str(r.get('Ser_Codigo')) == service['id']]
            if len(matched) != 1: raise ValueError('ambiguous_service')
            row = matched[0]
            if str(row.get('Moeda', '')).strip() != 'R$': raise ValueError('unexpected_currency')
            name = str(row.get('Ser_Descricao', '')).strip()
            if not name or len(name) > 160: raise ValueError('invalid_name')
            return {'status':'ok', 'source':'appbarber', 'observed_at':observed, 'service_id':service['id'],
                    'service_key':params['service_key'], 'name':name, 'price_cents':_money(row.get('Ser_Valor')), 'currency':'BRL'}
        professional = config.get('professionals', {}).get(params.get('professional_key'))
        if not professional: return dict(failure, reason='professional_required')
        if professional.get('human_only') is not False:
            return dict(failure, status='human_required', reason='human_only_professional')
        pid = str(professional.get('id', ''))
        if not re.fullmatch(r'\d{1,20}', pid): return dict(failure, reason='unknown_professional')
        raw_date = params.get('date')
        if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date): raise ValueError('invalid_date')
        date = dt.date.fromisoformat(raw_date)
        if not 0 <= (date - now.astimezone(ZoneInfo('America/Cuiaba')).date()).days <= 14:
            return dict(failure, reason='date_outside_test_window')
        catalog = _rows(reader('catalog', {'profissional':pid, 'tipo':'9'}), 'servicos')
        selected = [r for r in catalog if str(r.get('Ser_Codigo')) == service['id'] and str(r.get('TipoItem')) == '1']
        if len(selected) != 1: raise ValueError('service_not_offered')
        duration = str(selected[0].get('Ser_Intervalo_Padrao', ''))
        if not duration.isdigit() or not 1 <= int(duration) <= 1440: raise ValueError('invalid_duration')
        offered = _rows(reader('availability', {'servico':service['id'], 'profissional':pid, 'dia':str(date.day), 'mes':str(date.month), 'ano':str(date.year)}), 'data')
        selected = [r for r in offered if str(r.get('PesCodigoPrincipal')) == pid]
        if len(selected) != 1 or not isinstance(selected[0].get('horarios'), list): raise ValueError('invalid_slots')
        if 'tempo' in selected[0] and str(selected[0]['tempo']) != duration: raise ValueError('duration_changed')
        slots = []
        for slot in selected[0]['horarios']:
            if not isinstance(slot, dict): raise ValueError('invalid_slot')
            stamp = str(slot.get('DataIni', ''))
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', stamp): raise ValueError('invalid_slot')
            local = dt.datetime.combine(date, dt.time.fromisoformat(stamp), ZoneInfo('America/Cuiaba'))
            if local > now.astimezone(ZoneInfo('America/Cuiaba')): slots.append(stamp[:5])
        return {'status':'ok', 'source':'appbarber', 'observed_at':observed, 'service_id':service['id'],
                'service_key':params['service_key'], 'date':raw_date, 'professional_key':params['professional_key'],
                'professional_id':pid, 'duration_minutes':int(duration), 'timezone':'America/Cuiaba',
                'slots':sorted(set(slots))[:8], 'reservation_performed':False}
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        return failure
