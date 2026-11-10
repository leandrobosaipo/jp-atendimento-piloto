"""Synthetic encrypted journal readback; no production key, socket or send."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bridge import Held
from personal import INSTANCE
from personal_media_journal import JournalProofReader, request_from_proof


JID = "5511900000000@s.whatsapp.net"
LID = "100000001@lid"
NATIVE = "inbound-001"
TEXT = "Mensagem fictícia"
OPENED = "2026-11-10T12:00:00Z"
RECEIVED = "2026-11-10T12:00:02Z"
INSTANCE_ID = "synthetic-instance-id"
INSTANCE_ID_HASH = hashlib.sha256(INSTANCE_ID.encode()).hexdigest()


def b64(value):
    return base64.b64encode(value).decode()


class JournalTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()
        self.journal = self.folder / "retidos.sqlite3"
        self.keyfile = self.folder / "custodia-journal.pem"
        self.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keyfile.write_bytes(self.rsa_key.private_bytes(serialization.Encoding.PEM,
                                 serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.keyfile.chmod(0o600)
        self.aes_key = os.urandom(32)
        self.db = sqlite3.connect(self.journal)
        self.addCleanup(self.db.close)
        self.db.executescript("""CREATE TABLE records(source TEXT,file TEXT,digest TEXT,sealed BLOB,
            PRIMARY KEY(source,file)); CREATE TABLE checkpoints(cutoff TEXT,source TEXT,manifest TEXT,
            wrapped TEXT,total INTEGER, PRIMARY KEY(cutoff,source));""")
        self._checkpoint(self.aes_key, RECEIVED)
        self._checkpoint(os.urandom(32), "2026-11-10T12:00:03Z")  # rotated key cannot decrypt this record
        self.record_id = self._record()
        self.journal.chmod(0o600)

    def _checkpoint(self, secret, cutoff):
        wrapped = self.rsa_key.public_key().encrypt(secret, padding.OAEP(
            padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))
        self.db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?)", (cutoff, "evolution", "fixture", b64(wrapped), 1))
        self.db.commit()

    def _record(self, *, remote=JID, alt=None, from_me=False, timestamp=None, content=TEXT):
        stamp = int(datetime(2026, 11, 10, 12, 0, 1, tzinfo=timezone.utc).timestamp()) if timestamp is None else timestamp
        key = {"id": NATIVE, "fromMe": from_me, "remoteJid": remote}
        if alt is not None:
            key["remoteJidAlt"] = alt
        body = {"instance": INSTANCE, "event": "MESSAGES_UPSERT", "data": {"key": key,
                "messageTimestamp": stamp, "message": {"conversation": content}}}
        return self._seal(body)

    def _seal(self, body):
        raw = json.dumps(body, separators=(",", ":")).encode()
        meta = {"version": 1, "source": "evolution", "instance": INSTANCE, "receivedAt": RECEIVED,
                "rawSha256": hashlib.sha256(raw).hexdigest(), "workflowId": None, "executionId": None}
        iv = os.urandom(12)
        cipher = AESGCM(self.aes_key).encrypt(iv, raw, json.dumps(meta, separators=(",", ":")).encode())
        sealed = json.dumps({"version": 1, "meta": meta, "iv": b64(iv),
                             "ciphertext": b64(cipher[:-16]), "tag": b64(cipher[-16:])}, separators=(",", ":")).encode()
        instance = INSTANCE.encode()
        record_id = hashlib.sha256(struct.pack(">I", len(instance)) + instance + raw).hexdigest()
        self.db.execute("DELETE FROM records")
        self.db.execute("INSERT INTO records VALUES(?,?,?,?)",
                        ("evolution", record_id + ".json", hashlib.sha256(sealed).hexdigest(), sealed))
        self.db.commit()
        return record_id

    def _ack(self, *, status="DELIVERY_ACK", from_me=True, jid=LID, instance_id=INSTANCE_ID):
        return self._seal({"instance": INSTANCE, "event": "MESSAGES_UPDATE", "data": {
            "keyId": NATIVE, "fromMe": from_me, "status": status,
            "remoteJid": jid, "instanceId": instance_id}})

    def reader(self, mapping=None, opened_at=OPENED):
        return JournalProofReader(self.journal, self.keyfile, expected_jid=JID,
                                  opened_at=opened_at, expected_instance_id_hash=INSTANCE_ID_HASH,
                                  mapping=mapping)

    def test_read_only_aead_proof_request_and_stable_uuid(self):
        self._checkpoint(self.aes_key, "2026-11-10T12:00:04Z")  # OAEP rewrap of the same valid key
        before = self.journal.read_bytes()
        native_hash = hashlib.sha256(NATIVE.encode()).hexdigest()
        proof = self.reader().read_inbound(self.record_id, native_hash, TEXT)
        self.assertEqual(proof["sender"], JID)
        self.assertEqual(proof["native_id"], NATIVE)
        body, request = request_from_proof(proof, native_hash)
        self.assertEqual(body, {"op": "personal-ingress", "record_id": self.record_id,
                                "native_id_sha256": native_hash, "text": TEXT})
        self.assertEqual(request, str(uuid.uuid5(uuid.NAMESPACE_URL, "jp-personal-inbound:" + native_hash)))
        summary = self.reader().proof_only(self.record_id, native_hash, TEXT)
        self.assertTrue(summary["journal_verified"])
        self.assertNotIn(TEXT, json.dumps(summary))
        self.assertEqual(self.journal.read_bytes(), before)

    def test_wrong_id_text_timing_direction_and_ciphertext_are_held(self):
        native_hash = hashlib.sha256(NATIVE.encode()).hexdigest()
        for record_id, id_hash, text, opened in [
            ("0" * 64, native_hash, TEXT, OPENED),
            (self.record_id, "0" * 64, TEXT, OPENED),
            (self.record_id, native_hash, "different", OPENED),
            (self.record_id, native_hash, TEXT, RECEIVED),
        ]:
            with self.assertRaises(Held):
                self.reader(opened_at=opened).read_inbound(record_id, id_hash, text)
        self.record_id = self._record(from_me=True)
        with self.assertRaises(Held):
            self.reader().read_inbound(self.record_id, native_hash, TEXT)
        self.record_id = self._record(timestamp=int(datetime(2026, 11, 10, 11, 59, 59,
                                                            tzinfo=timezone.utc).timestamp()))
        with self.assertRaises(Held):
            self.reader().read_inbound(self.record_id, native_hash, TEXT)
        self.db.execute("UPDATE records SET digest='bad'")
        self.db.commit()
        with self.assertRaises(Held):
            self.reader().read_inbound(self.record_id, native_hash, TEXT)

    def test_two_chat_identifiers_require_bidirectional_mapping(self):
        self.record_id = self._record(remote=LID, alt=JID)
        native_hash = hashlib.sha256(NATIVE.encode()).hexdigest()
        with self.assertRaises(Held):
            self.reader().read_inbound(self.record_id, native_hash, TEXT)
        phone_hash = hashlib.sha256(JID.split("@")[0].encode()).hexdigest()
        mapping = {"instance_id_hash": INSTANCE_ID_HASH, "pn": phone_hash, "reverse_pn": phone_hash,
                   "forward_lid": LID, "reverse_lid": LID}
        self.assertEqual(self.reader(mapping).read_inbound(self.record_id, native_hash, TEXT)["sender"], JID)
        for field in ("pn", "reverse_pn", "forward_lid", "reverse_lid", "instance_id_hash"):
            with self.assertRaises(Held):
                self.reader({**mapping, field: "wrong"}).read_inbound(self.record_id, native_hash, TEXT)

    def test_delivery_ack_requires_aead_identity_mapping_and_post_claim_time(self):
        record = self._ack()
        native_hash = hashlib.sha256(NATIVE.encode()).hexdigest()
        phone_hash = hashlib.sha256(JID.split("@")[0].encode()).hexdigest()
        lid_hash = hashlib.sha256(LID.encode()).hexdigest()
        mapping = {"instance_id_hash": INSTANCE_ID_HASH, "pn": phone_hash,
                   "reverse_pn": phone_hash, "forward_lid": lid_hash, "reverse_lid": lid_hash}
        witness = self.reader().read_delivery_ack(record, native_hash,
            claimed_at=OPENED, mapping=mapping, position=1)
        self.assertEqual(witness["receipt"]["keyId"], NATIVE)
        self.assertEqual(witness["receipt"]["status"], "DELIVERY_ACK")
        for field in mapping:
            with self.assertRaises(Held):
                self.reader().read_delivery_ack(record, native_hash, claimed_at=OPENED,
                                                mapping={**mapping, field: "wrong"}, position=1)
        for claimed, id_hash in ((RECEIVED, native_hash), (OPENED, "0" * 64)):
            with self.assertRaises(Held):
                self.reader().read_delivery_ack(record, id_hash, claimed_at=claimed,
                                                mapping=mapping, position=1)
        for change in ({"status": "PENDING"}, {"from_me": False}, {"jid": JID},
                       {"instance_id": "other"}):
            bad = self._ack(**change)
            with self.assertRaises(Held):
                self.reader().read_delivery_ack(bad, native_hash, claimed_at=OPENED,
                                                mapping=mapping, position=1)


if __name__ == "__main__":
    unittest.main()
