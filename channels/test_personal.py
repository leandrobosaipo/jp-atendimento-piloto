import copy
import tempfile
import unittest
from pathlib import Path
from personal import Held, Personal, TEXT, run

PHONE = '+5511900000000'
ID = 'FAKE-NATIVE-ID'


class PersonalTest(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.TemporaryDirectory()
        self.addCleanup(self.path.cleanup)
        self.db = Path(self.path.name).resolve() / 'test.db'
        self.calls = []
        self.t = Personal(PHONE, self.exchange)
        self.key = {'id': ID, 'remoteJid': self.t.jid, 'fromMe': True}
        self.row = {'key': self.key, 'message': {'conversation': TEXT}, 'status': 'PENDING'}

    def exchange(self, route, body):
        if route == 'chat/whatsappNumbers':
            return [{'number': PHONE[1:], 'exists': True, 'jid': self.t.jid}]
        if route == 'message/sendText':
            self.calls.append(body)
            return {'key': self.key}
        self.assertEqual(route, 'chat/findMessages')
        self.assertEqual(body['where']['key']['id'], ID)
        return {'messages': {'records': [self.row]}}

    def test_boundaries(self):
        for field, value in [('sender', '5511000000000@s.whatsapp.net'), ('sender', '12345@g.us'),
                             ('account', 'other'), ('native_id', 'other')]:
            event = {**vars(self.t.event()), field: value}
            with self.assertRaises(Held):
                self.t.send(event, {'kind': 'text', 'text': TEXT})
        self.assertEqual(self.calls, [])

    def test_reconciliation_after_restart(self):
        self.assertEqual(run('send', self.t, self.db)['id'], ID)
        for field, value in [('status','PENDING'), ('id','WRONG-ID'), ('remoteJid','other'), ('text','other')]:
            row = copy.deepcopy(self.row)
            self.row['status'] = 'READ'
            if field == 'status': self.row[field] = value
            elif field == 'text': self.row['message']['conversation'] = value
            else: self.row['key'][field] = value
            with self.assertRaises(Held):
                run('receipt', self.t, self.db)
            self.row = row
        with self.assertRaises(Held):
            run('send', self.t, self.db)
        self.row['status'] = 'DELIVERY_ACK'
        self.assertEqual(run('receipt', self.t, self.db), {'state':'delivered','id':ID})
        run('send', self.t, self.db)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_never_repeats(self):
        def lost(route, body):
            result = self.exchange(route, body)
            if route == 'message/sendText': raise TimeoutError()
            return result
        self.t.native = lost
        for _ in range(2):
            with self.assertRaises(Held): run('send', self.t, self.db)
        self.assertEqual(run('receipt', self.t, self.db)['state'], 'sending')
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
