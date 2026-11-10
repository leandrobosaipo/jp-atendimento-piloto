import sqlite3
import hashlib
import json
import time
import uuid
import tempfile
import unittest
from pathlib import Path
from service import App, Stop, personal_ingress

class Fixture:
    cfg = {"actors":{"personal-test":{}}}
    def db(self): return sqlite3.connect(self.path)
    def submit(self,msg):return msg

class IntakeTest(unittest.TestCase):
    def test_native_bindings_cutoff_and_conflicting_retry(self):
        now=int(time.time());epoch=str(uuid.uuid4());instance=b"fixture"
        left="10000001@lid";right="10000002@lid";alias="123456789@s.whatsapp.net"
        def identity(jid):return hashlib.sha256(instance+b"\x00"+jid.encode()).hexdigest()
        cfg={"actors":{"A":{"contact":2,"conversation":3},"B":{"contact":4,"conversation":5}},
             "account":1,"inbox":9,"channel_ingress":{"instance":"fixture","epoch":epoch,"workflow":"intake",
             "start":now-5,"until":now+60,"bindings":{identity(left):"A",identity(alias):"A",identity(right):"B"}}}
        with tempfile.TemporaryDirectory() as d:
            app=App(cfg,{},{},Path(d)/"db",d);requests={}
            def submit(msg):
                requests.setdefault(msg["request"],msg)
                return {"status":"ok"} if msg["actor"]=="A" else {"state":"human","incoming":6}
            app.submit=submit
            def deliver(sender=left,other=alias,stamp=now,native="native001",value="Tabela",owner=epoch,extra=None):
                body={"instance":"fixture","event":"messages.upsert","data":{"messageTimestamp":stamp,"key":{"fromMe":False,"id":native,"remoteJid":sender,"remoteJidAlt":other},"message":{"conversation":value}}}
                if extra:body.update(extra)
                raw=json.dumps(body).encode();rid=hashlib.sha256(len(instance).to_bytes(4,"big")+instance+raw).hexdigest()
                return app.receive_channel(raw,rid,"intake","123",owner)["status"]
            self.assertEqual(deliver(),"processed")
            self.assertEqual(deliver(sender=alias,other=left,extra={"irrelevant":1}),"processed")
            self.assertEqual(len(requests),1)
            for values in (dict(other=right),dict(sender=right,other=None),dict(value="different"),dict(stamp=now-6),
                           dict(sender="1234567@g.us"),dict(sender="80000001@lid"),dict(owner=str(uuid.uuid4()))):
                with self.assertRaises(Stop):deliver(**values)
            self.assertEqual(deliver(sender=right,other=None,native="native002"),"retained")
    def test_original_native_id_is_the_only_eligible_request(self):
        app=Fixture()
        with tempfile.TemporaryDirectory() as directory:
            app.path=Path(directory)/"test.db"
            msg=dict(op="personal-ingress",record_id="a"*64,native_id_sha256="b"*64,text="Marcelinho, quero ver a tabela de preços do piloto")
            for invalid,uid in ((msg,1000),({**msg,'text':'outra pergunta'},0),({**msg,'native_id_sha256':'?'},0)):
                with self.assertRaises(Stop):personal_ingress(app,invalid,uid)
            self.assertFalse(app.path.exists())
            first=personal_ingress(app,msg,0)
            self.assertEqual(first,personal_ingress(app,{**msg,"record_id":"c"*64},0))
            self.assertEqual(first['actor'],'personal-test')
            with self.assertRaises(Stop):personal_ingress(app,{**msg,"native_id_sha256":"d"*64},0)
            with self.assertRaises(Stop):App.ui(app,{"op":"ask","actor":"personal-test"})

if __name__=='__main__':unittest.main()
