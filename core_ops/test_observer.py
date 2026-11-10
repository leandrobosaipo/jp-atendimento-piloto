import io,json,unittest
from observer import probe
class Reply(io.BytesIO):status=200
class Read(unittest.TestCase):
    def test_instance_filter_and_output_minimization(self):
        cfg=dict(origin='https://evolution.codigo5.com.br',instance='example',token='fake',client_id='fake',client_secret='fake')
        def read(req,timeout):
            self.assertEqual(req.method,'GET');self.assertTrue(req.full_url.endswith('/instance/fetchInstances?instanceName=example'))
            return Reply(json.dumps([{'name':'example','ownerJid':'1234567890@s.whatsapp.net','integration':'WHATSAPP-BAILEYS','token':'unused'}]).encode())
        self.assertEqual(set(probe('identity',cfg,read)[0]),{'name','ownerJid','integration'})
        with self.assertRaises(ValueError):probe('identity',cfg,lambda *a,**k:Reply(b'[{},{}]'))
        with self.assertRaises(ValueError):probe('send',cfg,read)
if __name__=='__main__':unittest.main()
