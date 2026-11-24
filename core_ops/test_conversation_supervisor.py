import json
import sqlite3
import unittest

from conversation_supervisor import DAY_PROMPT, SERVICE_PROMPT, date_answer, key, review_turn, service_answer


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.store=sqlite3.connect(':memory:')
        self.addCleanup(self.store.close)
        self.store.executescript('CREATE TABLE captured(seq,message_id,record_id,envelope_pos,item_pos,address_hashes);'
            'CREATE TABLE team_events(pn,stamp,mid);CREATE TABLE dispatch_attempts(attempt,source,native,state,destination,attempted);'
            'CREATE TABLE dispatch_content(attempt,content_hash);')
        self.jid='5565999990000@s.whatsapp.net'
        self.stamp=1800000000
        self.store.execute('INSERT INTO captured VALUES(1,?,?,?,?,?)',('mid','entry',0,0,json.dumps(['alias'])))
        self.store.execute('INSERT INTO dispatch_attempts VALUES(?,?,?,?,?,?)',
            ('attempt',key(['central-event-v1','reply','mid']),'answer','delivered',self.jid,self.stamp-30))
        self.store.execute('INSERT INTO dispatch_content VALUES(?,?)',('attempt',key(['text',SERVICE_PROMPT])))
        self.prior='Tem horário amanhã?'
    def read(self,rid):
        self.assertEqual(rid,'entry')
        return {},{'data':{'key':{'id':'mid','remoteJid':self.jid,'fromMe':False},
                           'messageTimestamp':self.stamp-60,'message':{'conversation':self.prior}}}
    def review(self,text):
        return review_turn(text,2,self.stamp,self.jid,{'alias'},self.store,self.store,self.read,{'after':1})
    def test_typo_and_natural_phrasing_keep_previous_request(self):
        for txt in ['Queria cortar o cabelo','Cotre de cabelo masculino','Corts de cabelo masculino','barba','manutenção de prótese']:
            self.assertTrue(self.review(txt).startswith(self.prior+' '))
    def test_refusal_other_subjects_and_ambiguous_services(self):
        for txt in ['não quero corte','corte e barba','tinta de cabelo','❌ corte','tô com dor','quero falar com o salão','cancelar','cort','não amanhã']:
            self.assertIsNone(self.review(txt))
    def test_next_day_uses_previous_service(self):
        self.prior='Cotre de cabelo masculino'
        self.store.execute('UPDATE dispatch_content SET content_hash=?',(key(['text',DAY_PROMPT]),))
        self.assertEqual(self.review('pode ser amanhã pela manhã'),'Quero agendar corte de cabelo amanha de manha')
        self.assertIsNone(self.review('amanhã às 15 com Pedro'))
    def test_uncertain_send_other_question_and_team_ownership(self):
        self.store.execute("UPDATE dispatch_attempts SET state='accepted'")
        self.assertIsNone(self.review('Cotre de cabelo'))
        self.store.execute("UPDATE dispatch_attempts SET state='read'")
        self.store.execute("INSERT INTO team_events VALUES('alias',?,'human')",(self.stamp-8,))
        self.assertIsNone(self.review('Cotre de cabelo'))
        self.store.execute('DELETE FROM team_events')
        self.store.execute('UPDATE dispatch_content SET content_hash=?',(key(['text','Outra pergunta']),))
        self.assertIsNone(self.review('Cotre de cabelo'))
    def test_scope_and_unrelated_prior(self):
        self.assertIsNone(review_turn('corte',2,self.stamp,self.jid,{'alias'},self.store,self.store,self.read,{'after':2}))
        self.store.execute("INSERT INTO captured VALUES(2,'new','current',0,0,'[]')")
        for expected,record in [(False,'wrong'),(True,'current')]:
            scope={'after':2,'test_sequence':2,'test_record':record}
            self.assertEqual(expected,bool(review_turn('corte',2,self.stamp,self.jid,{'alias'},self.store,self.store,self.read,scope)))
        self.store.execute("UPDATE dispatch_attempts SET source='another'")
        self.assertIsNone(self.review('corte'))

    def test_binding_preserves_existing_gates(self):
        from bind_conversation_supervisor import bind, ANCHOR
        source='before\n'+ANCHOR+'suffix\nafter'
        bound=bind(source)
        self.assertTrue(bound.startswith('before\n'))
        self.assertTrue(bound.endswith(ANCHOR+'suffix\nafter'))
        with self.assertRaises(ValueError):bind(bound)


if __name__=='__main__':unittest.main()
