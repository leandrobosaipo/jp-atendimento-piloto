import datetime as dt
from public_requests import plan,PREFERENCE

cfg={'services':{'maintenance':{'name':'Manutenção'}},'professionals':{'pedro':{'name':'Pedro','human_only':False}},
     'catalog':{'service_art_prices':[{'name':'Manutenção','price_cents':12000}],'service_price_by_key':{'maintenance':12000}}}
today=dt.date(2026,11,16)
def query(args):
    assert args==dict(service_key='maintenance',professional_key='pedro',date='2026-11-17')
    return dict(status='ok',date=args['date'],slots=['09:00','13:30'])

assert '120,00' in plan('Qual o preço da manutenção?',cfg,query,today)
assert 'Qual serviço' in plan('Quero marcar um horário',cfg,query,today)
assert '09:00' in plan('Quero agendar manutenção com Pedro amanhã de manhã',cfg,query,today)
assert '13:30' not in plan('Quero agendar manutenção com Pedro amanhã de manhã',cfg,query,today)
assert 'manutenção' in plan('Quais serviços vocês fazem?',cfg,query,today)
assert plan('Quero cancelar o horário',cfg,query,today) is None
assert plan('👍',cfg,query,today) is None
compound=plan('Qual o preço da manutenção e tem vaga com Pedro amanhã?',cfg,query,today)
assert '120,00' in compound and '09:00' in compound
