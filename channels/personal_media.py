"""Offline personal JPEG transport boundary for an ordered Outbox rehearsal.

All I/O-capable collaborators are injected. Importing this module creates no
client, socket, listener, secret or route. A caller must verify the recipient,
manifest and collector witness before using it outside a fixture.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone

from bridge import Event, canonical, digest, need
from personal import INSTANCE, Personal


HEX = re.compile(r"[a-f0-9]{64}")
NATIVE_ID = re.compile(r"[A-Za-z0-9_-]{6,160}")
DIRECT = re.compile(r"[0-9]{8,16}@s\.whatsapp\.net")
GROUPS = {"servicos": "servicos", "clube-jp": "clube"}


def approved_manifest(manifest_bytes, manifest_sha256):
    """Verify the six private entries against an independently pinned digest."""
    need(isinstance(manifest_bytes, bytes) and isinstance(manifest_sha256, str) and
         HEX.fullmatch(manifest_sha256) and
         hashlib.sha256(manifest_bytes).hexdigest() == manifest_sha256, "manifest_hash_mismatch")
    try:
        catalog = json.loads(manifest_bytes)
    except (ValueError, UnicodeError):
        need(False, "invalid_manifest")
    need(isinstance(catalog, dict) and catalog.get("price_source_policy") == "artwork" and
         isinstance(catalog.get("assets"), list) and len(catalog["assets"]) == 6,
         "six_approved_artworks_required")
    assets = catalog["assets"]
    need(sorted(a.get("group") for a in assets if isinstance(a, dict)) == ["clube-jp"] * 3 + ["servicos"] * 3,
         "artwork_groups_invalid")
    ids, names = set(), set()
    for asset in assets:
        name = asset.get("filename")
        need(asset.get("approved") is True and isinstance(asset.get("id"), str) and
             re.fullmatch(r"[A-Za-z0-9_-]{1,80}", asset["id"]) and
             isinstance(name, str) and name not in (".", "..") and Path(name).name == name and
             name.lower().endswith((".jpg", ".jpeg")) and
             isinstance(asset.get("sha256"), str) and HEX.fullmatch(asset["sha256"]) and
             type(asset.get("bytes")) is int and 4 <= asset["bytes"] <= 1_500_000,
             "invalid_approved_artwork")
        ids.add(asset["id"]); names.add(name)
    need(len(ids) == len(names) == 6, "duplicate_artwork_identity")
    return assets


class PersonalMedia:
    """Personal-only transport, with exact artwork and receipt verification."""

    def __init__(self, phone, native, *, manifest_bytes, manifest_sha256,
                 asset_bytes, read_native, collector_instance_id_hash):
        self.personal = Personal(phone, native)
        need(callable(native) and callable(asset_bytes) and callable(read_native), "injected_io_required")
        need(isinstance(collector_instance_id_hash, str) and HEX.fullmatch(collector_instance_id_hash),
             "collector_identity_required")
        assets = approved_manifest(manifest_bytes, manifest_sha256)
        self.assets = {a["id"]: a for a in assets}
        self.asset_bytes = asset_bytes
        self.read_native = read_native
        self.native = native
        self.collector_instance_id_hash = collector_instance_id_hash
        self.verified = False
        self._claimed_request_sha256 = None

    @property
    def jid(self):
        return self.personal.jid

    def verify_recipient(self):
        self.personal.verify_recipient()
        need(DIRECT.fullmatch(self.personal.jid), "personal_direct_jid_required")
        if hasattr(self.native, "bind_recipient"):
            self.native.bind_recipient(self.personal.jid)
        self.verified = True

    def event(self, native_id, text):
        need(self.verified and isinstance(native_id, str) and NATIVE_ID.fullmatch(native_id),
             "verified_personal_event_required")
        return Event("whatsapp", INSTANCE, self.jid, native_id, text).validate()

    def manifest_assets(self):
        """Project the six private catalog entries into Outbox's immutable plan."""
        return [dict(id=a["id"], group=GROUPS[a["group"]], sha256=a["sha256"],
                     filename=a["filename"], mime="image/jpeg") for a in self.assets.values()]

    def prepare(self, box, event, texts, *, image_group=None, choose=None):
        need(self.verified and event.channel == "whatsapp" and event.account == INSTANCE and
             event.sender == self.jid and DIRECT.fullmatch(event.sender), "outside_personal_pin")
        need(image_group in (None, "servicos", "clube"), "invalid_image_group")
        key = box.receive(event)
        args = dict(image_group=image_group, assets=self.manifest_assets())
        if choose is not None:
            args["choose"] = choose
        return key, box.prepare(key, texts, **args)

    def _scope(self, event, payload):
        need(self.verified and event.get("channel") == "whatsapp" and event.get("account") == INSTANCE and
             event.get("sender") == self.jid and DIRECT.fullmatch(self.jid) and
             isinstance(payload, dict), "outside_personal_pin")

    def _artwork(self, payload):
        asset = payload.get("asset")
        need(isinstance(asset, dict) and set(asset) == {"id", "group", "sha256", "filename", "mime"},
             "asset_plan_invalid")
        approved = self.assets.get(asset["id"])
        need(approved is not None and asset == dict(id=approved["id"], group=GROUPS[approved["group"]],
             sha256=approved["sha256"], filename=approved["filename"], mime="image/jpeg"),
             "asset_not_approved")
        raw = self.asset_bytes(asset["id"])
        need(type(raw) is bytes and len(raw) == approved["bytes"] and raw.startswith(b"\xff\xd8") and
             raw.endswith(b"\xff\xd9") and hashlib.sha256(raw).hexdigest() == approved["sha256"],
             "artwork_bytes_mismatch")
        return raw

    def request_intent(self, event, payload):
        """Build and hash the exact allowlisted native request before a claim."""
        self._scope(event, payload)
        if payload.get("kind") == "text":
            text = payload.get("text")
            need(isinstance(text, str) and 0 < len(text) <= 250, "invalid_text_part")
            route, body = "message/sendText", {"number": self.jid.split("@")[0], "text": text,
                                               "linkPreview": False}
        elif payload.get("kind") == "image":
            asset = payload.get("asset"); raw = self._artwork(payload)
            route, body = "message/sendMedia", {"number": self.jid.split("@")[0],
                "mediatype": "image", "mimetype": "image/jpeg", "caption": "Tabela",
                "fileName": asset["filename"], "media": base64.b64encode(raw).decode("ascii"),
                "linkPreview": False}
        else:
            need(False, "unsupported_part")
        raw = json.dumps(body, separators=(",", ":")).encode()
        return route, body, hashlib.sha256(route.encode() + b"\0" + raw).hexdigest()

    def send(self, event, payload):
        """Return native acceptance only; Outbox awaits a separate delivery proof."""
        route, body, intent_sha256 = self.request_intent(event, payload)
        need(intent_sha256 == self._claimed_request_sha256, "unclaimed_native_request")
        result = self.native(route, body)
        key = result.get("key", {}) if isinstance(result, dict) else {}
        need(key.get("fromMe") is True and key.get("remoteJid") == self.jid and
             isinstance(key.get("id"), str) and NATIVE_ID.fullmatch(key["id"]),
             "native_acceptance_unproved")
        if hasattr(self.native, "bind_native_id"):
            self.native.bind_native_id(key["id"])
        return {"id": key["id"], "destination": self.jid, "account": INSTANCE,
                "request_sha256": intent_sha256}

    def _native_content(self, native_id, payload):
        saved = self.read_native(native_id)
        need(isinstance(saved, dict) and saved.get("id") == native_id and
             saved.get("instance") == INSTANCE and saved.get("destination") == self.jid and
             saved.get("fromMe") is True, "native_readback_identity_mismatch")
        if payload["kind"] == "text":
            need(saved.get("text") == payload["text"], "native_text_mismatch")
        else:
            raw = self._artwork(payload)
            need(saved.get("mime") == "image/jpeg" and saved.get("filename") == payload["asset"]["filename"] and
                 saved.get("caption") == "Tabela" and type(saved.get("bytes")) is bytes and
                 hashlib.sha256(saved["bytes"]).hexdigest() == hashlib.sha256(raw).hexdigest(),
                 "native_media_mismatch")

    def proof(self, event, native_id, payload, witness, *, accepted_intent_sha256=None):
        """A trusted caller supplies an authenticated collector receipt and PN/LID readback."""
        self._scope(event, payload)
        need(isinstance(witness, dict) and witness.get("authenticated") is True and
             witness.get("source") == "authenticated_mac_collector", "collector_auth_required")
        receipt, mapping = witness.get("receipt"), witness.get("mapping")
        need(isinstance(receipt, dict) and isinstance(mapping, dict) and
             receipt.get("instance") == INSTANCE and receipt.get("event") == "messages.update" and
             receipt.get("keyId") == native_id and receipt.get("fromMe") is True and
             receipt.get("status") in ("DELIVERY_ACK", "READ") and
             isinstance(receipt.get("record_id"), str) and HEX.fullmatch(receipt["record_id"]) and
             isinstance(receipt.get("at"), str) and receipt["at"] and
             receipt.get("instance_id_hash") == self.collector_instance_id_hash,
             "collector_receipt_unbound")
        phone_hash = hashlib.sha256(self.jid.split("@")[0].encode()).hexdigest()
        lid = receipt.get("lid")
        need(isinstance(lid, str) and lid and mapping.get("instance_id_hash") == self.collector_instance_id_hash and
             mapping.get("pn") == mapping.get("reverse_pn") == phone_hash and
             mapping.get("forward_lid") == mapping.get("reverse_lid") == lid,
             "collector_recipient_mismatch")
        if accepted_intent_sha256 is None:
            self._native_content(native_id, payload)
            content_basis = "independent_native_readback"
        else:
            need(isinstance(accepted_intent_sha256, str) and HEX.fullmatch(accepted_intent_sha256) and
                 self.request_intent(event, payload)[2] == accepted_intent_sha256,
                 "accepted_intent_content_mismatch")
            content_basis = "durable_intent_and_native_http_acceptance"
        return {"id": native_id, "account": INSTANCE, "destination": self.jid,
                "payload_sha256": digest(payload), "status": "read" if receipt["status"] == "READ" else "delivered",
                "source": "authenticated_mac_collector", "collector_record_id": receipt["record_id"],
                "received_at": receipt["at"], "content_basis": content_basis}

    def confirm_part(self, box, event_key, position, witness, *, accepted_intent_sha256=None):
        need(type(position) is int and position > 0 and isinstance(witness, dict) and
             witness.get("position") == position,
             "part_position_required")
        record_id = (witness.get("receipt") or {}).get("record_id") if isinstance(witness.get("receipt"), dict) else None
        need(isinstance(record_id, str) and HEX.fullmatch(record_id), "collector_record_required")
        with box.db() as db:
            accepted = db.execute("SELECT receipt FROM channel_parts WHERE event_key=? AND position=? AND state='accepted'",
                                  (event_key, position)).fetchone()
            if accepted_intent_sha256 is not None:
                need(accepted and json.loads(accepted["receipt"]).get("request_sha256") == accepted_intent_sha256,
                     "accepted_intent_not_bound")
            for row in db.execute("SELECT event_key,position,receipt FROM channel_parts WHERE state='delivered'"):
                prior = json.loads(row["receipt"])
                need(prior.get("collector_record_id") != record_id or
                     (row["event_key"], row["position"]) == (event_key, position),
                     "collector_record_reused")
        return box.confirm(event_key, position, lambda event, native, payload:
                           self.proof(event, native, payload, witness,
                                      accepted_intent_sha256=accepted_intent_sha256))


