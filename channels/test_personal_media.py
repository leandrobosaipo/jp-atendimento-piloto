"""Fictional personal media and per-part receipt tests; no network client."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from bridge import Event, Held, Outbox
from personal import INSTANCE
from personal_media import PersonalMedia, claim_and_advance
from test_evolution import MEDIA


PHONE = "+5511900000000"
COLLECTOR = "a" * 64
LID = "fictional-lid"


def jpeg_variant(label):
    marker = label.encode()
    return MEDIA[:2] + b"\xff\xfe" + (len(marker) + 2).to_bytes(2, "big") + marker + MEDIA[2:]


class PersonalMediaTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.box = Outbox(root / "outbox.sqlite3")
        self.claims = root / "claims"
        self.claims.mkdir(mode=0o700)
        self.raw = {}
        assets = []
        for group in ("servicos", "clube-jp"):
            for index in range(1, 4):
                name = f"{group}-{index:02d}"
                raw = jpeg_variant(name)
                self.raw[name] = raw
                assets.append({"id": name, "group": group, "filename": name + ".jpg",
                               "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "approved": True})
        self.catalog = {"price_source_policy": "artwork", "assets": assets}
        self.sent = []
        self.saved = {}
        self.drop_ack = False
        self.result_jid = PHONE[1:] + "@s.whatsapp.net"
        self.transport = self.make_transport()
        self.transport.verify_recipient()

    def make_transport(self, catalog=None, asset_bytes=None):
        manifest = json.dumps(catalog or self.catalog, separators=(",", ":")).encode()
        return PersonalMedia(PHONE, self.exchange, manifest_bytes=manifest,
                             manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                             asset_bytes=asset_bytes or self.raw.__getitem__, read_native=self.saved.get,
                             collector_instance_id_hash=COLLECTOR)

    def exchange(self, route, body):
        if route == "chat/whatsappNumbers":
            return [{"number": PHONE[1:], "exists": True, "jid": self.result_jid}]
        self.assertIn(route, ("message/sendText", "message/sendMedia"))
        self.sent.append((route, body))
        native = f"native-{len(self.sent):02d}"
        saved = {"id": native, "instance": INSTANCE, "destination": self.transport.jid,
                 "fromMe": True}
        if route == "message/sendText":
            saved["text"] = body["text"]
        else:
            saved.update(bytes=base64.b64decode(body["media"], validate=True), mime=body["mimetype"],
                         filename=body["fileName"], caption=body["caption"])
        self.saved[native] = saved
        if self.drop_ack:
            raise TimeoutError("fictional_ack_lost")
        return {"key": {"id": native, "remoteJid": self.transport.jid, "fromMe": True}, "status": "PENDING"}

    def plan(self, event_id="event001", image_group="servicos", choose=None):
        event = self.transport.event(event_id, "fictional personal conversation")
        return self.transport.prepare(self.box, event, ["*Primeira parte*", "Segunda parte"],
                                      image_group=image_group, choose=choose)

    def witness(self, native, position, status="DELIVERY_ACK"):
        phone_hash = hashlib.sha256(self.transport.jid.split("@")[0].encode()).hexdigest()
        return {"authenticated": True, "source": "authenticated_mac_collector", "position": position,
                "receipt": {"instance": INSTANCE, "event": "messages.update", "keyId": native,
                            "fromMe": True, "status": status, "record_id": str(position) * 64,
                            "instance_id_hash": COLLECTOR, "lid": LID, "at": "2026-11-10T09:00:00Z"},
                "mapping": {"instance_id_hash": COLLECTOR, "pn": phone_hash, "reverse_pn": phone_hash,
                            "forward_lid": LID, "reverse_lid": LID}}

    def advance(self, key):
        return claim_and_advance(self.box, key, self.transport, self.claims)

    def test_six_approved_variants_reserve_correct_group_and_replay(self):
        self.assertEqual(len(self.transport.manifest_assets()), 6)
        for group in ("servicos", "clube"):
            for index in range(3):
                key, parts = self.plan(f"event-{group}-{index}", group, choose=lambda rows, i=index: rows[i])
                asset = json.loads(parts[-1]["payload"])["asset"]
                self.assertEqual(asset["group"], group)
                self.assertEqual(asset["id"], self.transport.manifest_assets()[index + (3 if group == "clube" else 0)]["id"])
                same = self.transport.prepare(self.box, self.transport.event(f"event-{group}-{index}",
                    "fictional personal conversation"), ["*Primeira parte*", "Segunda parte"],
                    image_group=group, choose=lambda _: self.fail("replay reselected"))
                self.assertEqual(parts, same[1])
                self.assertEqual(key, same[0])

    def test_ordered_text_jpeg_claim_and_collector_receipt_per_part(self):
        key, parts = self.plan(choose=lambda rows: rows[1])
        self.assertEqual([json.loads(p["payload"])["kind"] for p in parts], ["text", "text", "image"])
        for position in (1, 2, 3):
            self.assertEqual(self.advance(key), {"status": "accepted_not_delivered", "position": position})
            with self.assertRaises(Held):
                self.advance(key)
            native = f"native-{position:02d}"
            self.assertEqual(self.box.parts(key)[position - 1]["state"], "accepted")
            if position > 1:
                reused = self.witness(native, position)
                reused["receipt"]["record_id"] = str(position - 1) * 64
                with self.assertRaisesRegex(Held, "collector_record_reused"):
                    self.transport.confirm_part(self.box, key, position, reused)
            self.assertEqual(self.transport.confirm_part(self.box, key, position,
                             self.witness(native, position, "READ" if position == 3 else "DELIVERY_ACK"))["status"], "delivered")
        self.assertEqual(self.advance(key)["status"], "done")
        self.assertEqual([route for route, _ in self.sent],
                         ["message/sendText", "message/sendText", "message/sendMedia"])
        image = self.sent[-1][1]
        self.assertEqual(image["number"], self.transport.jid.split("@")[0])
        self.assertEqual(image["mimetype"], "image/jpeg")
        self.assertEqual(base64.b64decode(image["media"], validate=True), self.raw["servicos-02"])
        self.assertEqual(len(list(self.claims.glob("*.claim"))), 3)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.claims.glob("*.claim")))

    def test_third_party_and_wrong_acceptance_are_blocked(self):
        with self.assertRaises(Held):
            self.transport.prepare(self.box, Event("whatsapp", INSTANCE, "100000000@g.us", "event002", "x"), ["x"])
        with self.assertRaises(Held):
            self.transport.prepare(self.box, Event("whatsapp", INSTANCE, "5511900000001@s.whatsapp.net", "event003", "x"), ["x"])
        with self.assertRaises(Held):
            self.transport.send({"channel": "whatsapp", "account": INSTANCE, "sender": "100000000@g.us"},
                                {"kind": "text", "text": "x"})
        foreign = self.box.receive(Event("whatsapp", INSTANCE, "5511900000001@s.whatsapp.net", "event004", "x"))
        self.box.prepare(foreign, ["x"])
        with self.assertRaises(Held):
            self.advance(foreign)
        self.assertEqual(list(self.claims.glob("*.claim")), [])
        key, _ = self.plan()
        self.result_jid = "100000000@g.us"
        with self.assertRaises(Held):
            self.make_transport().verify_recipient()
        real = self.transport.native
        self.transport.native = lambda route, body: {"key": {"id": "native-99", "remoteJid": "other@s.whatsapp.net", "fromMe": True}}
        with self.assertRaises(Held):
            self.advance(key)
        self.transport.native = real
        with self.assertRaises(Held):
            self.advance(key)
        self.assertEqual(self.box.parts(key)[0]["state"], "sending")
        self.assertEqual(len(list(self.claims.glob("*.claim"))), 1)

    def test_bad_manifest_or_bytes_hold_before_provider_call(self):
        for change in (lambda c: c.update(price_source_policy="pending_user_resolution"),
                       lambda c: c["assets"].pop(),
                       lambda c: c["assets"][0].update(approved=False),
                       lambda c: c["assets"][0].update(group="other")):
            catalog = copy.deepcopy(self.catalog)
            change(catalog)
            with self.assertRaises(Held):
                self.make_transport(catalog=catalog)
        manifest = json.dumps(self.catalog).encode()
        with self.assertRaises(Held):
            PersonalMedia(PHONE, self.exchange, manifest_bytes=manifest, manifest_sha256="0" * 64,
                          asset_bytes=self.raw.__getitem__, read_native=self.saved.get,
                          collector_instance_id_hash=COLLECTOR)
        key, _ = self.plan()
        self.advance(key)
        self.transport.confirm_part(self.box, key, 1, self.witness("native-01", 1))
        self.advance(key)
        self.transport.confirm_part(self.box, key, 2, self.witness("native-02", 2))
        self.transport.asset_bytes = lambda _: b"\xff\xd8wrong\xff\xd9"
        with self.assertRaises(Held):
            self.advance(key)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.box.parts(key)[2]["state"], "sending")

    def test_receipt_must_bind_position_native_id_mapping_and_native_content(self):
        key, _ = self.plan()
        self.advance(key)
        good = self.witness("native-01", 1)
        for field, value in (("authenticated", False), ("source", "webhook"), ("position", 2)):
            with self.assertRaises(Held):
                self.transport.confirm_part(self.box, key, 1, {**good, field: value})
        for field, value in (("keyId", "native-02"), ("status", "PENDING"),
                             ("instance_id_hash", "other"), ("record_id", "bad")):
            bad = copy.deepcopy(good)
            bad["receipt"][field] = value
            with self.assertRaises(Held):
                self.transport.confirm_part(self.box, key, 1, bad)
        for field in ("pn", "reverse_pn", "forward_lid", "reverse_lid"):
            bad = copy.deepcopy(good)
            bad["mapping"][field] = "other"
            with self.assertRaises(Held):
                self.transport.confirm_part(self.box, key, 1, bad)
        self.saved["native-01"]["text"] = "different"
        with self.assertRaises(Held):
            self.transport.confirm_part(self.box, key, 1, good)
        self.saved["native-01"]["text"] = "*Primeira parte*"
        self.assertEqual(self.transport.confirm_part(self.box, key, 1, good)["status"], "delivered")

    def test_lost_ack_keeps_claim_and_no_second_attempt(self):
        key, _ = self.plan()
        self.drop_ack = True
        with self.assertRaisesRegex(Held, "send_outcome_unknown"):
            self.advance(key)
        with self.assertRaises(Held):
            self.advance(key)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.box.parts(key)[0]["state"], "sending")


if __name__ == "__main__":
    unittest.main()
