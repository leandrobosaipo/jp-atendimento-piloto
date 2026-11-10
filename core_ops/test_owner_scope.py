import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
