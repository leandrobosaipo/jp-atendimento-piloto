"""Offline synthetic credential refresh and rollback contracts."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from refresh_envelope import build, private, sha, transition, ORIGIN


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "envelope.json"
        self.old = json.dumps({"origin": ORIGIN, "cookieHeader": "session=old-fixture",
                               "services": {"fixture": {"id": "1"}},
                               "professionals": {"human": {"human_only": True}},
                               "policy": {"write": False}}).encode()
        self.path.write_bytes(self.old); self.path.chmod(0o600)
        self.candidate = json.dumps({"origin": ORIGIN, "cookieHeader": "session=new-fixture"}).encode()
        self.backup = self.root / "archived.json"

    def tearDown(self): self.tmp.cleanup()

    def test_preserves_all_non_session_fields(self):
        after = json.loads(build(self.old, self.candidate))
        before = json.loads(self.old)
        self.assertNotEqual(after.pop("cookieHeader"), before.pop("cookieHeader"))
        self.assertEqual(after, before)

    def test_rejects_catalog_origin_and_header_injection(self):
        cases = [{"origin": ORIGIN, "cookieHeader": "a=b", "services": {}},
                 {"origin": "https://invalid.example", "cookieHeader": "a=b"},
                 {"origin": ORIGIN, "cookieHeader": "a=b\r\nInjected: value"}]
        for candidate in cases:
            with self.assertRaises(RuntimeError): build(self.old, json.dumps(candidate).encode())

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


if __name__ == "__main__": unittest.main()
