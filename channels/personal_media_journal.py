"""Read-only authenticated inbound proof from the restored Evolution journal.

No socket, sender, scheduler or command-line entry point. The root-only journal
and key paths are supplied explicitly by the authorized runner.
"""
import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from bridge import need
from personal import INSTANCE
from personal_media import DIRECT, HEX, NATIVE_ID, _instant


def _private_file(path, *, read=True):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1 and
             stat.S_IMODE(st.st_mode) == 0o600, "private_journal_file_required")
        if not read:
            return st, None
        with os.fdopen(os.dup(fd), "rb") as stream:
            return st, stream.read()
    finally:
        os.close(fd)


def _b64(value):
    need(isinstance(value, str), "invalid_cipher_encoding")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        need(False, "invalid_cipher_encoding")


def request_from_proof(proof, native_id_sha256):
    """Build only the panel request object; never opens panel.sock."""
    need(isinstance(proof, dict) and proof.get("authenticated") is True and
         proof.get("source") == "authenticated_mac_collector" and
         isinstance(proof.get("record_id"), str) and HEX.fullmatch(proof["record_id"]) and
         isinstance(native_id_sha256, str) and HEX.fullmatch(native_id_sha256) and
         isinstance(proof.get("native_id"), str) and
         hashlib.sha256(proof["native_id"].encode()).hexdigest() == native_id_sha256 and
         isinstance(proof.get("text"), str), "unverified_panel_request")
    body = {"op": "personal-ingress", "record_id": proof["record_id"],
            "native_id_sha256": native_id_sha256, "text": proof["text"]}
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "jp-personal-inbound:" + native_id_sha256))
    return body, request_id


