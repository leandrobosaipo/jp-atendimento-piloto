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
    source, _ = QUESTIONS[topic]
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
    source,question=QUESTIONS[topic]
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

def candidate(text, seq, execution, db, conf):
    topic=subject(text)
    if not topic:return None
    policy=json.loads((conf/'published-answers.json').read_bytes())
    if seq<=policy['after'] or topic not in policy['topics']:return None
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
                topic=peer.makefile('rb').readline(32).decode().strip()
                if topic in QUESTIONS:prepare(topic)
            except (OSError,ValueError,KeyError) as exc:
                print('knowledge held',type(exc).__name__,flush=True)
