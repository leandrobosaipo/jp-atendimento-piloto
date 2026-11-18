"""Bounded informational replies using the revision published by the operator."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
import unicodedata
import uuid

BASE = Path('/etc/atendimento-piloto-ai')
CACHE = Path('/var/lib/jp-published-answers')
SOCKET = '/run/jp-published-answers.sock'
QUESTIONS = {
    'maintenance': ('manutencao-e-cuidados', 'Como funciona a manutenção de prótese capilar? Resuma em uma frase curta.'),
    'models': ('modelos-de-protese', 'Quais modelos de prótese capilar a JP oferece? Resuma em uma frase curta.'),
}

def plain(text):
    return ' '.join(unicodedata.normalize('NFKD', text.casefold()).encode('ascii','ignore').decode().split())

def subject(text):
    value = plain(text).strip(' ?!.')
    value = re.sub(r'^(oi|ola|bom dia|boa tarde|boa noite)[!, .]+','',value)
    if re.fullmatch(r'(?:como (?:e|funciona|e feita)|o que e) (?:a |uma )?manutencao(?: (?:da|de) protese(?: capilar)?)?', value):
        return 'maintenance'
    if re.fullmatch(r'(?:quais|que) (?:sao os |os )?(?:tipos|modelos)(?: de protese(?: capilar)?)?(?: (?:voces (?:tem|oferecem|usam)|existem|a jp (?:tem|oferece)))?', value):
        return 'models'
    return None

def operator_questions(kb):
    """Each public FAQ has a single subject and operator-authored example questions."""
    topics={}
    for entry in kb['entries']:
        topic=entry['id']
        if not re.fullmatch(r'faq-publica-[a-z0-9-]{1,45}',topic):continue
        questions=[k.partition(':')[2].strip() for k in entry.get('keywords',[]) if k.startswith('pergunta:')]
        if not questions or not all(informational(q) for q in questions):continue
        topics[topic]=(topic,questions[0]+' Resuma em uma frase curta.',{plain(q).strip(' ?!.') for q in questions})
    return topics

def informational(q):
    q=plain(q).strip(' ?!.')
    return bool(len(q)<=90 and re.fullmatch(r'(?:como|onde|quais?|o que|que tipos?) [a-z ]+',q)
                and not re.search(r'\b(nao|preco|valor|custa|pago|pagamento|desconto|clube|dor|coceira|ferida|saude|pele|cola|remover|medicamento|agenda\w*|remarc\w*|amanha|hoje|vaga|horario|cancel\w*|atendente|humano|reclam\w*|cadastro|clientes|telefone|pix|divulgacao|parceria|confirm\w*)\b',q))

def resolve(text, conf):
    topic=subject(text)
    if topic or not informational(text) or not (conf/'published-answers.json').exists():return topic
    value=plain(text).strip(' ?!.')
    matches=[k for k,v in operator_questions(json.loads((BASE/'kb.json').read_bytes())).items() if value in v[2]]
    return matches[0] if len(matches)==1 else None

def revision():
    h = hashlib.sha256()
    for name in ('kb.json','operator-instructions.json'):
        h.update(hashlib.sha256((BASE/name).read_bytes()).digest())
    return h.hexdigest()

def gateway(payload):
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(65)
        peer.connect('/run/atendimento-piloto-ai.sock')
        peer.sendall(json.dumps(payload,ensure_ascii=False).encode()+b'\n')
        return json.loads(peer.makefile('rb').readline(16385))

def checked(result, topic, version):
    source=QUESTIONS[topic][0] if topic in QUESTIONS else topic
    if result.get('status')!='ok' or result.get('decision')!='answer' or result.get('kb_version')!=version:
        return None
    ids={r['id'] for r in result.get('sources',[]) if isinstance(r,dict) and 'id' in r}
    if source not in ids or not ids <= {source,'voz-e-identidade'}:
        return None
    text=re.sub(r'^Olá! Sou [^.!?\n]{1,170}assistente virtual[^.!?\n]{0,100}\.\s*','',result['reply']).strip()
    if not 15<=len(text)<=240 or re.search(r'\d|https?:|@|R\$|\b(garant\w*|reservad\w*|agendad\w*|promocao|diagnostico|tratamento|minoxidil|medicamento|simulacao|fichas?|laboratorio|piloto)\b',plain(text)):
        return None
    return text

def prepare(topic):
    rev=revision()
    path=CACHE/(rev+'-'+topic+'.json')
    if path.exists():return
    kb=json.loads((BASE/'kb.json').read_bytes())
    definition=QUESTIONS.get(topic) or operator_questions(kb).get(topic)
    if not definition:return
    source,question=definition[:2]
    key='published-answer-v1/'+rev+'/'+topic
    result=gateway(dict(op='ask',id=str(uuid.uuid5(uuid.NAMESPACE_URL,key)),session=str(uuid.uuid5(uuid.NAMESPACE_URL,key+'/session')),text=question))
    if revision()!=rev:return
    answer=checked(result,topic,kb['version'])
    item=dict(revision=rev,version=kb['version'],topic=topic,source=source,answer=answer,
              estimate_micro_usd=result.get('estimate_micro_usd'),tokens=result.get('tokens'),status='ready' if answer else 'held')
    fd,name=tempfile.mkstemp(dir=CACHE)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(item,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)

def observe(db, rev, topic, seq, execution):
    db.execute('CREATE TABLE IF NOT EXISTS kb_observation(revision TEXT,topic TEXT,first_seq INTEGER,execution TEXT,at INTEGER,PRIMARY KEY(revision,topic))')
    db.execute('INSERT OR IGNORE INTO kb_observation VALUES(?,?,?,?,?)',(rev,topic,seq,execution,int(time.time())))
    first=db.execute('SELECT first_seq FROM kb_observation WHERE revision=? AND topic=?',(rev,topic)).fetchone()[0]
    return seq>first

def contextual_question(text,seq,stamp,jid,aliases,db,ledger):
    """Resolve a short price follow-up only after our own delivered maintenance reply."""
    if plain(text).strip(' ?!.') not in {'e o valor','e o preco','e quanto custa','quanto custa','qual o valor'}:return text
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kb_proposals'").fetchone():return text
    rows=db.execute("SELECT seq,message_id,address_hashes FROM captured WHERE seq<? ORDER BY seq DESC LIMIT 60",(seq,))
    last=next(((n,mid) for n,mid,raw in rows if aliases.intersection(json.loads(raw))),None)
    if not last or not db.execute("SELECT 1 FROM kb_proposals WHERE seq=? AND topic='maintenance'",(last[0],)).fetchone():return text
    source=hashlib.sha256(json.dumps(['central-event-v1','reply',last[1]],separators=(',',':')).encode()).hexdigest()
    latest=ledger.execute('SELECT source,state,native,attempted FROM dispatch_attempts WHERE destination=? AND attempted BETWEEN ? AND ? ORDER BY attempted DESC LIMIT 2',(jid,int(stamp)-1800,int(stamp))).fetchall()
    if not latest or latest[0][0]!=source or latest[0][1] not in ('delivered','read') or (len(latest)>1 and latest[0][3]==latest[1][3]):return text
    if any(db.execute('SELECT 1 FROM team_events WHERE pn=? AND stamp>=? AND mid<>? LIMIT 1',(alias,latest[0][3],latest[0][2])).fetchone() for alias in aliases):return text
    return 'Qual o valor da manutenção?'

def candidate(text, seq, execution, db, conf):
    topic=resolve(text,conf)
    if not topic:return None
    policy=json.loads((conf/'published-answers.json').read_bytes())
    if seq<=policy['after'] or (topic not in policy['topics'] and not topic.startswith('faq-publica-')):return None
    rev=revision()
    path=CACHE/(rev+'-'+topic+'.json')
    if not path.exists():
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(0.15);peer.connect(SOCKET);peer.sendall(topic.encode()+b'\n')
        return None
    row=json.loads(path.read_bytes())
    if row['status']!='ready' or row['revision']!=rev or row['topic']!=topic:return None
    if not observe(db,rev,topic,seq,execution):return None
    return row

if __name__=='__main__':
    # The existing n8n capture wakes this socket. The compiler has no send route.
    import select
    if os.getenv('LISTEN_PID')!=str(os.getpid()) or os.getenv('LISTEN_FDS')!='1':raise SystemExit(1)
    listener=socket.socket(fileno=3)
    while select.select([listener],[],[],8)[0]:
        peer,_=listener.accept()
        with peer:
            peer.settimeout(1)
            try:
                topic=peer.makefile('rb').readline(64).decode().strip()
                if topic in QUESTIONS or re.fullmatch(r'faq-publica-[a-z0-9-]{1,45}',topic):prepare(topic)
            except (OSError,ValueError,KeyError) as exc:
                print('knowledge held',type(exc).__name__,flush=True)
