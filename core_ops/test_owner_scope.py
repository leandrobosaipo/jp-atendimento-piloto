import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from owner import transition


class TransportScope(unittest.TestCase):
    def test_only_bound_inbound_can_request_both_transport_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, state = root / "ops-config", root / "ops-state"
            config.mkdir()
            state.mkdir()
            (state / "lease.lock").touch()
            (state / "admissions").mkdir()
            (state / "ownership.json").write_text(json.dumps({"mode": "maintenance"}))
            (config / "roles.json").write_text(json.dumps({"owner": "central-dell"}))
            manifest = config / "manifest.json"
            manifest.write_text(json.dumps({"inbound": {"nodes": {"receive": "/api/n8n/inbound"}}}))
            def plan(roles):
                return transition(root, "rehearsal", roles, "central-dell", None, False)
            roles = ["inbound", "public_first_reply", "public_art_continuation"]
            self.assertEqual(plan(roles)["roles"], sorted(roles))
            for invalid in [roles[1:], roles[::2], ["inbound", "unrelated_send"]]:
                with self.assertRaises(ValueError):
                    plan(invalid)
            manifest.write_text(json.dumps({"inbound": {"nodes": {"other": "/other"}}}))
            with self.assertRaises(ValueError):
                plan(roles)
            self.assertEqual(json.loads((state / "ownership.json").read_text()), {"mode": "maintenance"})

    def test_review_matches_only_one_original_and_preserves_replay_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config,state=root/'ops-config',root/'ops-state'
            config.mkdir()
            admissions=state/'admissions'
            admissions.mkdir(parents=True)
            (state/'lease.lock').touch()
            (state/'ownership.json').write_text('{"mode":"maintenance"}')
            (config/'manifest.json').write_text('{"clock":{}}')
            (config/'roles.json').write_text('{"owner":"central-dell"}')
            intent=admissions/'a.intent'
            intent.write_bytes(b'original')
            archive=config/'administrative-close.json'
            archive.write_text(json.dumps({intent.name:hashlib.sha256(intent.read_bytes()).hexdigest()}))
            archive.chmod(0o600)
            attrs=list(archive.stat())
            attrs[4]=0
            with patch.object(Path,'lstat',return_value=os.stat_result(attrs)):
                def reopen():
                    return transition(root,'rehearsal',['clock'],'central-dell',None,False)
                self.assertEqual(reopen()['uncertain'],[])
                extra=admissions/'new.intent'
                extra.write_bytes(b'original')
                with self.assertRaisesRegex(ValueError,'close_and_reconcile_required'):
                    reopen()
                extra.unlink()
                intent.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'close_and_reconcile_required'):
                    reopen()
                self.assertFalse(intent.with_suffix('.done').exists())


if __name__ == "__main__":
    unittest.main()
