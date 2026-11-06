import unittest
from policy import classify,service_price

class Test(unittest.TestCase):
    def test_precedence_and_ambiguous_art(self):
        source={"name":"Fixture","price_cents":3000}
        for p,amount,route,art in [('pending_user_resolution',None,'handoff',None),('human_review',None,'handoff',None),('appbarber','30,00','answer',None),('artwork','35,00','answer','servicos')]:
            c={"price_source_policy":p,"service_price_by_key":{"x":3500}}
            _,reply,action,group=service_price(dict(source),"x",c,"servicos")
            if amount:self.assertIn(amount,reply)
            self.assertEqual((action,group),(route,art))
        self.assertIsNone(service_price(source,"none",{"price_source_policy":"appbarber","service_price_by_key":{}},"servicos")[-1])
    def test_specificity(self):
        cfg={"services":{k:{} for k in ("coloring","beard","haircut","haircut_beard")},"professionals":{"alex":{"name":"Alex Fixture"}}}
        opts={"intent":"auto","service":"","professional":"","date":""}
        self.assertEqual(classify("Preço de corte e barba com Alex",opts,cfg)[:3],("price","haircut_beard","alex"))
        self.assertEqual(classify("Quanto custa coloração de prótese?",opts,cfg)[0],"clarify")

if __name__=="__main__":unittest.main()
