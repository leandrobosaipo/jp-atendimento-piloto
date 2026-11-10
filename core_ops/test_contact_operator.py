import hashlib,unittest
from contact_operator import identity

class Operator(unittest.TestCase):
    def test_confirmed_identity_and_inbox(self):
        addr='fixture@example.invalid';cfg=dict(uid=17,account=21,inbox=23,email_sha256=hashlib.sha256(addr.encode()).hexdigest())
        p=dict(id=17,email=addr,confirmed=True,accounts=[dict(id=21,role='agent',status='active')]);inboxes=[dict(id=23)]
        def get(route,token):
            if token!='fixture-token-123':raise ValueError
            self.assertIn(route,['profile','accounts/21/inboxes']);return p if route=='profile' else {'payload':inboxes}
        self.assertEqual(identity(cfg,'fixture-token-123',get),'chatwoot/21/17')
        for field,value in [('confirmed',False),('email','someone@example.invalid'),('id',10),('accounts',[])]:
            old=p[field];p[field]=value
            with self.assertRaises(ValueError):identity(cfg,'fixture-token-123',get)
            p[field]=old
        inboxes.clear()
        with self.assertRaises(ValueError):identity(cfg,'fixture-token-123',get)
if __name__=='__main__':unittest.main()