class JournalProofReader:
    """Verify one record against every applicable wrapped key in a read snapshot."""

    def __init__(self, journal_path, key_path, *, expected_jid, opened_at,
                 expected_instance_id_hash, mapping=None):
        journal_path, key_path = Path(journal_path).absolute(), Path(key_path).absolute()
        need(journal_path.parent == key_path.parent, "journal_key_not_colocated")
        directory = journal_path.parent
        info = directory.lstat()
        need(stat.S_ISDIR(info.st_mode) and not directory.is_symlink() and
             stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid(),
             "private_journal_directory_required")
        need(journal_path.name == "retidos.sqlite3" and key_path.name == "custodia-journal.pem",
             "journal_identity_invalid")
        need(isinstance(expected_jid, str) and DIRECT.fullmatch(expected_jid) and
             isinstance(expected_instance_id_hash, str) and HEX.fullmatch(expected_instance_id_hash),
             "personal_mapping_identity_required")
        self.journal_path, self.key_path = journal_path, key_path
        self.expected_jid = expected_jid
        self.opened_at = _instant(opened_at)
        self.expected_instance_id_hash = expected_instance_id_hash
        self.mapping = mapping

    def _sender(self, data, key):
        ids = {value for value in (key.get("remoteJid"), key.get("remoteJidAlt"),
                                  data.get("remoteJidAlt")) if isinstance(value, str) and value}
        need(ids, "inbound_sender_missing")
        if ids == {self.expected_jid}:
            return self.expected_jid
        mapping = self.mapping
        need(isinstance(mapping, dict) and mapping.get("instance_id_hash") == self.expected_instance_id_hash,
             "bidirectional_mapping_required")
        phone_hash = hashlib.sha256(self.expected_jid.split("@")[0].encode()).hexdigest()
        lid = mapping.get("forward_lid")
        need(isinstance(lid, str) and lid.endswith("@lid") and
             mapping.get("reverse_lid") == lid and mapping.get("pn") == mapping.get("reverse_pn") == phone_hash and
             lid in ids and ids.issubset({self.expected_jid, lid}), "bidirectional_mapping_mismatch")
        return self.expected_jid

    def read_inbound(self, record_id, native_id_sha256, text):
        need(isinstance(record_id, str) and HEX.fullmatch(record_id) and
             isinstance(native_id_sha256, str) and HEX.fullmatch(native_id_sha256) and
             isinstance(text, str) and 0 < len(text) <= 4000, "inbound_query_invalid")
        _private_file(self.journal_path, read=False)
        _, key_bytes = _private_file(self.key_path)
        try:
            private_key = serialization.load_pem_private_key(key_bytes, password=None)
        except (ValueError, TypeError):
            need(False, "journal_key_invalid")
        db = sqlite3.connect(self.journal_path.as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            rows = db.execute("SELECT digest,sealed FROM records WHERE source='evolution' AND file=?",
                              (record_id + ".json",)).fetchall()
            need(len(rows) == 1, "inbound_record_not_unique")
            digest, sealed = rows[0]
            need(type(sealed) is bytes and hashlib.sha256(sealed).hexdigest() == digest,
                 "journal_envelope_digest_mismatch")
            wrapped = [row[0] for row in db.execute("SELECT DISTINCT wrapped FROM checkpoints WHERE source='evolution'")]
            need(wrapped, "journal_checkpoint_missing")
            envelope = json.loads(sealed)
            meta = envelope.get("meta")
            need(isinstance(meta, dict) and meta.get("version") == 1 and meta.get("source") == "evolution" and
                 meta.get("instance") == INSTANCE and meta.get("workflowId") is None and
                 meta.get("executionId") is None and isinstance(meta.get("rawSha256"), str) and
                 HEX.fullmatch(meta["rawSha256"]), "journal_envelope_scope_mismatch")
            received = _instant(meta.get("receivedAt"))
            need(received > self.opened_at, "inbound_before_test_open")
            aad = json.dumps(meta, separators=(",", ":"), ensure_ascii=False).encode()
            iv, ciphertext, tag = (_b64(envelope.get(name)) for name in ("iv", "ciphertext", "tag"))
            need(len(iv) == 12 and len(tag) == 16, "journal_cipher_shape_invalid")
            valid_keys = set()
            plaintext = None
            for value in wrapped:
                try:
                    secret = private_key.decrypt(_b64(value), padding.OAEP(
                        padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))
                    need(len(secret) == 32, "journal_key_shape_invalid")
                    candidate = AESGCM(secret).decrypt(iv, ciphertext + tag, aad)
                    valid_keys.add(secret)
                    plaintext = candidate
                except (ValueError, TypeError, InvalidTag):
                    continue  # Different checkpoint key; AEAD chooses the record's key.
            need(len(valid_keys) == 1, "journal_aead_ambiguous_or_unavailable")
            raw = plaintext
            instance = INSTANCE.encode()
            need(hashlib.sha256(raw).hexdigest() == meta["rawSha256"] and
                 hashlib.sha256(struct.pack(">I", len(instance)) + instance + raw).hexdigest() == record_id,
                 "journal_raw_integrity_mismatch")
            body = json.loads(raw)
            envelopes = body if isinstance(body, list) else [body]
            need(envelopes and all(isinstance(item, dict) and item.get("instance") == INSTANCE for item in envelopes),
                 "journal_body_instance_mismatch")
            matches = []
            for item in envelopes:
                need(str(item.get("event", "")).lower().replace("_", ".") == "messages.upsert",
                     "journal_event_not_upsert")
                data = item.get("data")
                for message in data if isinstance(data, list) else [data]:
                    if not isinstance(message, dict) or not isinstance(message.get("key"), dict):
                        continue
                    key = message["key"]
                    native = key.get("id")
                    if isinstance(native, str) and hashlib.sha256(native.encode()).hexdigest() == native_id_sha256:
                        matches.append((message, key, native))
            need(len(matches) == 1, "native_inbound_id_not_unique")
            data, native_key, native_id = matches[0]
            need(NATIVE_ID.fullmatch(native_id) and native_key.get("fromMe") is False and
                 not native_key.get("participant"), "inbound_direction_or_id_invalid")
            sender = self._sender(data, native_key)
            message = data.get("message")
            need(isinstance(message, dict), "inbound_message_missing")
            content = message.get("conversation")
            if content is None and isinstance(message.get("extendedTextMessage"), dict):
                content = message["extendedTextMessage"].get("text")
            need(isinstance(content, str) and content == text, "inbound_text_mismatch")
            stamp = data.get("messageTimestamp")
            need(type(stamp) in (int, float, str) and str(stamp).replace(".", "", 1).isdigit(),
                 "native_timestamp_required")
            value = float(stamp)
            if value > 1e12:
                value /= 1000
            native_at = datetime.fromtimestamp(value, timezone.utc)
            need(native_at > self.opened_at and native_at <= received + timedelta(seconds=60),
                 "native_before_test_or_after_receipt")
            db.rollback()  # Ends only the read transaction; query_only prevents writes.
        finally:
            db.close()
        return {"authenticated": True, "source": "authenticated_mac_collector", "instance": INSTANCE,
                "event": "messages.upsert", "fromMe": False, "sender": sender,
                "native_id": native_id, "record_id": record_id, "received_at": meta["receivedAt"], "text": content}

    def proof_only(self, record_id, native_id_sha256, text):
        proof = self.read_inbound(record_id, native_id_sha256, text)
        _, request_id = request_from_proof(proof, native_id_sha256)
        return {"journal_verified": True, "personal_sender_verified": True,
                "post_open_verified": True, "record_id": record_id,
                "native_id_sha256": native_id_sha256, "request": request_id}
