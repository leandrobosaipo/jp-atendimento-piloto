import json
import pathlib
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from service import App, Stop, message_parts
from policy import prepare

class Parts(unittest.TestCase):
    def test_conversation_hold_survives_restart_and_release_covers_future_only(self):
        cfg={"account":1,"inbox":7,"agent_token":"fixture","actors":{"A":{"conversation":2,"contact":12},"B":{"conversation":3,"contact":13}}}
        def native(port,path,**kw):
            conv=int(path.rsplit("/",1)[1])
            return {"account_id":1,"inbox_id":7,"id":conv,"meta":{"sender":{"id":conv+10}},
                    "labels":labels[conv],"status":"open"}
        labels={2:["jp-atendimento-humano"],3:[]}
        with tempfile.TemporaryDirectory() as directory,patch("service.local_request",native):
            new=lambda:App(cfg,{},{},pathlib.Path(directory)/"db",directory)
            app=new()
            def event(actor):
                rid=str(uuid.uuid4())
                with app.db() as db:
                    db.execute("INSERT INTO events(req,actor,question,options,state) VALUES(?,?,'text','{}','incoming')",(rid,actor))
                return app.row(rid)
            first=event("A")
            self.assertTrue(app.hold_event(first))
            self.assertFalse(app.hold_event(event("B")))
            command={"op":"human-release","actor":"A","contact":12,"conversation":2,"episode":app.ownership("A")["episode"]}
            with self.assertRaisesRegex(Stop,"human_still_assigned"):app.operator_ownership(command,0)
            labels[2]=[];app=new()
            self.assertTrue(app.hold_event(event("A")))
            for changes,uid in [({},1000),({"conversation":3},0),({"actor":"B","conversation":3,"contact":13},0),({"episode":str(uuid.uuid4())},0)]:
                with self.assertRaises(Stop):app.operator_ownership({**command,**changes},uid)
            self.assertEqual(app.operator_ownership(command,0)["pending_replayed"],0)
            self.assertFalse(app.hold_event(event("A")))
            self.assertEqual(app.row(first["req"])["state"],"human")
            self.assertIsNotNone(app.operator_ownership({**command,"op":"human-take"},0))
            with self.assertRaisesRegex(Stop,"wrong_ownership_episode"):app.operator_ownership(command,0)
            with patch("service.local_request",return_value={"id":3,"account_id":1,"inbox_id":7,"meta":{"sender":{"id":12}}}):
                with self.assertRaisesRegex(Stop,"conversation_identity_mismatch"):app.native_owner("A")
    def test_split_bounds_and_semantics(self):
        text=("Palavras que continuam na mesma frase "*12)+"*R$ 35,00* às 17:30 https://example.org/a.b"
        parts=message_parts(text,"servicos-02",False)
        self.assertTrue(all(len(t)<=250 for t,a in parts))
        self.assertEqual(" ".join(t for t,a in parts[:-1]),text)
        self.assertEqual(parts[-1][1],"servicos-02")
        self.assertTrue(any("*R$ 35,00*" in t for t,a in parts))
        self.assertEqual(message_parts("Tudo certo.",None,False),[("Tudo certo.",None)])
        self.assertEqual(message_parts("Valor: *R$ 30,00*.",None,False),[("Valor: *R$ 30,00*.",None)])
        for value in ("*"+"a"*300+"*","abc "*900):
            with self.assertRaises(Stop):message_parts(value,None,False)
    def test_catalog_without_provider(self):
        catalog={"price_source_policy":"artwork","service_art_prices":[{"name":"Coloração Barba","price_cents":1111},{"name":"Coloração Prótese","price_cents":2222},{"name":"Reajuste da Prótese (a partir de)","price_cents":3333}],"service_price_by_key":{"adjustment":3333}}
        cfg={"services":{"adjustment":{},"coloring":{},"beard":{}}}
        opts={"intent":"auto","service":"","professional":"","date":""}
        for question,expected in [("Preço da coloração da barba?","11,11"),("Quanto custa a coloração da prótese?","22,22"),("Quanto custa o reajuste?","a partir de *R$ 33,33*")]:
            facts,text,route,group=prepare(question,opts,cfg,catalog,None)
            self.assertIn(expected,text);self.assertEqual((facts["source"],route,group),("approved_artwork","answer","servicos"))
        facts,text,route,group=prepare("Preço de coloração?",opts,cfg,catalog,None)
        self.assertEqual((facts["category"],route,group),("clarify","answer",None))
    def test_one_owner_restart_and_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            def new():
                app=App({"account":1,"inbox":4,"actors":{"A":{"conversation":1,"contact":5}}},{},{},pathlib.Path(d)/"db",d)
                app.native_owner=lambda actor:None
                return app
            app=new();rid=str(uuid.uuid4())
            with app.db() as db:
                db.execute("INSERT INTO events(req,actor,question,options,state,incoming,plan) VALUES(?,'A','input','{}','prepared',1,?)",(rid,json.dumps({"decision":"answer","reply":"Hello","ai":{},"provider":{}})))
                db.executemany("INSERT INTO parts(req,pos,content,private) VALUES(?,?,'content',0)",[(rid,i) for i in range(1,4)])
            posts=[]
            def post(row,p):
                posts.append(p["pos"])
                if p["pos"]==2:raise TimeoutError
                return p["pos"]+20
            app.post_part=post;app.confirm_part=lambda r,p:None
            def deliver():
                try:app.step("deliver",{"request":rid,"incoming":1})
                except (TimeoutError,Stop):pass
            with ThreadPoolExecutor(2) as pool:list(pool.map(lambda i:deliver(),range(2)))
            self.assertEqual(posts,[1,2])
            app=new()
            with self.assertRaisesRegex(Stop,"part_delivery_unknown"):app.step("deliver",{"request":rid,"incoming":1})
            self.assertEqual([p["state"] for p in app.status(rid)["parts"]],["confirmed","sending","pending"])
if __name__=="__main__":unittest.main()