def claim_and_advance(box, event_key, transport, claims_dir):
    """Durably reserve one part before its only injected send attempt."""
    need(isinstance(transport, PersonalMedia) and isinstance(event_key, str) and HEX.fullmatch(event_key),
         "invalid_personal_claim")
    with box.db() as db:
        row = db.execute("SELECT event FROM channel_events WHERE event_key=?", (event_key,)).fetchone()
    need(row, "unknown_event")
    transport._scope(json.loads(row["event"]), {})
    parts = box.parts(event_key)
    need(parts, "unknown_event")
    pending = next((part for part in parts if part["state"] != "delivered"), None)
    if pending is None:
        return box.advance(event_key, transport)
    need(pending["state"] == "pending", "await_same_receipt_no_resend")
    _, _, intent_sha256 = transport.request_intent(json.loads(row["event"]), json.loads(pending["payload"]))
    directory = Path(claims_dir).absolute()
    need(not any(p.is_symlink() for p in (directory, *directory.parents)), "unsafe_claim_directory")
    info = directory.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
         stat.S_IMODE(info.st_mode) == 0o700, "private_claim_directory_required")
    name = f"{event_key}-{pending['position']}.claim"
    file = directory / name
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        raw = json.dumps({"event_sha256": event_key, "position": pending["position"],
                          "request_sha256": intent_sha256}, separators=(",", ":")).encode()
        while raw:
            written = os.write(fd, raw)
            need(written > 0, "claim_write_failed")
            raw = raw[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    transport._claimed_request_sha256 = intent_sha256
    try:
        return box.advance(event_key, transport)
    finally:
        transport._claimed_request_sha256 = None


def _instant(value):
    need(isinstance(value, str), "timestamp_required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        need(False, "timestamp_invalid")
    need(stamp.tzinfo is not None, "timestamp_timezone_required")
    return stamp.astimezone(timezone.utc)


class PersonalSequence:
    """Persistently bind a response plan to one authenticated post-open inbound."""

    def __init__(self, box, transport, claims_dir, *, opened_at, read_inbound):
        need(isinstance(transport, PersonalMedia) and transport.verified and callable(read_inbound),
             "personal_recipient_or_collector_unverified")
        self.box, self.transport, self.claims_dir = box, transport, claims_dir
        self.read_inbound = read_inbound
        self.opened_at = _instant(opened_at).isoformat()
        recipient_hash = hashlib.sha256(transport.jid.encode()).hexdigest()
        with box.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS personal_test_window
                (id INTEGER PRIMARY KEY CHECK(id=1), opened_at TEXT NOT NULL, recipient_hash TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS personal_inbound
                (event_key TEXT PRIMARY KEY, record_id TEXT NOT NULL UNIQUE,
                 source_sha256 TEXT NOT NULL, received_at TEXT NOT NULL)""")
            db.execute("INSERT OR IGNORE INTO personal_test_window VALUES(1,?,?)",
                       (self.opened_at, recipient_hash))
            saved = db.execute("SELECT opened_at,recipient_hash FROM personal_test_window WHERE id=1").fetchone()
            need(tuple(saved) == (self.opened_at, recipient_hash), "personal_window_changed")

    def start(self, inbound, texts, *, image_group=None, choose=None):
        need(isinstance(inbound, dict) and isinstance(inbound.get("record_id"), str) and
             HEX.fullmatch(inbound["record_id"]), "inbound_record_required")
        verified = self.read_inbound(inbound["record_id"])
        need(isinstance(verified, dict) and verified.get("record_id") == inbound["record_id"] and
             digest(verified) == digest(inbound), "inbound_readback_mismatch")
        inbound = verified
        need(isinstance(inbound, dict) and inbound.get("authenticated") is True and
             inbound.get("source") == "authenticated_mac_collector" and
             inbound.get("instance") == INSTANCE and inbound.get("event") in ("messages.upsert", "MESSAGES_UPSERT") and
             inbound.get("fromMe") is False and inbound.get("sender") == self.transport.jid and
             isinstance(inbound.get("record_id"), str) and HEX.fullmatch(inbound["record_id"]),
             "inbound_not_authenticated_personal")
        received = _instant(inbound.get("received_at"))
        need(received > _instant(self.opened_at), "inbound_before_test_open")
        event = self.transport.event(inbound.get("native_id"), inbound.get("text"))
        key = event.key
        fingerprint = digest(inbound)
        with self.box.db() as db:
            db.execute("INSERT OR IGNORE INTO personal_inbound VALUES(?,?,?,?)",
                       (key, inbound["record_id"], fingerprint, received.isoformat()))
            saved = db.execute("SELECT record_id,source_sha256,received_at FROM personal_inbound WHERE event_key=?", (key,)).fetchone()
            need(saved and tuple(saved) == (inbound["record_id"], fingerprint, received.isoformat()),
                 "inbound_identity_conflict")
        return self.transport.prepare(self.box, event, texts, image_group=image_group, choose=choose)

    def advance(self, event_key):
        with self.box.db() as db:
            bound = db.execute("SELECT 1 FROM personal_inbound WHERE event_key=?", (event_key,)).fetchone()
        need(bound, "authenticated_inbound_required")
        return claim_and_advance(self.box, event_key, self.transport, self.claims_dir)

    def confirm(self, event_key, position, witness):
        with self.box.db() as db:
            bound = db.execute("SELECT 1 FROM personal_inbound WHERE event_key=?", (event_key,)).fetchone()
        need(bound, "authenticated_inbound_required")
        parts = self.box.parts(event_key)
        need(type(position) is int and 0 < position <= len(parts), "part_position_required")
        part = parts[position - 1]
        if part["native_id"] and hasattr(self.transport.native, "bind_native_id"):
            self.transport.native.bind_native_id(json.loads(part["native_id"])[2])
        return self.transport.confirm_part(self.box, event_key, position, witness)

    def confirm_from_intent(self, event_key, position, witness):
        """Use a real collector ACK when the native store has no message readback.

        This proves accepted request content and technical delivery by ID; it
        does not claim a WhatsApp copy or that the image was viewed.
        """
        need(type(position) is int and position > 0, "part_position_required")
        with self.box.db() as db:
            bound = db.execute("SELECT 1 FROM personal_inbound WHERE event_key=?", (event_key,)).fetchone()
            row = db.execute("SELECT payload,receipt,state FROM channel_parts WHERE event_key=? AND position=?",
                             (event_key, position)).fetchone()
            event = db.execute("SELECT event FROM channel_events WHERE event_key=?", (event_key,)).fetchone()
        need(bound and row and event and row["state"] == "accepted", "accepted_personal_part_required")
        claim = Path(self.claims_dir).absolute() / f"{event_key}-{position}.claim"
        info = claim.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
             stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 1024, "private_intent_claim_required")
        saved = json.loads(claim.read_bytes())
        need(set(saved) == {"event_sha256", "position", "request_sha256"} and
             saved["event_sha256"] == event_key and saved["position"] == position and
             saved["request_sha256"] == json.loads(row["receipt"]).get("request_sha256") and
             saved["request_sha256"] == self.transport.request_intent(
                 json.loads(event["event"]), json.loads(row["payload"]))[2],
             "accepted_intent_mismatch")
        return self.transport.confirm_part(self.box, event_key, position, witness,
                                           accepted_intent_sha256=saved["request_sha256"])
