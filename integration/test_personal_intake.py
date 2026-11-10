import sqlite3
import tempfile
import unittest
from pathlib import Path
from service import App, Stop, personal_ingress

class Fixture:
    cfg = {"actors":{"personal-test":{}}}
    def db(self): return sqlite3.connect(self.path)
    def submit(self,msg):return msg

class IntakeTest(unittest.TestCase):
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
