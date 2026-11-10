"""The approved two-part plan stops before every transport."""
import base64
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from customer_ingress import Intake, receipt_id
from customer_planner import PlannerReservation
from customer_plan import HeldPlanner, PlanHeld
from test_customer_ingress import FakeChatwoot, INSTANCE, KNOWN, NEW


class TestHeldPlan(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)
        self.chat = FakeChatwoot()
        directory = {"data": [{"Codigo": "7101", "Cli_Celular": "+55 11 99000-0001",
                               "PRe_Ativo": "1"}], "recordsFiltered": 1, "recordsTotal": 1}
        lookup = lambda numbers: [{"number": numbers[0], "exists": True, "jid": KNOWN}] if numbers else []
        self.app = Intake(root / "intake.sqlite3", {"instance": INSTANCE, "workflow": "fixture-wf",
            "account": 12, "inbox": 22, "channel": "fixturechannel", "fixture_account": 1,
            "review_inbox": 3}, self.chat, lambda: directory, lookup)
        self.reserve = PlannerReservation(root / "reservations.sqlite3", root / "intake.sqlite3", self.chat)
        images = {}
        artworks = []
        for group in ("servicos", "clube-jp"):
            for number in range(1, 4):
                ident = f"{group}-{number:02d}"
                image = b"\xff\xd8" + ident.encode() + b"\xff\xd9"
                filename = ident + ".jpg"
                images[filename] = image
                artworks.append({"id": ident, "filename": filename, "group": group,
                                 "approved": True, "bytes": len(image),
                                 "sha256": hashlib.sha256(image).hexdigest()})
        manifest = json.dumps({"assets": artworks, "price_source_policy": "pending_user_resolution",
                               "club_plans": [{"name": "Plano fictício", "visits_per_month": 1,
                                               "price_cents": 10000}]}).encode()
        self.plan = HeldPlanner(root / "plans.sqlite3", root / "reservations.sqlite3", self.chat,
            manifest_bytes=manifest, manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            asset_bytes=lambda filename: images[filename])
        self.artworks = {a["id"]: a for a in artworks}

    def inbound(self, sender, native, text="Mostre a tabela de serviços"):
        raw = json.dumps({"instance": INSTANCE, "event": "messages.upsert", "data": {
            "key": {"id": native, "remoteJid": sender, "fromMe": False},
            "message": {"conversation": text}}}, separators=(",", ":")).encode()
        body = {"raw_base64": base64.b64encode(raw).decode(), "record_id": receipt_id(INSTANCE, raw),
                "workflow_id": "fixture-wf", "execution_id": "101"}
        row = self.app.receive(body)
        reservation = self.reserve.reserve({k: row[k] for k in
            ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")})
        return reservation

    def query(self, reservation):
        return {k: reservation[k] for k in
            ("reservation", "request", "account", "inbox", "contact", "conversation", "message", "mode")}

    def test_public_and_identified_share_exact_chatwoot_identity_and_two_held_parts(self):
        for sender, native, mode in ((NEW, "FIXTURENEW001", "public_information"),
                                     (KNOWN, "FIXTUREKNOWN001", "identified_customer")):
            receipt = self.inbound(sender, native)
            plan = self.plan.prepare(self.query(receipt))
            self.assertEqual((plan["mode"], plan["status"], plan["transport_attempts"]),
                             (mode, "prepared_held", 0))
            self.assertEqual((plan["account"], plan["inbox"], plan["contact"],
                              plan["conversation"], plan["message"]),
                             (receipt["account"], receipt["inbox"], receipt["contact"],
                              receipt["conversation"], receipt["message"]))
            self.assertEqual([x["state"] for x in plan["parts"]], ["held_before_transport"] * 2)
            chosen = self.artworks[plan["parts"][1]["asset_id"]]
            self.assertEqual(plan["parts"][1]["asset_sha256"], chosen["sha256"])
            self.assertEqual(chosen["group"], "servicos")
            self.assertEqual(self.plan.prepare(self.query(receipt)), plan)
        with self.plan.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM held_parts").fetchone()[0], 4)
        self.assertEqual(self.chat.outgoing_calls, 0)

    def test_variants_are_distributed_by_reservation_and_replay_is_stable(self):
        selected = set()
        for number in range(12):
            reservation = self.inbound(KNOWN, f"VARIANT{number:03d}")
            result = self.plan.prepare(self.query(reservation))
            selected.add(result["parts"][1]["asset_id"])
            self.assertEqual(self.plan.prepare(self.query(reservation)), result)
        self.assertEqual(selected, {"servicos-01", "servicos-02", "servicos-03"})
        self.assertEqual(self.chat.outgoing_calls, 0)

    def test_club_package_uses_approved_variant_and_stays_held(self):
        reservation = self.inbound(NEW, "PACKAGE001", "Quero conhecer o Clube JP")
        result = self.plan.prepare(self.query(reservation))
        self.assertEqual((result["status"], result["transport_attempts"]), ("prepared_held", 0))
        chosen = self.artworks[result["parts"][1]["asset_id"]]
        self.assertEqual(chosen["group"], "clube-jp")
        self.assertEqual(result["parts"][1]["asset_sha256"], chosen["sha256"])
        self.assertEqual(self.plan.prepare(self.query(reservation)), result)

    def test_prior_persisted_variant_is_preserved_after_policy_upgrade(self):
        reservation = self.inbound(KNOWN, "PRIORART001")
        first = self.plan.prepare(self.query(reservation))
        old = self.artworks["servicos-03"]
        with self.plan.db() as db:
            db.execute("UPDATE held_parts SET asset_id=?,asset_sha256=? WHERE reservation=? AND position=2",
                       (old["id"], old["sha256"], reservation["reservation"]))
        again = self.plan.prepare(self.query(reservation))
        self.assertEqual((again["parts"][1]["asset_id"], again["parts"][1]["asset_sha256"]),
                         (old["id"], old["sha256"]))
        self.assertEqual(again["parts"][0]["part_id"], first["parts"][0]["part_id"])

    def test_human_claim_between_parts_blocks_both(self):
        reservation = self.inbound(KNOWN, "FIXTUREKNOWN002")
        original = self.chat.conversation_readback
        calls = []
        def switch(conv):
            calls.append(conv)
            if len(calls) == 2:
                self.chat.assigned = True
            return original(conv)
        self.chat.conversation_readback = switch
        with self.assertRaisesRegex(PlanHeld, "human_ownership"):
            self.plan.prepare(self.query(reservation))
        with self.plan.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM held_parts").fetchone()[0], 0)

    def test_wrong_reservation_or_inbound_context_never_prepares(self):
        reservation = self.inbound(KNOWN, "FIXTUREKNOWN003")
        with self.assertRaises(PlanHeld):
            self.plan.prepare({**self.query(reservation), "conversation": reservation["conversation"] + 1})
        with self.assertRaises(PlanHeld):
            self.plan.prepare({**self.query(reservation), "reservation": "00000000-0000-5000-8000-000000000000"})

    def test_nonpublic_or_personal_lookup_request_is_held(self):
        ordinary = self.inbound(NEW, "FIXTURENEW004", "Mensagem fictícia de ensaio")
        self.assertEqual(self.plan.prepare(self.query(ordinary))["status"], "held")
        price = self.inbound(KNOWN, "FIXTUREKNOWN004", "Quanto custa manutenção?")
        self.assertEqual(self.plan.prepare(self.query(price))["status"], "held")
        with self.plan.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM held_parts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
