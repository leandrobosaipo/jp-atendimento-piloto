import datetime as dt
from public_requests import plan,PREFERENCE

cfg={'services':{'maintenance':{'name':'Manutenção'}},'professionals':{'pedro':{'name':'Pedro','human_only':False}},
     'catalog':{'service_art_prices':[{'name':'Manutenção','price_cents':12000}],'service_price_by_key':{'maintenance':12000}}}
today=dt.date(2026,11,16)
for text in ('Bom dia', 'Olá!', 'Oi', 'Boa tarde.'):
    assert 'agendar' in plan(text,cfg,None,today)
assert plan('Bom dia, quero falar com um humano',cfg,None,today) is None
def query(args):
    assert {k:v for k,v in args.items() if k!='period'}==dict(service_key='maintenance',professional_key='pedro',date='2026-11-17')
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
followup=plan('Tem horário amanhã à tarde? Manutenção de prótese',cfg,query,today,offer_options=True)
assert followup=='Para 17/11, tenho 13:30 com Pedro. Qual você prefere?'
assert plan('manutenção de prótese',cfg,query,today) is None
assert plan('Sim',cfg,query,today) == 'Você consegue me dizer a que você se refere? Assim eu confiro direitinho.'
assert plan('Pode',cfg,query,today) == 'Você consegue me dizer a que você se refere? Assim eu confiro direitinho.'

# Mídia não recebe interpretação: pede somente o contexto necessário.
assert plan('',cfg,query,today,media_kind='document') == 'Recebi o documento. Você consegue me dizer como posso ajudar com ele?'
assert plan('',cfg,query,today,media_kind='photo') == 'Recebi sua foto. Você consegue me dizer como posso ajudar?'
# A pergunta sobre o JP não promete serviço, vaga ou confirmação.
assert plan('O JP que vai colocar a nova prótese? Teria como marcar com ele?',cfg,query,today) == (
    'Entendi. Preciso verificar com a equipe se o JP pode atender esse caso. Qual dia ou período fica melhor para você?')
