import sqlite3
import unittest
from attendance_evidence import scan, summarize


class Evidence(unittest.TestCase):
    def test_repeated_read_keeps_one_direct_message_and_excludes_groups(self):
        db=sqlite3.connect(':memory:')
        db.executescript("create table receipts(seq integer,id);insert into receipts values(1,'fake');")
        def msg(jid):
            return {'key':{'remoteJid':jid,'remoteJidAlt':'123456@lid','id':jid,'fromMe':False}}
        envelope={'instance':'fixture','event':'messages.upsert','data':[msg('123456@lid'),msg('123456@g.us')]}
        index={'last':0,'anchor':None,'incoming':{},'quotes':{},'connections':[]}
        read=lambda _:({'receivedAt':'2026-11-23T12:00:00Z'},[envelope,{'instance':'fixture','event':'connection.update','data':{'state':'open'}}])
        for _ in range(2):scan(db,index,read,'fixture')
        self.assertEqual(list(index['incoming']),['123456@lid'])
        self.assertEqual(len(index['connections']),1)

    def test_shared_ticket_and_unaccepted_send_stay_pending(self):
        db=sqlite3.connect(':memory:')
        db.executescript("""create table captured(message_id,kind,received_at);
            create table core_links(capture,status);create table triage_cases(capture,conversation);
            create table dispatch_attempts(native,destination,state);
            insert into captured values('one','text','a'),('two','text','b');
            insert into triage_cases values(1,8),(2,8);
            insert into dispatch_attempts values('reply','5511000000000@s.whatsapp.net','delivered');""")
        entry=dict(name='Cliente fictício',at='2026-11-23T10:00:00-04:00',addr=['verified'])
        index=dict(incoming={'one':dict(entry,seq=9),'two':dict(entry,seq=10)},quotes={})
        state={'jp_reply_attempts':[dict(source_message_id='one',message_id='reply',destination_e164='+5511000000000',attempted_at='2026-11-23T10:01:00-04:00')]}
        rows=summarize(index,db,db,state,{8:('Equipe fictícia',0)},{})
        self.assertEqual([r['reply'] for r in rows],['Resposta entregue',None])
        self.assertEqual(rows[1]['status'],'Com a equipe')
        state['jp_reply_attempts'][0]['destination_e164']='+5511000000001'
        self.assertEqual(summarize(index,db,db,state,{},{} )[0]['reply'],'Envio incerto')
