"""Offline channel boundary and durable outbox. No listener or live transport is installed.

The caller must authenticate ingress before normalizing it. A transport's accepted
response is not delivery. Only a destination-bound delivered/read receipt unlocks
next parts. Unknown sends are held across restarts and never retried automatically.
"""
from dataclasses import dataclass, asdict
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat


class Held(ValueError):
    pass


def need(value, reason):
    if not value:
        raise Held(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def human_pause(path, scope, observed, request=None, release_episode=None):
    """Latch observed ownership. A release applies only to later intake rows."""
    account,inbox,contact,conversation=scope
    need(all(type(i) is int and i>0 for i in scope),"invalid_human_scope")
    uri=Path(path).resolve(strict=True).as_uri()+"?mode=rw"
    db=sqlite3.connect(uri,uri=True,timeout=3)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS intake_human_episodes (
            episode TEXT PRIMARY KEY, account INTEGER NOT NULL,inbox INTEGER NOT NULL,
            contact INTEGER NOT NULL,conversation INTEGER NOT NULL,release_cut INTEGER)""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS intake_human_active ON
            intake_human_episodes(account,inbox,conversation) WHERE release_cut IS NULL""")
        where="account=? AND inbox=? AND conversation=?"; key=(account,inbox,conversation)
        active=db.execute("SELECT episode,contact FROM intake_human_episodes WHERE "+where+
                          " AND release_cut IS NULL",key).fetchone()
        if not active and not observed:
            prior=db.execute("""SELECT 1 FROM intakes WHERE contact=? AND conversation=? AND
                reason='human_ownership' AND rowid>(SELECT coalesce(max(release_cut),0)
                FROM intake_human_episodes WHERE account=? AND inbox=? AND contact=? AND conversation=?) LIMIT 1""",
                (contact,conversation,*scope)).fetchone()
            observed=bool(prior)
        if active:need(active[1]==contact,"human_contact_changed")
        if release_episode is not None:
            need(not observed and active and active[0]==release_episode,"invalid_human_release")
            cutoff=db.execute("SELECT coalesce(max(rowid),0) FROM intakes").fetchone()[0]
            db.execute("UPDATE intake_human_episodes SET release_cut=? WHERE episode=? AND release_cut IS NULL",
                       (cutoff,release_episode))
            active=None
        elif observed and not active:
            active=(secrets.token_hex(16),contact)
            db.execute("INSERT INTO intake_human_episodes VALUES(?,?,?,?,?,NULL)",(active[0],*scope))
        protected=False
        if request is not None:
            protected=bool(db.execute("""SELECT 1 FROM intakes i JOIN intake_human_episodes h
                ON h.account=? AND h.inbox=? AND h.contact=? AND h.conversation=?
                WHERE i.request=? AND i.rowid<=h.release_cut LIMIT 1""",(*scope,request)).fetchone())
        db.commit()
        return bool(active or protected)
    finally:db.close()


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def intake_held(path,scope,request):
    db=sqlite3.connect(Path(path).resolve(strict=True).as_uri()+"?mode=ro",uri=True,timeout=3)
    try:
        return bool(db.execute("""SELECT 1 FROM intake_human_episodes h JOIN intakes i ON i.request=?
            WHERE h.account=? AND h.inbox=? AND h.contact=? AND h.conversation=? AND
            (h.release_cut IS NULL OR i.rowid<=h.release_cut) LIMIT 1""",(request,*scope)).fetchone())
    finally:db.close()


def bounded(value, maximum=512):
    need(isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value, "invalid_field")
    return value


@dataclass(frozen=True)
class Event:
    channel: str
    account: str
    sender: str
    native_id: str
    text: str

    def validate(self):
        need(self.channel in ("whatsapp", "instagram"), "invalid_channel")
        for x in (self.account, self.sender, self.native_id):
            bounded(x, 200)
        bounded(self.text, 4000)
        return self

    @property
    def key(self):
        return digest([self.channel, self.account, self.native_id])

    @property
    def conversation(self):
        return digest([self.channel, self.account, self.sender])


def evolution_event(body, *, expected_instance, authenticated=False):
    """Normalize observed Evolution messages.upsert; auth is the caller's responsibility."""
    need(authenticated is True, "unauthenticated")
    need(isinstance(body, dict) and body.get("event") in ("messages.upsert", "MESSAGES_UPSERT"), "unsupported_event")
    need(body.get("instance") == expected_instance, "wrong_instance")
    data = body.get("data", {})
    need(isinstance(data, dict), "invalid_data")
    key, message = data.get("key", {}), data.get("message", {})
    need(isinstance(key, dict) and isinstance(message, dict), "invalid_data")
    need(key.get("fromMe") is False, "echo_or_unverified_direction")
    sender = key.get("remoteJid", "")
    need(isinstance(sender, str) and re.fullmatch(r"[0-9]{8,16}@s\.whatsapp\.net", sender), "not_direct_message")
    text = message.get("conversation")
    if text is None:
        ext = message.get("extendedTextMessage", {})
        need(isinstance(ext, dict), "invalid_text")
        text = ext.get("text")
    return Event("whatsapp", expected_instance, sender, key.get("id"), text).validate()


def instagram_events(raw, signature, *, app_secret, expected_account):
    """Meta HMAC-SHA256 checked over unchanged bytes. Account identity is allowlisted."""
    need(isinstance(raw, bytes) and len(raw) <= 1_000_000, "invalid_body")
    need(isinstance(app_secret, bytes) and len(app_secret) >= 16, "missing_existing_secret")
    expected = "sha256=" + hmac.new(app_secret, raw, hashlib.sha256).hexdigest()
    need(isinstance(signature, str) and hmac.compare_digest(expected, signature), "bad_signature")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise Held("invalid_json") from None
    need(isinstance(body, dict) and body.get("object") == "instagram", "wrong_object")
    entries = body.get("entry", [])
    need(isinstance(entries, list) and 0 < len(entries) <= 100, "invalid_entries")
    out = []
    for entry in entries:
        need(isinstance(entry, dict) and entry.get("id") == expected_account, "wrong_account")
        messages = entry.get("messaging", [])
        need(isinstance(messages, list) and len(messages) <= 100, "invalid_messages")
        for item in messages:
            need(isinstance(item, dict), "invalid_message")
            msg = item.get("message")
            if msg is None:  # Delivery/read/postback events are not new customer text.
                continue
            need(isinstance(msg, dict), "invalid_message")
            if msg.get("is_echo") is True:
                continue
            sender, recipient = item.get("sender", {}), item.get("recipient", {})
            need(isinstance(sender, dict) and isinstance(recipient, dict), "invalid_identity")
            need(recipient.get("id") == expected_account and sender.get("id") != expected_account, "wrong_recipient")
            need("attachments" not in msg and "text" in msg, "unsupported_message_requires_review")
            out.append(Event("instagram", expected_account, sender.get("id"), msg.get("mid"), msg["text"]).validate())
    return out


class Outbox:
    def __init__(self, path):
        path = Path(path).absolute()
        need(not any(p.is_symlink() for p in [path, *path.parents]), "unsafe_database_path")
        parent = path.parent.stat()
        need(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700, "private_directory_required")
        self.path = str(path)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, "private_database_required")
            self.identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS channel_events (
                    event_key TEXT PRIMARY KEY, conversation TEXT NOT NULL,
                    event TEXT NOT NULL, plan_hash TEXT, state TEXT NOT NULL DEFAULT 'received');
                CREATE TABLE IF NOT EXISTS channel_parts (
                    event_key TEXT NOT NULL, position INTEGER NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    native_id TEXT, receipt TEXT, PRIMARY KEY(event_key, position));
                CREATE UNIQUE INDEX IF NOT EXISTS channel_native_id ON channel_parts(native_id)
                    WHERE native_id IS NOT NULL;
            """)

    @contextmanager
    def db(self):
        info = os.lstat(self.path)
        need(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == self.identity and info.st_nlink == 1, "database_identity_changed")
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(self.path + suffix)
            try:
                info = side.lstat()
            except FileNotFoundError:
                # SQLite may remove a normal WAL while another connection closes.
                continue
            need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, "unsafe_database_sidecar")
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def receive(self, event):
        event.validate()
        value = canonical(asdict(event))
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO channel_events(event_key,conversation,event) VALUES(?,?,?)", (event.key, event.conversation, value))
            row = db.execute("SELECT event FROM channel_events WHERE event_key=?", (event.key,)).fetchone()
            need(row["event"] == value, "event_identity_conflict")
        return event.key

    def prepare(self, event_key, texts, *, image_group=None, assets=(), choose=secrets.choice):
        """Persist exact content and reserved variant atomically; replay never reselects."""
        need(isinstance(texts, list) and texts and len(texts) <= 10 - bool(image_group), "invalid_parts")
        for text in texts:
            bounded(text, 250)
            need(text.count("*") % 2 == 0, "unbalanced_emphasis")
        request_hash = digest([texts, image_group, assets])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM channel_events WHERE event_key=?", (event_key,)).fetchone()
            need(row, "unknown_event")
            if row["plan_hash"]:
                need(row["plan_hash"] == request_hash, "plan_conflict")
                return self.parts(event_key)
            parts = [{"kind": "text", "text": text} for text in texts]
            if image_group:
                need(image_group in ("servicos", "clube"), "invalid_image_group")
                selected = [a for a in assets if a.get("group") == image_group]
                need(len(selected) == 3 and len({a.get("id") for a in selected}) == 3, "three_approved_variants_required")
                for asset in selected:
                    need(set(asset) == {"id", "group", "sha256", "filename", "mime"}, "invalid_asset_manifest")
                    bounded(asset["id"], 80)
                    need(re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) and asset["mime"] == "image/jpeg", "invalid_asset_digest")
                    bounded(asset["filename"], 200)
                    need(Path(asset["filename"]).name == asset["filename"] and asset["filename"] not in (".", ".."), "invalid_asset_path")
                chosen = choose(selected)
                need(chosen in selected, "invalid_variant_choice")
                parts.append({"kind": "image", "asset": chosen})
            db.executemany("INSERT INTO channel_parts(event_key,position,payload) VALUES(?,?,?)", [(event_key, i, canonical(p)) for i, p in enumerate(parts, 1)])
            db.execute("UPDATE channel_events SET plan_hash=?,state='prepared' WHERE event_key=?", (request_hash, event_key))
        return self.parts(event_key)

    def parts(self, event_key):
        with self.db() as db:
            return [dict(x) for x in db.execute("SELECT position,payload,state,native_id FROM channel_parts WHERE event_key=? ORDER BY position", (event_key,))]

    def advance(self, event_key, transport):
        """At most one side effect. transport.send is injected, never constructed here."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            event = db.execute("SELECT rowid,* FROM channel_events WHERE event_key=?", (event_key,)).fetchone()
            need(event and event["plan_hash"], "unknown_or_unprepared_event")
            if event["state"] == "done":
                return {"status": "done"}
            prior = db.execute("SELECT 1 FROM channel_events WHERE conversation=? AND rowid<? AND state!='done'", (event["conversation"], event["rowid"])).fetchone()
            need(not prior, "prior_event_unresolved")
            part = db.execute("SELECT * FROM channel_parts WHERE event_key=? AND state!='delivered' ORDER BY position LIMIT 1", (event_key,)).fetchone()
            if part is None:
                db.execute("UPDATE channel_events SET state='done' WHERE event_key=?", (event_key,))
                return {"status": "done"}
            need(part["state"] == "pending", "await_same_receipt_no_resend")
            db.execute("UPDATE channel_parts SET state='sending' WHERE event_key=? AND position=?", (event_key, part["position"]))
        value = json.loads(event["event"])
        payload = json.loads(part["payload"])
        try:
            receipt = transport.send(value, payload)
            need(isinstance(receipt, dict), "invalid_receipt")
            native_id = bounded(receipt.get("id"), 200)
            need(receipt.get("destination") == value["sender"] and receipt.get("account") == value["account"], "receipt_identity_mismatch")
            qualified_id = canonical([value["channel"], value["account"], native_id])
            with self.db() as db:
                db.execute("UPDATE channel_parts SET state='accepted',native_id=?,receipt=? WHERE event_key=? AND position=? AND state='sending'", (qualified_id, canonical(receipt), event_key, part["position"]))
            return {"status": "accepted_not_delivered", "position": part["position"]}
        except Exception:
            # It may have executed: retain sending, block every subsequent part/event.
            raise Held("send_outcome_unknown") from None

    def confirm(self, event_key, position, verifier):
        """Verifier must use authenticated native readback, not user-supplied text."""
        with self.db() as db:
            part = db.execute("SELECT * FROM channel_parts WHERE event_key=? AND position=?", (event_key, position)).fetchone()
            event = db.execute("SELECT event FROM channel_events WHERE event_key=?", (event_key,)).fetchone()
        need(part and event, "unknown_part")
        if part["state"] == "delivered":
            return {"status": "delivered"}
        need(part["state"] == "accepted", "unknown_native_id_requires_manual_reconciliation")
        value = json.loads(event["event"])
        payload = json.loads(part["payload"])
        native = json.loads(part["native_id"])[2]
        proof = verifier(value, native, payload)
        need(isinstance(proof, dict) and proof.get("id") == native and proof.get("destination") == value["sender"] and proof.get("account") == value["account"], "readback_identity_mismatch")
        need(proof.get("payload_sha256") == digest(payload), "readback_content_mismatch")
        need(proof.get("status") in ("delivered", "read"), "not_delivered")
        with self.db() as db:
            db.execute("UPDATE channel_parts SET state='delivered',receipt=? WHERE event_key=? AND position=? AND state='accepted'", (canonical(proof), event_key, position))
        return {"status": "delivered"}


def exception_summary(*, channel, name, summary, decision, reason):
    """Only unresolved exceptions; no full transcript, scheduling confirmation, or send."""
    need(channel in ("whatsapp", "instagram"), "invalid_channel")
    need(reason in ("unresolved_request", "provider_unknown", "conflicting_business_rule", "customer_requests_human"), "not_an_exception")
    fields = [bounded(name, 80), bounded(summary, 350), bounded(decision, 200)]
    need(all("\n" not in f and "\r" not in f for f in fields), "single_line_fields_required")
    return {"channel": channel, "name": name, "summary": summary, "decision": decision, "reason": reason}


def exception_destination(config, *, verified_at, now):
    """Consume an independently verified group, never infer members from its name."""
    need(config.get("mode") == "exceptions_only", "exception_route_disabled")
    need(config.get("group_identity_verified") is True and config.get("members_authorized") is True, "group_verification_required")
    need(0 <= (now - verified_at).total_seconds() <= 86400, "group_verification_stale")
    dest = config.get("group_id")
    need(isinstance(dest, str) and re.fullmatch(r"[0-9-]+@g\.us", dest), "invalid_group")
    return dest
