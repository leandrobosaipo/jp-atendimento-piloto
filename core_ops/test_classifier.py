import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assistant_panel'))
from gateway import Gateway
from test_gateway import KB
from classifier import classify,Held

class IntentTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.g=Gateway(Path(tmp.name)/'state',KB,'fake');self.addCleanup(self.g.db.close)
        self.msg={'task':'fixture-001','message':'Podemos conversar amanhã?'}
        self.count=0
    def upstream(self,payload,key):
        self.count+=1
        self.assertEqual(json.loads(payload['input']),{'message':self.msg['message']})
        self.assertFalse(payload['store']);self.assertNotIn('tools',payload)
        out={'intent':'clarification','confidence':0.75,'evidence':'conversar','reason':'Pedido aberto.'}
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(out)}]}],'usage':{'input_tokens':400,'output_tokens':30}}
    def test_replays_share_budget_and_no_request_after_unknown(self):
        result=classify(self.g.db,self.msg,'fake',self.upstream)
        self.assertEqual(result['output']['evidence'],'conversar')
        self.assertTrue(classify(self.g.db,self.msg,'fake',self.upstream)['replayed']);self.assertEqual(self.count,1)
        with self.assertRaises(Held):classify(self.g.db,{**self.msg,'message':'changed'},'fake',self.upstream)
        other={**self.msg,'task':'fixture-002'}
        with self.assertRaises(Held):classify(self.g.db,other,'fake',lambda *_:(_ for _ in ()).throw(TimeoutError()))
        with self.assertRaises(Held):classify(self.g.db,other,'fake',self.upstream)
        self.assertEqual(self.count,1)
    def test_identity_stays_private(self):
        with self.assertRaises(Held):classify(self.g.db,{**self.msg,'message':'user@example.invalid'},'fake',self.upstream)
        self.assertEqual(self.count,0)
if __name__=='__main__':unittest.main()
