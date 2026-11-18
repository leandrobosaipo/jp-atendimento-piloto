import sqlite3
import unittest
from unittest.mock import patch
import receipt_capture as rc


class Pending(unittest.TestCase):
    def test_unmatched_receipt_does_not_hold_new_messages_and_replay_is_idempotent(self):
        q=sqlite3.connect(":memory:")
        d=sqlite3.connect(":memory:")
        q.execute("CREATE TABLE receipts(seq INTEGER,id TEXT)")
        d.execute("CREATE TABLE cursor(slot INTEGER,seq INTEGER,record_id TEXT)")
        d.execute("INSERT INTO cursor VALUES(1,0,NULL)")
        read=lambda k:({},{"instance":rc.capture.INSTANCE,"event":"messages.update","data":[
            {"status":"DELIVERY_ACK","keyId":k,"remoteJid":"123@lid"}]})
        rc.tick(q,d,read)
        q.executemany("INSERT INTO receipts VALUES(?,?)",[(1,"held"),(2,"valid")])
        def observe(k):
            return {"matched":1,"waiting":int(k=="held"),"confirmed":int(k=="valid")}
        with patch.object(rc,"_forward",observe):
            result=rc.tick(q,d,read)
            self.assertEqual((result["confirmed"],result["pending"]),(1,1))
            self.assertEqual(d.execute("SELECT seq FROM receipt_cursor").fetchone(),(2,))
            self.assertEqual(rc.tick(q,d,read)["confirmed"],0)
