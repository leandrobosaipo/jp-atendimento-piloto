"""No network, send path, real identity or private customer fixture."""
import base64
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from customer_ingress import Chatwoot, Intake, Held, digest, receipt_id, process_channel
from customer_ingress import native_sender
from customer_planner import PlannerReservation, ReservationError


INSTANCE = "jp-fixture"
KNOWN = "5511990000001@s.whatsapp.net"
NEW = "5511990000002@s.whatsapp.net"


class FakeChatwoot:
    account, inbox, channel = 12, 22, "fixturechannel"

    def __init__(self):
        self.contacts = {}
        self.conversations = {}
        self.messages_by_conversation = {}
        self.contact_calls = self.incoming_calls = self.outgoing_calls = 0
        self.assigned = False
        self.fail_incoming = False

    def contact(self, address_hash):
        self.contact_calls += 1
        contact = len(self.contacts) + 40
        self.contacts[contact] = address_hash
        return {"id": contact, "identifier": "jp-intake-" + address_hash}

    def contact_inbox(self, contact_id, source_id):
        assert contact_id in self.contacts
        return {"source_id": source_id}

    def conversation(self, contact_id, source_id):
        cid = len(self.conversations) + 50
        self.conversations[cid] = (contact_id, source_id)
        return {"id": cid}

    def conversation_readback(self, conversation_id):
        contact, _ = self.conversations[conversation_id]
        return {"account_id": self.account, "inbox_id": self.inbox, "id": conversation_id,
                "meta": {"sender": {"id": contact}, "assignee": {"id": 1} if self.assigned else None},
                "labels": []}

    def incoming(self, source_id, conversation_id, text, request_id):
        self.incoming_calls += 1
        if self.fail_incoming:
            raise TimeoutError("unknown result")
        assert self.conversations[conversation_id][1] == source_id
        mid = 100 + self.incoming_calls
        self.messages_by_conversation.setdefault(conversation_id, []).append({
            "id": mid, "message_type": 0, "content": text,
            "sender": {"id": self.conversations[conversation_id][0]}, "echo_id": request_id})
        return {"id": mid}

    def messages(self, conversation_id):
        return self.messages_by_conversation[conversation_id]


class IngressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chat = FakeChatwoot()
        self.config = {"instance": INSTANCE, "workflow": "fixture-wf", "account": 12,
                       "inbox": 22, "channel": "fixturechannel", "fixture_account": 1,
                       "review_inbox": 3}
        self.directory = {"data": [{"Codigo": "7101", "Cli_Celular": "+55 11 99000-0001",
                                    "PRe_Ativo": "1"}], "recordsFiltered": 1, "recordsTotal": 1}
        self.lookup = lambda numbers: [{"number": numbers[0], "exists": True,
                                        "jid": KNOWN}] if numbers else []
        self.app = Intake(pathlib.Path(self.tmp.name) / "intake.sqlite3", self.config,
                          self.chat, lambda: self.directory, lambda n: self.lookup(n))
        self.planner = PlannerReservation(pathlib.Path(self.tmp.name) / "reservations.sqlite3",
                                          pathlib.Path(self.tmp.name) / "intake.sqlite3", self.chat)

    def event(self, sender, native="FIXTURE001", text="Mensagem fictícia"):
        raw = json.dumps({"instance": INSTANCE, "event": "messages.upsert", "data": {
            "key": {"id": native, "remoteJid": sender, "fromMe": False},
            "message": {"conversation": text}}}, separators=(",", ":")).encode()
        return {"raw_base64": base64.b64encode(raw).decode(), "record_id": receipt_id(INSTANCE, raw),
                "workflow_id": "fixture-wf", "execution_id": "101"}

    def test_new_and_identified_have_separate_materialized_conversations(self):
        new = self.app.receive(self.event(NEW, "FIXTURE001"))
        known = self.app.receive(self.event(KNOWN, "FIXTURE002"))
        self.assertEqual((new["state"], new["reason"]), ("public_only", None))
        self.assertEqual((known["state"], known["reason"]), ("ready", None))
        self.assertNotEqual(new["contact"], known["contact"])
        self.assertNotEqual(new["conversation"], known["conversation"])
        self.assertEqual(self.chat.incoming_calls, 2)
        self.assertEqual(self.chat.outgoing_calls, 0)
        receipt_new = self.planner.reserve({k: new[k] for k in
            ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")})
        receipt_known = self.planner.reserve({k: known[k] for k in
            ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")})
        self.assertEqual(receipt_new["mode"], "public_information")
        self.assertEqual(receipt_new["capabilities"], ["public_price", "human_handoff"])
        self.assertEqual(receipt_known["mode"], "identified_customer")
        self.assertEqual(receipt_known["status"], "reserved")
        with self.planner.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reservations").fetchone()[0], 2)
        with self.app.db() as db:
            identity = json.loads(db.execute("SELECT identity FROM contacts WHERE address_hash=?",
                         (digest(KNOWN.encode()),)).fetchone()[0])
            self.assertEqual(identity["client_code"], "7101")

    def test_exact_retry_reuses_ids_and_conflict_never_reposts(self):
        first = self.app.receive(self.event(KNOWN))
        self.assertEqual(self.app.receive(self.event(KNOWN)), first)
        self.assertEqual((self.chat.contact_calls, self.chat.incoming_calls), (1, 1))
        submitted = {k: first[k] for k in ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")}
        self.assertEqual(self.planner.reserve(submitted), self.planner.reserve(submitted))
        with self.assertRaises(Held):
            self.app.receive(self.event(KNOWN, text="Texto alterado"))
        self.assertEqual(self.chat.incoming_calls, 1)

    def test_human_ownership_retains_incoming(self):
        self.chat.assigned = True
        row = self.app.receive(self.event(KNOWN))
        self.assertEqual((row["state"], row["reason"]), ("held", "human_ownership"))
        self.assertIsInstance(row["message"], int)
        self.assertEqual(self.chat.outgoing_calls, 0)
        self.assertEqual(self.planner.reserve({k: row[k] for k in
            ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")})["status"], "held")

    def test_uncertain_post_is_held_without_retry(self):
        self.chat.fail_incoming = True
        event = self.event(KNOWN)
        with self.assertRaises(TimeoutError):
            self.app.receive(event)
        self.assertEqual(self.app.receive(event)["reason"], "outcome_unknown")
        self.assertEqual(self.chat.incoming_calls, 1)

    def test_wrong_scope_record_and_identity_are_rejected(self):
        with self.assertRaises(Held):
            Intake(pathlib.Path(self.tmp.name) / "bad.sqlite3",
                   {**self.config, "account": 1}, self.chat, lambda: self.directory, self.lookup)
        bad = self.event(KNOWN)
        bad["record_id"] = "0" * 64
        with self.assertRaises(Held):
            self.app.receive(bad)
        bad = self.event("123456789@lid")
        with self.assertRaises(Held):
            self.app.receive(bad)
        self.assertEqual(self.chat.incoming_calls, 0)

    def test_bidirectional_instance_native_link(self):
        lid = "100000001@lid"
        proof = {"instance": INSTANCE, "paused": False,
                 "forward": {KNOWN: lid}, "reverse": {lid: KNOWN}}
        self.app.bindings = lambda: proof
        def linked(sender, alt=None):
            body = self.event(sender, native="LINKED001")
            raw = json.loads(base64.b64decode(body["raw_base64"]))
            if alt is not None:
                raw["data"]["key"]["remoteJidAlt"] = alt
            encoded = json.dumps(raw, separators=(",", ":")).encode()
            return {**body, "raw_base64": base64.b64encode(encoded).decode(),
                    "record_id": receipt_id(INSTANCE, encoded)}
        first = self.app.receive(linked(lid, KNOWN))
        self.assertEqual(first["state"], "ready")
        self.assertEqual((first["workflow"], first["execution"]), ("fixture-wf", "101"))
        self.assertEqual(self.app.receive(linked(lid, KNOWN)), first)
        self.assertEqual(self.chat.incoming_calls, 1)
        for changed in (
            {**proof, "paused": True},
            {**proof, "instance": "other"},
            {**proof, "reverse": {}},
            {**proof, "reverse": {lid: NEW}},
            {**proof, "forward": {KNOWN: lid, NEW: lid}},
        ):
            with self.assertRaises(Held):
                native_sender({"key": {"fromMe": False, "remoteJid": lid,
                                       "remoteJidAlt": KNOWN}}, INSTANCE, changed)
        with self.assertRaises(Held):
            native_sender({"key": {"fromMe": False, "remoteJid": lid,
                                   "remoteJidAlt": NEW}}, INSTANCE, proof)
        with self.assertRaises(Held):
            native_sender({"key": {"fromMe": False, "remoteJid": "123@g.us"}}, INSTANCE, proof)

    def test_claimed_collector_handoff_keeps_execution_and_never_sends(self):
        lid = "100000001@lid"
        proof = {"instance": INSTANCE, "instance_id_hash": "a" * 64, "paused": False,
                 "forward": {KNOWN: lid}, "reverse": {lid: KNOWN}}
        self.config.update(channel_epoch="12345678-1234-1234-1234-123456789abc",
                           channel_workflow="candidatewf", native_instance_id_hash="a" * 64)
        self.app.bindings = lambda: proof
        body = self.event(lid, native="LINKED002")
        raw = base64.b64decode(body["raw_base64"])
        proof.update(epoch=self.config["channel_epoch"], record_id=body["record_id"])
        self.config["channel_fixture_records"] = [body["record_id"]]
        class FakeHeldPlan:
            calls = 0
            def prepare(self, values):
                self.calls += 1
                return {"status": "prepared_held", "parts": [1, 2], "transport_attempts": 0}
        plan = FakeHeldPlan()
        def call(execution):
            return process_channel(raw, body["record_id"], "candidatewf", execution,
                                   self.config["channel_epoch"], self.config,
                                   self.app, self.planner, plan)
        first = call("701")
        self.assertEqual((first["status"], first["plan"], first["execution"], first["first_execution"]),
                         ("retained", "prepared_held", "701", "701"))
        retry = call("702")
        self.assertEqual((retry["intake"], retry["reservation"], retry["first_execution"]),
                         (first["intake"], first["reservation"], "701"))
        self.assertEqual(retry["execution"], "702")
        self.assertEqual(self.chat.incoming_calls, 1)
        self.assertEqual(self.chat.outgoing_calls, 0)
        self.app.bindings = lambda: {**proof, "reverse": {}}
        held = call("703")
        self.assertEqual((held["status"], held["reason"]),
                         ("retained", "native_identity_unverified"))
        self.assertEqual(self.chat.incoming_calls, 1)
        self.app.bindings = lambda: {**proof, "record_id": "0" * 64}
        self.assertEqual(call("704")["reason"], "native_identity_unverified")

    def test_reservation_rejects_wrong_chatwoot_ids(self):
        row = self.app.receive(self.event(KNOWN))
        submitted = {k: row[k] for k in ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")}
        with self.assertRaises(ReservationError):
            self.planner.reserve({**submitted, "conversation": row["conversation"] + 1})
        with self.planner.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_ambiguous_identity_is_held_without_reservation(self):
        self.directory = {"data": [self.directory["data"][0],
            {"Codigo": "7102", "Cli_Celular": "+55 11 99000-0001"}],
            "recordsFiltered": 2, "recordsTotal": 2}
        row = self.app.receive(self.event(KNOWN))
        self.assertEqual((row["state"], row["reason"]), ("held", "ambiguous_customer_match"))
        self.assertEqual(self.planner.reserve({k: row[k] for k in
            ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")})["status"], "held")

    def test_chatwoot_v419_contact_shape_and_identifier(self):
        api = Chatwoot(12, 22, "fixturechannel", "test-token")
        calls = []
        def fake(method, path, body):
            calls.append((method, path, body))
            return {"payload": {"contact": {"id": 40, "identifier": body["identifier"]}}}
        api.request = fake
        self.assertEqual(api.contact(digest(KNOWN.encode()))["id"], 40)
        self.assertEqual(calls[0][2]["name"], "Contato a conferir")


if __name__ == "__main__":
    unittest.main()
