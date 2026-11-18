import sqlite3
import unittest

import capture_link
import reply_capture as cap
from receipt_reply_identity import team_aliases, verified_pairs


class ReplyIdentityTests(unittest.TestCase):
    def test_only_verified_bidirectional_delivered_pairs(self):
        ledger = sqlite3.connect(":memory:")
        ledger.execute("CREATE TABLE dispatch_attempts(attempt,native,destination,state,attempted)")
        pn, lid = cap.digest("123@s.whatsapp.net"), cap.digest("456@lid")
        for idx, state in enumerate(("accepted", "read", "delivered")):
            ledger.execute("INSERT INTO dispatch_attempts VALUES(?,?,?,?,?)", (str(idx), "m"+str(idx), "123@s.whatsapp.net", state, 1000))
        def authority(attempt):
            return "jp", dict(instance="jp", native="m"+attempt, attempt=attempt, pn=pn, lid=lid, reverse_pn=pn, forward_lid=lid if attempt=="1" else "wrong")
        self.assertEqual(verified_pairs(ledger, authority, 1001), {(pn,lid)})
        self.assertEqual(verified_pairs(ledger, authority, 1001+9*86400), set())

    def test_team_intervention_across_aliases_and_idempotence(self):
        q, d = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        q.execute("CREATE TABLE receipts(seq INTEGER,id TEXT)")
        q.execute("INSERT INTO receipts VALUES(1,'orig')")
        def read(rid):
            self.assertEqual(rid,"orig")
            return {"receivedAt":"2026-11-18T18:00:00Z"}, {"instance":cap.INSTANCE,"event":"messages.upsert","data":{"key":{"fromMe":True,"id":"human","remoteJid":"456@lid"}}}
        for _ in range(2):
            self.assertTrue(team_aliases(q,d,read))
        self.assertEqual(d.execute("SELECT alias,mid FROM reply_team_alias").fetchall(), [(cap.digest("456@lid"),"human")])
        d.execute("CREATE TABLE captured(seq,address_hashes)")
        d.execute("INSERT INTO captured VALUES(2,?)", ('["lid"]',))
        self.assertTrue(capture_link.has_newer_identity(d,{"rowid":0,"seq":1},"pn",[dict(pn="pn",lid="lid")]))
