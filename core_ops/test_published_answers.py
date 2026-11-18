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
