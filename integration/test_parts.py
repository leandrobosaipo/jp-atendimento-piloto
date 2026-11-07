import json
import pathlib
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from service import App, Stop, message_parts
from policy import prepare

class Parts(unittest.TestCase):
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
            new=lambda:App({"actors":{"A":{"conversation":1}}},{},{},pathlib.Path(d)/"db",d)
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
