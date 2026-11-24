"""Review an unanswered service or date turn against the question actually sent."""
import datetime as dt
import hashlib
import json
import re
import unicodedata
from zoneinfo import ZoneInfo


SERVICE_PROMPT = 'Qual serviço você gostaria de agendar?'
DAY_PROMPT = 'Qual dia ou período você prefere para esse serviço?'
CUIABA = ZoneInfo('America/Cuiaba')
SERVICE = {'corte':'corte de cabelo', 'cortar':'corte de cabelo', 'barba':'barba',
           'manutencao':'manutenção de prótese'}
FILLER = set('eu gostaria quero queria preciso de do da o a um uma meu minha fazer agendar marcar servico '
             'por favor cabelo cabelos masculino masculina capilar protese mesmo isso so seria para mim'.split())


def key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def words(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold())
                   if not unicodedata.combining(c)).strip()


def near(a,b):
    if a==b:return True
    if abs(len(a)-len(b))>1:return False
    if len(a)!=len(b):
        longer,shorter=sorted((a,b),key=len,reverse=True)
        return any(longer[:i]+longer[i+1:]==shorter for i in range(len(longer)))
    differences=[i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
    if len(differences)==1:return True
    return (len(differences)==2 and differences[1]==differences[0]+1
            and a[differences[0]]==b[differences[1]] and a[differences[1]]==b[differences[0]])


def service_answer(text):
    plain=words(text)
    if not re.fullmatch(r'[a-z ,.!]{2,100}',plain):return None
    tokens=re.findall(r'[a-z]+',plain)
    if not 1<=len(tokens)<=12:return None
    labels=set()
    for token in tokens:
        if token in FILLER:continue
        if token in SERVICE:
            labels.add(SERVICE[token]);continue
        # A near spelling needs a second clue. A name or unrelated word on its own is insufficient.
        clues={'corte':'cabelo','cortar':'cabelo','manutencao':'protese'}
        choices={SERVICE[s] for s,clue in clues.items() if clue in tokens and near(token,s)}
        if len(choices)!=1:return None
        labels.update(choices)
    return labels.pop() if len(labels)==1 else None


def date_answer(text):
    v=' '.join(words(text).strip(' .!').split())
    day=r'(?:hoje|amanha|depois de amanha|(?:segunda|terca|quarta|quinta|sexta)(?:[- ]feira)?|sabado|domingo)'
    period=r'(?: de manha| pela manha| a tarde| de tarde)?'
    match=re.fullmatch(r'(?:pode ser |prefiro |quero )?(?:na |no )?('+day+')('+period+')',v)
    if not match:return None
    d,p=match.groups()
    return d+({' pela manha':' de manha',' de tarde':' a tarde'}.get(p,p))


def review_turn(text,seq,stamp,jid,aliases,db,ledger,read,scope):
    if seq<=scope['after']:
        if seq!=scope.get('test_sequence'):return None
        record=db.execute('SELECT record_id FROM captured WHERE seq=?',(seq,)).fetchone()
        if not record or record[0]!=scope.get('test_record'):return None
    if not re.fullmatch(r'55[1-9]\d{9,10}@s\.whatsapp\.net',jid):return None
    # Only short answers to a known question are eligible for reinterpretation.
    choice,day=service_answer(text),date_answer(text)
    if not choice and not day:return None
    prior=next((r for r in db.execute('SELECT message_id,record_id,envelope_pos,item_pos,address_hashes '
        'FROM captured WHERE seq<? ORDER BY seq DESC LIMIT 64',(seq,)) if aliases.intersection(json.loads(r[4]))),None)
    if not prior:return None
    mid,rid,ei,mi,_=prior
    delivery=ledger.execute('SELECT a.source,a.native,a.state,c.content_hash,a.attempted FROM dispatch_attempts a '
        'JOIN dispatch_content c USING(attempt) WHERE a.destination=? AND a.attempted BETWEEN ? AND ? '
        'ORDER BY a.attempted DESC,a.rowid DESC LIMIT 1',(jid,int(stamp)-1800,int(stamp))).fetchone()
    if not delivery or delivery[0]!=key(['central-event-v1','reply',mid]) or not delivery[1] or delivery[2] not in ('delivered','read'):return None
    if any(db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=? AND mid<>? LIMIT 1',
                      (alias,delivery[4],delivery[1])).fetchone() for alias in aliases):
        # The caller also checks interventions after the incoming message.
        return None
    raw=read(rid)[1];env=(raw if isinstance(raw,list) else [raw])[ei]
    batch=env['data'];msg=(batch if isinstance(batch,list) else [batch])[mi];k=msg.get('key') or {}
    if k.get('remoteJid')!=jid or k.get('fromMe') is not False or k.get('id')!=mid:return None
    payload=msg.get('message') or {}
    previous=payload.get('conversation') or (payload.get('extendedTextMessage') or {}).get('text')
    if not isinstance(previous,str) or not 1<=len(previous)<=300:return None
    previous_stamp=msg.get('messageTimestamp')
    if not str(previous_stamp).isdigit() or dt.datetime.fromtimestamp(int(previous_stamp),CUIABA).date()!=dt.datetime.fromtimestamp(stamp,CUIABA).date():return None
    if choice and delivery[3]==key(['text',SERVICE_PROMPT]):
        return previous+' '+choice
    if day and delivery[3]==key(['text',DAY_PROMPT]):
        service=service_answer(previous)
        if service:return 'Quero agendar '+service+' '+day
    return None
