import hashlib,json,sqlite3,unittest
from fixed_return_status import key,read_results

class Outcomes(unittest.TestCase):
    def test_physical_receipt_and_matching_quoted_response(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('create table dispatch_attempts(attempt,source,destination,state,native,attempted);'
                         'create table captured(seq,quoted_id,address_hashes,received_at);')
        k='test';attempt='original';destination='5511999990000@s.whatsapp.net'
        state={'fixed_return_dispatches':[dict(request_id=k,attempt_id=attempt,attempted_at='test-time',
                                               execution='1',message_id='native-id')]}
        read=lambda:read_results([dict(client='1',approval_key=k)],{k:'matched'},state,db,db,[])[0]
        self.assertEqual(read()['status'],'delivery_unknown')
        db.execute('insert into dispatch_attempts values(?,?,?,?,?,?)',
            (key('central-attempt-v1','fixed-return',attempt,0),key('central-event-v1','fixed-return',k),
             destination,'accepted','native-id',100))
        self.assertFalse(read()['delivered']);db.execute("update dispatch_attempts set state='read'")
        self.assertTrue(read()['delivered']);self.assertTrue(read()['read']);self.assertIsNone(read()['response'])
        db.execute('insert into captured values(?,?,?,?)',(2,'native-id',json.dumps([hashlib.sha256(destination.encode()).hexdigest()]),'2026-11-23T12:00:00Z'))
        self.assertEqual(read()['response']['kind'],'reply_to_invitation')
