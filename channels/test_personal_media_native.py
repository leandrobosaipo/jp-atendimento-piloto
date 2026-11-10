"""Fake HTTP rehearsal for the concrete pinned personal media client."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from bridge import Held, Outbox
from personal import INSTANCE, ORIGIN
from personal_media import PersonalMedia, PersonalSequence
from personal_media_native import PersonalMediaNative
from test_personal_media import PHONE, COLLECTOR, LID, jpeg_variant


class Response:
    status = 201

    def __init__(self, value):
        self.raw = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.raw[:limit]


class FakeHTTP:
    def __init__(self, jid):
        self.jid = jid
        self.requests = []
        self.records = {}
        self.media = {}
        self.lose_ack = False

    def open(self, request, timeout):
        route = request.full_url.removeprefix(ORIGIN + "/").removesuffix("/" + INSTANCE)
        body = json.loads(request.data)
        self.requests.append((request, route, body, timeout))
        if route == "chat/whatsappNumbers":
            return Response([{"number": PHONE[1:], "exists": True, "jid": self.jid}])
        if route == "chat/findMessages":
            native = body["where"]["key"]["id"]
            return Response({"messages": {"records": [self.records[native]] if native in self.records else []}})
        native = f"accepted-{len([r for r in self.requests if r[1].startswith('message/')]):02d}"
        key = {"id": native, "remoteJid": self.jid, "fromMe": True}
        if route == "message/sendText":
            message = {"conversation": body["text"]}
        else:
            message = {"imageMessage": {"mimetype": body["mimetype"], "fileName": body["fileName"],
                                        "caption": body["caption"]}}
            self.media[native] = base64.b64decode(body["media"], validate=True)
        self.records[native] = {"key": key, "message": message, "status": "PENDING"}
        if self.lose_ack:
            raise TimeoutError("fictional_ack_lost")
        return Response({"key": key, "status": "PENDING"})


class NativeSequenceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.box = Outbox(root / "ledger.sqlite3")
        self.claims = root / "claims"
        self.claims.mkdir(mode=0o700)
        self.jid = PHONE[1:] + "@s.whatsapp.net"
        self.http = FakeHTTP(self.jid)
        self.raw = {}
        assets = []
        for group in ("servicos", "clube-jp"):
            for index in range(1, 4):
                name = f"{group}-{index:02d}"
                raw = jpeg_variant(name)
                self.raw[name] = raw
                assets.append({"id": name, "filename": name + ".jpg", "group": group,
                               "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "approved": True})
        self.manifest = json.dumps({"price_source_policy": "artwork", "assets": assets}).encode()
        self.manifest_hash = hashlib.sha256(self.manifest).hexdigest()
        config = {"origin": ORIGIN, "instance": INSTANCE, "token": "fictional-token",
                  "client_id": "fictional-id", "client_secret": "fictional-secret"}
        self.native = PersonalMediaNative(config, PHONE, manifest_bytes=self.manifest,
                                          manifest_sha256=self.manifest_hash, opener=self.http,
                                          media_readback=self.http.media.__getitem__)
        self.transport = PersonalMedia(PHONE, self.native, manifest_bytes=self.manifest,
                                       manifest_sha256=self.manifest_hash, asset_bytes=self.raw.__getitem__,
                                       read_native=self.native.read_native,
                                       collector_instance_id_hash=COLLECTOR)
        self.transport.verify_recipient()
        self.approved_inbound = self.inbound()
        self.sequence = PersonalSequence(self.box, self.transport, self.claims,
                                         opened_at="2026-11-10T12:00:00Z", read_inbound=self.read_inbound)

    def read_inbound(self, record_id):
        return copy.deepcopy(self.approved_inbound) if record_id == self.approved_inbound["record_id"] else None

    def inbound(self, **changes):
        return {"authenticated": True, "source": "authenticated_mac_collector",
                "instance": INSTANCE, "event": "messages.upsert", "fromMe": False,
                "sender": self.jid, "native_id": "inbound-001", "record_id": "b" * 64,
                "received_at": "2026-11-10T12:00:01Z", "text": "Mensagem fictícia", **changes}

    def witness(self, native, position):
        number_hash = hashlib.sha256(PHONE[1:].encode()).hexdigest()
        return {"authenticated": True, "source": "authenticated_mac_collector", "position": position,
                "receipt": {"instance": INSTANCE, "event": "messages.update", "keyId": native,
                            "fromMe": True, "status": "DELIVERY_ACK", "record_id": str(position) * 64,
                            "instance_id_hash": COLLECTOR, "lid": LID, "at": "2026-11-10T12:01:00Z"},
                "mapping": {"instance_id_hash": COLLECTOR, "pn": number_hash, "reverse_pn": number_hash,
                            "forward_lid": LID, "reverse_lid": LID}}

    def test_fake_http_full_sequence_inbound_bound_and_no_replay(self):
        inbound = self.inbound()
        key, parts = self.sequence.start(inbound, ["Primeira parte", "Segunda parte"], image_group="servicos",
                                         choose=lambda rows: rows[0])
        self.assertEqual(len(parts), 3)
        for position in (1, 2, 3):
            self.assertEqual(self.sequence.advance(key), {"status": "accepted_not_delivered", "position": position})
            with self.assertRaises(Held):
                self.sequence.advance(key)
            native = f"accepted-{position:02d}"
            self.assertEqual(self.sequence.confirm(key, position, self.witness(native, position))["status"], "delivered")
        self.assertEqual(self.sequence.advance(key)["status"], "done")
        self.assertEqual(self.http.media["accepted-03"], self.raw["servicos-01"])
        sent = [r for r in self.http.requests if r[1].startswith("message/")]
        self.assertEqual([r[1] for r in sent], ["message/sendText", "message/sendText", "message/sendMedia"])
        self.assertTrue(all(r[0].get_method() == "POST" and r[0].full_url == f"{ORIGIN}/{r[1]}/{INSTANCE}"
                            and r[3] == 15 for r in self.http.requests))
        restarted = PersonalSequence(Outbox(self.box.path), self.transport, self.claims,
                                     opened_at="2026-11-10T12:00:00+00:00", read_inbound=self.read_inbound)
        replay = restarted.start(inbound, ["Primeira parte", "Segunda parte"], image_group="servicos",
                                 choose=lambda _: self.fail("replay reselected"))[1]
        self.assertEqual([(p["position"], p["payload"]) for p in replay],
                         [(p["position"], p["payload"]) for p in parts])
        self.assertEqual(restarted.advance(key)["status"], "done")
        self.assertEqual(len(sent), 3)
        with self.box.db() as db:
            row = db.execute("SELECT record_id FROM personal_inbound WHERE event_key=?", (key,)).fetchone()
            self.assertEqual(row[0], inbound["record_id"])

    def test_strict_http_allowlist_blocks_other_route_recipient_media_and_unbound_lookup(self):
        prior = len(self.http.requests)
        for route, body in [
            ("message/sendMedia/other", {}), ("message/sendText", {"number": "5511900000001", "text": "x", "linkPreview": False}),
            ("message/sendMedia", {"number": "5511900000001", "mediatype": "image", "mimetype": "image/jpeg",
                                   "caption": "Tabela", "fileName": "servicos-01.jpg", "media": "a", "linkPreview": False}),
            ("chat/findMessages", {"where": {"key": {"id": "unbound-id"}}, "limit": 5}),
            ("chat/whatsappNumbers", {"numbers": ["5511900000001"]}),
            ("instance/logout", {}),
        ]:
            with self.subTest(route=route), self.assertRaises(Held):
                self.native(route, body)
        self.assertEqual(len(self.http.requests), prior)
        good = {"number": PHONE[1:], "mediatype": "image", "mimetype": "image/jpeg", "caption": "Tabela",
                "fileName": "servicos-01.jpg", "media": base64.b64encode(self.raw["servicos-01"]).decode(),
                "linkPreview": False}
        for change in ({"fileName": "../servicos-01.jpg"}, {"media": base64.b64encode(b"\xff\xd8other\xff\xd9").decode()},
                       {"mimetype": "image/png"}):
            with self.assertRaises(Held):
                self.native("message/sendMedia", {**good, **change})
        self.assertEqual(len(self.http.requests), prior)

    def test_authenticated_inbound_must_follow_immutable_opening(self):
        prior = len(self.http.requests)
        for change in ({"authenticated": False}, {"sender": "5511900000001@s.whatsapp.net"},
                       {"fromMe": True}, {"native_id": "other"}, {"record_id": "bad"},
                       {"received_at": "2026-11-10T12:00:00Z"},
                       {"received_at": "2026-11-10T11:59:59Z"}):
            self.approved_inbound = self.inbound(**change)
            with self.subTest(change=change), self.assertRaises(Held):
                self.sequence.start(self.approved_inbound, ["response"])
        self.approved_inbound = self.inbound()
        with self.assertRaisesRegex(Held, "inbound_readback_mismatch"):
            self.sequence.start(self.inbound(text="altered webhook"), ["response"])
        with self.assertRaisesRegex(Held, "personal_window_changed"):
            PersonalSequence(Outbox(self.box.path), self.transport, self.claims,
                             opened_at="2026-11-10T11:00:00Z", read_inbound=self.read_inbound)
        key, _ = self.sequence.start(self.inbound(), ["response"])
        self.approved_inbound = self.inbound(record_id="c" * 64)
        with self.assertRaisesRegex(Held, "inbound_identity_conflict"):
            self.sequence.start(self.inbound(record_id="c" * 64), ["response"])
        self.approved_inbound = self.inbound(native_id="inbound-002")
        with self.assertRaises(Held):
            self.sequence.start(self.inbound(native_id="inbound-002"), ["response"])
        self.assertEqual(len(self.http.requests), prior)
        self.assertEqual(self.sequence.advance(key)["status"], "accepted_not_delivered")

    def test_collector_and_native_readback_gate_each_part(self):
        key, _ = self.sequence.start(self.inbound(), ["response"])
        self.sequence.advance(key)
        witness = self.witness("accepted-01", 1)
        for field, value in (("status", "PENDING"), ("keyId", "other")):
            bad = copy.deepcopy(witness)
            bad["receipt"][field] = value
            with self.assertRaises(Held):
                self.sequence.confirm(key, 1, bad)
        self.http.records["accepted-01"]["message"]["conversation"] = "different"
        with self.assertRaises(Held):
            self.sequence.confirm(key, 1, witness)
        self.http.records["accepted-01"]["message"]["conversation"] = "response"
        self.assertEqual(self.sequence.confirm(key, 1, witness)["status"], "delivered")

    def test_lost_ack_stays_claimed_after_restart(self):
        key, _ = self.sequence.start(self.inbound(), ["response"], image_group="clube",
                                     choose=lambda rows: rows[0])
        self.http.lose_ack = True
        with self.assertRaisesRegex(Held, "send_outcome_unknown"):
            self.sequence.advance(key)
        self.http.lose_ack = False
        restarted = PersonalSequence(Outbox(self.box.path), self.transport, self.claims,
                                     opened_at="2026-11-10T12:00:00Z", read_inbound=self.read_inbound)
        with self.assertRaises(Held):
            restarted.advance(key)
        self.assertEqual(len([r for r in self.http.requests if r[1].startswith("message/")]), 1)

    def test_collector_ack_with_durable_intent_when_native_store_is_empty(self):
        key, _ = self.sequence.start(self.inbound(), ["response"], image_group="servicos",
                                     choose=lambda rows: rows[0])
        self.assertEqual(self.sequence.advance(key)["status"], "accepted_not_delivered")
        self.http.records.pop("accepted-01")  # Existing Evolution setting stores no new message.
        with self.assertRaises(Held):
            self.sequence.confirm(key, 1, self.witness("accepted-01", 1))
        self.assertEqual(self.sequence.confirm_from_intent(key, 1, self.witness("accepted-01", 1))["status"],
                         "delivered")
        with self.box.db() as db:
            receipt = json.loads(db.execute("SELECT receipt FROM channel_parts WHERE event_key=? AND position=1",
                                            (key,)).fetchone()[0])
        self.assertEqual(receipt["content_basis"], "durable_intent_and_native_http_acceptance")
        self.assertEqual(self.sequence.advance(key)["position"], 2)

    def test_intent_claim_and_accepted_request_must_match_before_ack(self):
        key, _ = self.sequence.start(self.inbound(), ["response"])
        self.sequence.advance(key)
        claim = self.claims / f"{key}-1.claim"
        before = claim.read_bytes()
        changed = json.loads(before)
        changed["request_sha256"] = "0" * 64
        claim.write_text(json.dumps(changed))
        with self.assertRaisesRegex(Held, "accepted_intent_mismatch"):
            self.sequence.confirm_from_intent(key, 1, self.witness("accepted-01", 1))
        claim.write_bytes(before)
        with self.box.db() as db:
            row = db.execute("SELECT receipt FROM channel_parts WHERE event_key=? AND position=1", (key,)).fetchone()
            receipt = json.loads(row[0]);receipt["request_sha256"] = "0" * 64
            db.execute("UPDATE channel_parts SET receipt=? WHERE event_key=? AND position=1",
                       (json.dumps(receipt), key))
        with self.assertRaisesRegex(Held, "accepted_intent_mismatch"):
            self.sequence.confirm_from_intent(key, 1, self.witness("accepted-01", 1))

    def test_jpeg_ack_needs_full_media_readback_before_next_part(self):
        key, _ = self.sequence.start(self.inbound(), ["response"], image_group="servicos")
        self.sequence.advance(key)
        self.sequence.confirm(key, 1, self.witness("accepted-01", 1))
        self.sequence.advance(key)
        self.native.media_readback = None
        with self.assertRaisesRegex(Held, "media_readback_required"):
            self.sequence.confirm(key, 2, self.witness("accepted-02", 2))
        self.assertEqual(self.box.parts(key)[1]["state"], "accepted")
        with self.assertRaises(Held):
            self.sequence.advance(key)
        self.assertEqual(len([r for r in self.http.requests if r[1].startswith("message/")]), 2)

    def test_direct_recipient_confirmation_unlocks_only_personal_jpeg(self):
        key, _ = self.sequence.start(self.inbound(), ["Aqui está a tabela de serviços."],
                                     image_group="servicos", choose=lambda rows: rows[0])
        self.sequence.advance(key)
        with self.assertRaises(Held):
            self.sequence.advance_after_recipient_confirmation(key)
        with self.box.db() as db:
            accepted = json.loads(db.execute("SELECT receipt FROM channel_parts WHERE event_key=? AND position=1",
                                             (key,)).fetchone()[0])
        source_hash = hashlib.sha256("inbound-001".encode()).hexdigest()
        confirmation = {"status": "recipient_confirmed", "source": "recipient_direct_chat", "position": 1,
                        "request": str(uuid.uuid5(uuid.NAMESPACE_URL, "jp-personal-inbound:" + source_hash)),
                        "native_id": "accepted-01", "request_sha256": accepted["request_sha256"],
                        "question_sha256": hashlib.sha256("Mensagem fictícia".encode()).hexdigest(),
                        "response_sha256": hashlib.sha256("Aqui está a tabela de serviços.".encode()).hexdigest(),
                        "confirmation_sha256": hashlib.sha256("Recebi só o texto".encode()).hexdigest(),
                        "confirmed_at": "2026-11-10T12:01:00Z"}
        with self.assertRaises(Held):
            self.sequence.record_recipient_confirmation(key, {**confirmation, "response_sha256": "0" * 64})
        self.assertEqual(self.sequence.record_recipient_confirmation(key, confirmation)["provider_state"],
                         "accepted_not_delivered")
        with self.box.db() as db:
            self.assertEqual(db.execute("SELECT state FROM channel_parts WHERE event_key=? AND position=1",
                                        (key,)).fetchone()[0], "accepted")
        with self.assertRaises(Held):
            self.sequence.record_recipient_confirmation(key, confirmation)
        self.assertEqual(self.sequence.advance_after_recipient_confirmation(key)["position"], 2)
        with self.assertRaises(Held):
            self.sequence.advance_after_recipient_confirmation(key)
        self.assertEqual([r[1] for r in self.http.requests if r[1].startswith("message/")],
                         ["message/sendText", "message/sendMedia"])


if __name__ == "__main__":
    unittest.main()
