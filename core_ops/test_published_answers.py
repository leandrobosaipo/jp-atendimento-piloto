import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json
import published_answers as kb

class Coverage(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(kb.subject('Oi, como funciona a manutenção?'),'maintenance')
        self.assertEqual(kb.subject('Quais modelos de prótese vocês têm?'),'models')
        for value in ('Quais modelos vocês têm e qual o preço?', 'Tenho dor, como funciona a manutenção?', 'Confirmo','Pode ser amanhã?', 'Não me procurem','Quero falar com a equipe'):
            self.assertIsNone(kb.subject(value))

    def test_operator_questions(self):
        entry=dict(id='faq-publica-recepcao',keywords=['pergunta: Onde fica a recepção?','pergunta: Onde encontro a recepção?'])
        self.assertIn('onde fica a recepcao',kb.operator_questions({'entries':[entry]})[entry['id']][2])
        entry['keywords'].append('pergunta: Qual o valor do clube?')
        self.assertFalse(kb.operator_questions({'entries':[entry]}))

    def test_context_requires_delivered_same_customer_without_human(self):
        db=sqlite3.connect(':memory:')
        db.executescript("""CREATE TABLE captured(seq,message_id,address_hashes);
        CREATE TABLE kb_proposals(seq,topic);
        CREATE TABLE dispatch_attempts(source,state,native,attempted,destination);
        CREATE TABLE team_events(pn,stamp,mid);
        INSERT INTO captured VALUES(1,'request','["person"]');
        INSERT INTO kb_proposals VALUES(1,'maintenance');""")
        source=kb.hashlib.sha256(b'["central-event-v1","reply","request"]').hexdigest()
        db.execute("INSERT INTO dispatch_attempts VALUES(?,'accepted','notice',100,'one')",(source,))
        def lookup(person='person'):
            return kb.contextual_question('E quanto custa?',2,140,'one',{person},db,db)
        self.assertEqual(lookup(),'E quanto custa?')
        db.execute("UPDATE dispatch_attempts SET state='delivered'")
        self.assertIn('manutenção',lookup())
        self.assertEqual(lookup('another'),'E quanto custa?')
        db.execute("INSERT INTO team_events VALUES('person',102,'human')")
        self.assertEqual(lookup(),'E quanto custa?')

    def test_publication_and_no_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            for name,body in [('kb.json',{'version':'fixture'}),('operator-instructions.json',{}),('published-answers.json',{'after':8,'topics':['models']})]:
                (base/name).write_text(json.dumps(body))
            response=dict(status='ok',decision='answer',kb_version='fixture',reply='Existem diferentes modelos de prótese.',sources=[{'id':'modelos-de-protese'}])
            db=sqlite3.connect(':memory:')
            with patch.object(kb,'BASE',base),patch.object(kb,'CACHE',base),patch.object(kb,'gateway',return_value=response) as gateway:
                kb.prepare('models');kb.prepare('models')
                gateway.assert_called_once()
                lookup=lambda seq:kb.candidate('Quais modelos de prótese vocês têm?',seq,'1',db,base)
                self.assertIsNone(lookup(8))
                self.assertIsNone(lookup(9))
                self.assertEqual(lookup(10)['answer'],response['reply'])
                self.assertIsNone(lookup(9))
                (base/'operator-instructions.json').write_text('{"updated":true}')
                kb.prepare('models')
                self.assertIsNone(lookup(11))
                self.assertEqual(gateway.call_count,2)
            for change in ({'decision':'handoff'},{'sources':[]},{'kb_version':'old'},{'reply':'O serviço custa R$ 100'}):
                self.assertIsNone(kb.checked({**response,**change},'models','fixture'))
