"""Synthetic-only durable receiver boundary tests, without network."""
import concurrent.futures
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from bridge import Held
from ingress import Journal

SECRET = b"synthetic-private-receiver-secret-0001"


def wire(identifier="fixture-1", text="Fixture"):
    return json.dumps({"event": "messages.upsert", "instance": "fixture",
                       "data": {"key": {"fromMe": False, "remoteJid": "55555555@s.whatsapp.net", "id": identifier},
                                "message": {"conversation": text}}}).encode()


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(self.tmp.name, 0o700)
        self.path = Path(self.tmp.name) / "journal.sqlite"
        self.journal = Journal(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def receive(self, identifier="fixture-1", text="Fixture"):
        return self.journal.evolution(wire(identifier, text), SECRET, secret=SECRET, instance="fixture")

    def test_restart_duplicate_conflict_and_bounded_read(self):
        first = self.receive()
        self.assertEqual(first["status"], "committed_locally")
        high = self.journal.high_water()
        self.receive("fixture-2")
        self.assertEqual(len(self.journal.read(after=0, through=high)), 1)
        result = subprocess.check_output([sys.executable, "-c", "from ingress import Journal; import sys; j=Journal(sys.argv[1]); print(j.high_water())", str(self.path)], cwd=Path(__file__).parent)
        self.assertGreater(int(result), high)
        self.journal = Journal(self.path)
        self.assertTrue(self.receive()["events"][0]["duplicate"])
        self.assertEqual(self.receive()["events"][0]["sequence"], first["events"][0]["sequence"])
        with self.assertRaisesRegex(Held, "identity_conflict"):
            self.receive(text="Changed")
        self.assertEqual(len(self.journal.read(after=0, through=self.journal.high_water())), 2)

    def test_authentication_and_normalization_before_commit(self):
        for raw, auth, instance in [(wire(), b"wrong", "fixture"), (b"bad", SECRET, "fixture"), (wire(), SECRET, "other")]:
            with self.assertRaises(Held):
                self.journal.evolution(raw, auth, secret=SECRET, instance=instance)
        self.assertEqual(self.journal.high_water(), 0)

    def test_concurrent_duplicate_has_one_record(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.receive(), range(8)))
        self.assertEqual(sum(not r["events"][0]["duplicate"] for r in results), 1)
        self.assertEqual(len(self.journal.read(after=0, through=self.journal.high_water())), 1)

    def test_failed_transaction_has_no_ack_or_orphan(self):
        with self.journal.box.db() as db:
            db.execute("CREATE TRIGGER fail_ingress BEFORE INSERT ON ingress_journal BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.receive()
        with self.journal.box.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM channel_events").fetchone()[0], 0)
        self.assertEqual(self.journal.high_water(), 0)

    def test_instagram_batch_conflict_rolls_back_and_bad_signature(self):
        def signed(messages):
            raw = json.dumps({"object": "instagram", "entry": [{"id": "fixture", "messaging": messages}]}).encode()
            return raw, "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
        def item(identifier, text):
            return {"sender": {"id": "synthetic"}, "recipient": {"id": "fixture"}, "message": {"mid": identifier, "text": text}}
        raw, signature = signed([item("one", "First")])
        self.journal.instagram(raw, signature, app_secret=SECRET, account="fixture")
        with self.assertRaises(Held):
            self.journal.instagram(raw, "bad", app_secret=SECRET, account="fixture")
        raw, signature = signed([item("two", "Second"), item("one", "Changed")])
        with self.assertRaisesRegex(Held, "identity_conflict"):
            self.journal.instagram(raw, signature, app_secret=SECRET, account="fixture")
        self.assertEqual(len(self.journal.read(after=0, through=self.journal.high_water())), 1)


if __name__ == "__main__":
    unittest.main()
