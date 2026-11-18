"""Read-only status for the authenticated knowledge editor. No contact data in output."""
import hashlib
import json
from pathlib import Path
import sqlite3
from published_answers import BASE, CACHE, QUESTIONS, operator_questions, revision

def readonly(path):
    return sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=2)

def status():
    rev=revision()
    kb=json.loads((BASE/'kb.json').read_bytes())
    topics={**QUESTIONS,**operator_questions(kb)}
    titles={entry['id']:entry['title'] for entry in kb['entries']}
    result=[]
    with readonly('/var/lib/jp-reply-capture/replies.sqlite3') as capture, readonly('/var/lib/jp-dispatch/ledger.sqlite3') as ledger:
        tables={row[0] for row in capture.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for topic,info in topics.items():
            path=CACHE/(rev+'-'+topic+'.json')
            item=json.loads(path.read_bytes()) if path.is_file() else {}
            observed='kb_observation' in tables and capture.execute('SELECT 1 FROM kb_observation WHERE revision=? AND topic=?',(rev,topic)).fetchone() is not None
            state='Aguardando primeira pergunta' if not observed else 'Observação registrada · próximas perguntas elegíveis'
            if item.get('status')=='held':state='Retida para conferir a ficha e a resposta'
            if not item:state='Aguardando preparo para esta revisão'
            counts={}
            if 'kb_proposals' in tables:
                sql='SELECT c.message_id FROM kb_proposals p JOIN captured c USING(seq) WHERE p.revision=? AND p.topic=?'
                for mid, in capture.execute(sql,(rev,topic)):
                    source=hashlib.sha256(json.dumps(['central-event-v1','reply',mid],separators=(',',':')).encode()).hexdigest()
                    rows=ledger.execute('SELECT state FROM dispatch_attempts WHERE source=?',(source,)).fetchall()
                    label={ 'accepted':'Aceita pelo provedor', 'read':'Lida', 'delivered':'Entregue' }.get(rows[0][0],'Conferir tentativa') if len(rows)==1 else ('Sem tentativa localizada' if not rows else 'Conferir tentativas')
                    counts[label]=counts.get(label,0)+1
            result.append(dict(title=titles.get(info[0],topic),state=state,counts=counts))
    return dict(revision=rev[:12],topics=result)

if __name__=='__main__':
    print(json.dumps(status(),ensure_ascii=False))
