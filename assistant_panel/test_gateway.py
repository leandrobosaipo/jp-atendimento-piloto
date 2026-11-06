import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from gateway import Gateway,RESERVE,DAY_LIMIT,MODEL,Refused

KB={"version":"fixture-1","entries":[{"id":"hours","title":"Hours","text":"Synthetic hours are 10 to 12.","source":"private document marker","keywords":["hours"]}]}

def uid():return str(uuid.uuid4())

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"state"
        self.sent=[]
        def fake(payload,key):
            self.sent.append(payload)
            return {"status":"completed","usage":{"input_tokens":540,"output_tokens":20},"output":[{"type":"message","content":[{"type":"output_text","text":json.dumps({"answer":"10 to 12","decision":"answer","source_ids":["hours"]})}]}]}
        self.fake=fake;self.g=Gateway(self.path,KB,"fake",fake);self.addCleanup(lambda:self.g.db.close())
        self.sid=uid()
    def msg(self,q="What are the hours?"):return {"op":"ask","session":self.sid,"id":uid(),"text":q}
    def test_continuity_and_replay(self):
        m=self.msg();first=self.g.handle(m);second=self.g.handle(m)
        self.assertTrue(second["replayed"]);self.assertEqual(len(self.sent),1)
        with self.assertRaisesRegex(Refused,"id_conflict"):self.g.handle(dict(m,text="changed"))
        self.g.handle(self.msg("And those hours?"))
        payload=self.sent[1];self.assertFalse(payload["store"]);self.assertEqual(payload["model"],MODEL)
        self.assertEqual(payload["max_output_tokens"],600);self.assertNotIn("tools",payload)
        context=json.loads(payload["input"]);self.assertEqual(len(context["history"]),1);self.assertFalse(context["first_turn"])
        self.assertNotIn("private document marker",payload["input"])
        self.assertEqual(first["sources"],[{"id":"hours","title":"Hours"}])
    def test_unknown_never_retried_after_restart(self):
        def fail(*a):raise TimeoutError()
        self.g.send=fail;m=self.msg()
        with self.assertRaisesRegex(Refused,"held_uncertain"):self.g.handle(m)
        self.g.db.close();self.g=Gateway(self.path,KB,"fake",self.fake)
        with self.assertRaisesRegex(Refused,"held_uncertain"):self.g.handle(m)
        self.assertEqual(self.g.handle({"op":"info"})["total_reserved_micro_usd"],RESERVE);self.assertFalse(self.sent)
    def test_budget_persists_across_sessions(self):
        self.g.send=lambda *_:None
        for _ in range(DAY_LIMIT//RESERVE):
            self.sid=uid()
            with self.assertRaisesRegex(Refused,"held_uncertain"):self.g.handle(self.msg())
        self.sid=uid()
        with self.assertRaisesRegex(Refused,"budget_limit"):self.g.handle(self.msg())
    def test_unsupported_sources_held(self):
        self.g.send=lambda *a:{"status":"completed","usage":{"input_tokens":1,"output_tokens":1},"output":[{"type":"message","content":[{"type":"output_text","text":'{"answer":"bad","decision":"answer","source_ids":["invented"]}'}]}]}
        with self.assertRaisesRegex(Refused,"held_uncertain"):self.g.handle(self.msg())
    def test_private_identity_greeting_and_commercial_guard(self):
        self.g.kb={"version":"v2","entries":[*KB["entries"],{"id":"voice","title":"Identidade","source":"internal","keywords":["identity"],"text":"Téo é o assistente virtual do laboratório."}]}
        greeting=self.g.handle(self.msg("Oi"));self.assertIn("Téo",greeting["reply"]);self.assertEqual(greeting["mode"],"local")
        self.assertEqual(self.g.handle({"op":"info"})["daily_requests"],0)
        self.assertFalse(self.sent)
        following=self.g.handle(self.msg("What are the hours?"));self.assertNotIn("Téo",following["reply"])
        self.sid=uid()
        club=self.g.handle(self.msg("Clube hours?"));self.assertIn("Téo",club["reply"])
        self.assertEqual(club["decision"],"handoff");self.assertIn("confirmadas",club["reply"])

if __name__=="__main__":unittest.main()
