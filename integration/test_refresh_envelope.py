"""Offline synthetic credential refresh and rollback contracts."""
import json
import hashlib
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from refresh_envelope import build, database_audit, private, sha, transition, ORIGIN


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "envelope.json"
        self.old = json.dumps({"origin": ORIGIN, "cookieHeader": "session=old-fixture",
                               "source_generation": 1, "source_verified_at": "2026-01-01T00:00:00.000Z",
                               "captured_at": "2026-01-01T00:00:00.000000+00:00",
                               "services": {"fixture": {"id": "1"}},
                               "professionals": {"human": {"human_only": True}},
                               "policy": {"write": False}}).encode()
        self.path.write_bytes(self.old); self.path.chmod(0o600)
        self.candidate = json.dumps({"origin": ORIGIN, "cookieHeader": "session=new-fixture",
                                     "source_generation": 2, "source_verified_at": "2026-01-02T00:00:00.000Z",
                                     "captured_at": "2026-01-02T00:00:00.000000+00:00"}).encode()
        self.backup = self.root / "archived.json"

    def tearDown(self): self.tmp.cleanup()

    def test_refresh_preserves_persisted_human_takeover(self):
        path=self.root / "events.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE events(state TEXT, reason TEXT, incoming INTEGER)")
            db.execute("INSERT INTO events VALUES('human','human_ownership',10)")
        self.assertEqual(database_audit(path,allow_human_holds=True)["events"]["count"],1)
        with self.assertRaises(RuntimeError):
            database_audit(path)
        for state,reason,incoming in [("human",None,10),("human","human_ownership",None),("planning","human_ownership",10)]:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE events SET state=?,reason=?,incoming=?",(state,reason,incoming))
            with self.assertRaises(RuntimeError):
                database_audit(path,allow_human_holds=True)

    def test_retained_budget_pair_requires_restored_digest_and_no_outgoing_effect(self):
        path = self.root/"retained.sqlite3"
        def checkpoint():
            h=hashlib.sha256()
            with sqlite3.connect(path) as conn:
                for stmt in conn.iterdump():
                    b=stmt.encode();h.update(len(b).to_bytes(8,'big'));h.update(b)
            return h.hexdigest()
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE events(actor,state,reason,incoming,outgoing,asset,plan,receipt)")
            db.executemany("INSERT INTO events VALUES(?,?,?,?,NULL,NULL,NULL,NULL)",[
                ('A','planning','ai_session_limit',1),('A','waiting','prior_event_requires_review',2)])
        evidence=checkpoint()
        with self.assertRaises(RuntimeError):database_audit(path,allow_human_holds=True)
        self.assertEqual(database_audit(path,allow_human_holds=True,retained_db_sha256=evidence)['events']['count'],2)
        with sqlite3.connect(path) as db:db.execute("UPDATE events SET actor='B' WHERE state='waiting'")
        for digest in (evidence,checkpoint()):
            with self.assertRaises(RuntimeError):database_audit(path,allow_human_holds=True,retained_db_sha256=digest)
        with sqlite3.connect(path) as db:db.execute("UPDATE events SET actor='A',receipt='unknown' WHERE state='waiting'")
        with self.assertRaises(RuntimeError):database_audit(path,allow_human_holds=True,retained_db_sha256=checkpoint())

    def test_preserves_all_non_session_fields(self):
        after = json.loads(build(self.old, self.candidate))
        before = json.loads(self.old)
        self.assertNotEqual(after.pop("cookieHeader"), before.pop("cookieHeader"))
        for name in ("source_generation", "source_verified_at", "captured_at"):
            after.pop(name); before.pop(name)
        self.assertEqual(after, before)

    def test_rejects_catalog_origin_and_header_injection(self):
        cases = [{"origin": ORIGIN, "cookieHeader": "a=b", "services": {}},
                 {"origin": "https://invalid.example", "cookieHeader": "a=b"},
                 {"origin": ORIGIN, "cookieHeader": "a=b\r\nInjected: value"}]
        for candidate in cases:
            fresh = json.loads(self.candidate); fresh.update(candidate)
            with self.assertRaises(RuntimeError): build(self.old, json.dumps(fresh).encode())

    def test_success_ack_after_reloaded_credential(self):
        calls = []
        def check(expected):
            self.assertEqual(sha(private(self.path)), expected)
            calls.append("check")
        result = transition(self.path, self.candidate, self.backup,
                            stop=lambda: calls.append("stop"), start=lambda: calls.append("start"),
                            check=check, invariant=lambda: calls.append("invariant"))
        self.assertEqual(result["status"], "local_credential_reloaded")
        self.assertEqual(calls, ["stop", "invariant", "start", "check", "invariant"])
        self.assertEqual(private(self.backup), self.old)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failed_consumer_restores_old_and_preserves_backup(self):
        calls = []
        def check(expected):
            calls.append(expected)
            if expected != sha(self.old): raise RuntimeError("synthetic postcheck failure")
        with self.assertRaisesRegex(RuntimeError, "rollback_verified"):
            transition(self.path, self.candidate, self.backup, stop=lambda: None, start=lambda: None,
                       check=check, invariant=lambda: None)
        self.assertEqual(private(self.path), self.old)
        self.assertEqual(private(self.backup), self.old)
        self.assertEqual(calls[-1], sha(self.old))
        with self.assertRaisesRegex(RuntimeError, "prior_attempt"):
            transition(self.path, self.candidate, self.backup, stop=lambda: self.fail("must hold"),
                       start=lambda: None, check=lambda _: None, invariant=lambda: None)

    def test_private_guard_rejects_symlink_or_permissive_file(self):
        self.path.chmod(0o644)
        with self.assertRaises(RuntimeError): private(self.path)
        link = self.root / "link"; link.symlink_to(self.path)
        with self.assertRaises(RuntimeError): private(link)

    def test_rejects_generation_and_timestamp_regression(self):
        for field, value in (("source_generation", 1), ("source_generation", True),
                             ("source_verified_at", "2025-01-01T00:00:00Z"),
                             ("captured_at", "2026-01-02T00:00:00")):
            candidate = json.loads(self.candidate); candidate[field] = value
            with self.assertRaises(RuntimeError): build(self.old, json.dumps(candidate).encode())

    def test_third_party_drift_withholds_rollback(self):
        third_party = b'{"third_party":"preserve"}'
        def check(_):
            self.path.write_bytes(third_party)
            raise RuntimeError("synthetic concurrent change")
        with self.assertRaisesRegex(RuntimeError, "rollback_withheld"):
            transition(self.path, self.candidate, self.backup, stop=lambda: None,
                       start=lambda: None, check=check, invariant=lambda: None)
        self.assertEqual(self.path.read_bytes(), third_party)
        self.assertEqual(private(self.backup), self.old)


if __name__ == "__main__": unittest.main()
