import sqlite3
import tempfile
import unittest
from pathlib import Path

import reply_capture
import reply_triage as triage


class ReplyTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = reply_capture.connect(Path(self.tmp.name) / "replies.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE core_links(capture INTEGER PRIMARY KEY,status TEXT,members INTEGER,checked INTEGER,execution TEXT)")
        triage.setup(self.db)
        self.db.execute("INSERT INTO captured VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "jp-protese-atendimento", "message-1", 1, "record", 0, 0, "emoji_text",
            "possible_confirmation", "", '["' + "a" * 64 + '"]', "content", "2026-11-12T12:00:00Z",
        ))
        self.db.execute("INSERT INTO core_links VALUES(1,'human_review',0,0,'1')")
        self.db.commit()
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def deliver(self, case):
        self.calls.append(case)
        return (44, 55)

    def test_creates_once_and_never_promotes_emoji_to_confirmation(self):
        first = triage.run_tick(self.db, self.deliver, now=1000)
        second = triage.run_tick(self.db, self.deliver, now=1001)
        self.assertEqual(first, {"created": 1, "existing": 0, "total": 1})
        self.assertEqual(second, {"created": 0, "existing": 0, "total": 1})
        self.assertEqual(len(self.calls), 1)
        self.assertIn("não confirmar agenda", self.calls[0].note)
        self.assertNotIn("confirmado", self.calls[0].note.casefold())

    def test_visit_conflict_gets_safe_suggested_reply(self):
        self.db.execute("UPDATE captured SET kind='text',disposition='changed_review'")
        self.db.execute("UPDATE core_links SET status='visit_review'")
        self.db.commit()
        triage.run_tick(self.db, self.deliver, now=1000)
        self.assertIn("Recebi. Vou conferir o horário com a equipe.", self.calls[0].note)

    def test_context_only_uses_proven_fields(self):
        note = triage.render_note("human_review", "text", "review", "marker", [{"name":"Ana","date":"2026-11-13","time":"15:00","service":"Manutenção"}])
        self.assertIn("Nome: Ana", note)
        self.assertNotIn("telefone", note.casefold())

    def test_retry_after_delivery_error_does_not_persist_claim(self):
        with self.assertRaises(RuntimeError):
            triage.run_tick(self.db, lambda _case: (_ for _ in ()).throw(RuntimeError("offline")), now=1000)
        self.assertEqual(self.db.execute("SELECT count(*) FROM triage_cases").fetchone()[0], 0)

    def test_operator_sees_reply_and_sticker_stays_uninterpreted(self):
        note = triage.render_note("human_review", "text", "review", "ref", received="Combinamos 11h", when="12/11 às 09:23")
        self.assertIn("«Combinamos 11h»", note)
        self.assertIn("09:23 (Cuiabá)", note)
        self.assertIn("Próxima ação", note)
        sticker = triage.render_note("human_review", "sticker", "review", "ref", received="👍")
        self.assertIn("figurinha", sticker)
        self.assertNotIn("👍", sticker)


if __name__ == "__main__":
    unittest.main()
