"""Isolated collector-to-Chatwoot intake. There is deliberately no send operation."""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import http.server
import json
import os
import pathlib
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "channels"))
from customer_identity import resolve
from customer_planner import PlannerReservation, ReservationError
from customer_plan import HeldPlanner, PlanHeld


class Held(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Held(reason)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def receipt_id(instance, raw):
    name = instance.encode()
    return digest(len(name).to_bytes(4, "big") + name + raw)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Chatwoot:
    """The only writable external surface is one pinned, isolated Chatwoot account."""

    def __init__(self, account, inbox, channel, token, origin="http://127.0.0.1:13300"):
        need(re.fullmatch(r"http://127\.0\.0\.1:13300", origin) is not None, "chatwoot_origin")
        self.account, self.inbox, self.channel = account, inbox, channel
        self.token, self.origin = token, origin
        need(type(account) is int and account > 0 and type(inbox) is int and inbox > 0 and
             isinstance(channel, str) and re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", channel), "chatwoot_scope")

    def request(self, method, path, body=None):
        need(path.startswith("/") and not path.startswith("//") and ".." not in path, "chatwoot_path")
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        req = urllib.request.Request(self.origin + path, data=data, method=method,
            headers={"api_access_token": self.token, "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=15) as response:
            raw = response.read(1_000_001)
            need(response.status in (200, 201) and len(raw) <= 1_000_000, "chatwoot_response")
            return json.loads(raw)

    def contact(self, address_hash):
        identifier = "jp-intake-" + address_hash
        result = self.request("POST", f"/api/v1/accounts/{self.account}/contacts", {
            "name": "Contato a conferir", "identifier": identifier})
        contact = (result.get("payload") or {}).get("contact")
        need(isinstance(contact, dict) and contact.get("identifier") == identifier,
             "contact_identity_mismatch")
        return contact

    def contact_inbox(self, contact_id, source_id):
        return self.request("POST", f"/api/v1/accounts/{self.account}/contacts/{contact_id}/contact_inboxes",
                            {"inbox_id": self.inbox, "source_id": source_id})

    def conversation(self, contact_id, source_id):
        return self.request("POST", f"/api/v1/accounts/{self.account}/conversations",
                            {"source_id": source_id, "inbox_id": self.inbox,
                             "contact_id": contact_id, "status": "open"})

    def conversation_readback(self, conversation_id):
        return self.request("GET", f"/api/v1/accounts/{self.account}/conversations/{conversation_id}")

    def incoming(self, source_id, conversation_id, text, request_id):
        path = (f"/public/api/v1/inboxes/{self.channel}/contacts/{source_id}"
                f"/conversations/{conversation_id}/messages")
        return self.request("POST", path, {"content": text, "echo_id": request_id})

    def messages(self, conversation_id):
        result = self.request("GET", f"/api/v1/accounts/{self.account}/conversations/{conversation_id}/messages")
        return result["payload"]


class Intake:
    def __init__(self, db_path, config, chatwoot, directory, lookup):
        self.db_path, self.cfg, self.chat = str(db_path), config, chatwoot
        self.directory, self.lookup = directory, lookup
        need(config["account"] == chatwoot.account and config["inbox"] == chatwoot.inbox and
             config["channel"] == chatwoot.channel and type(config["fixture_account"]) is int and
             type(config["review_inbox"]) is int and config["account"] != config["fixture_account"] and
             config["inbox"] != config["review_inbox"], "isolated_scope_required")
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS contacts (
                address_hash TEXT PRIMARY KEY, state TEXT NOT NULL, contact INTEGER,
                source TEXT, conversation INTEGER, identity TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS intakes (
                request TEXT PRIMARY KEY, native_hash TEXT UNIQUE NOT NULL, record TEXT NOT NULL,
                address_hash TEXT NOT NULL, raw_hash TEXT NOT NULL, text_hash TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT, contact INTEGER, conversation INTEGER,
                message INTEGER)""")
        fd = os.open(pathlib.Path(self.db_path).resolve().parent,
                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def current(self, request_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM intakes WHERE request=?", (request_id,)).fetchone()
        need(row, "missing_intake")
        return {"account": self.chat.account, "inbox": self.chat.inbox,
                **{k: row[k] for k in ("request", "record", "state", "reason", "contact", "conversation", "message")}}

    def _contact(self, address_hash, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM contacts WHERE address_hash=?", (address_hash,)).fetchone()
            if row:
                need(row["state"] == "ready", "contact_outcome_unknown")
                need(row["identity"] == identity, "identity_changed_requires_review")
                return row["contact"], row["source"], row["conversation"]
            db.execute("INSERT INTO contacts(address_hash,state,identity) VALUES(?,'creating_contact',?)",
                       (address_hash, identity))
        contact = self.chat.contact(address_hash)
        contact_id = contact.get("id")
        need(type(contact_id) is int and contact_id > 0,
             "contact_readback_failed")
        with self.db() as db:
            db.execute("UPDATE contacts SET contact=?,state='creating_inbox' WHERE address_hash=?",
                       (contact_id, address_hash))
        source = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jp-isolated-intake:{self.chat.inbox}:{address_hash}"))
        self.chat.contact_inbox(contact_id, source)
        with self.db() as db:
            db.execute("UPDATE contacts SET source=?,state='creating_conversation' WHERE address_hash=?", (source, address_hash))
        conversation = self.chat.conversation(contact_id, source)
        conv_id = conversation.get("id")
        need(type(conv_id) is int and conv_id > 0, "conversation_create_unknown")
        self._check_conversation(conv_id, contact_id)
        with self.db() as db:
            db.execute("UPDATE contacts SET conversation=?,state='ready' WHERE address_hash=?", (conv_id, address_hash))
        return contact_id, source, conv_id

    def _check_conversation(self, conv_id, contact_id):
        row = self.chat.conversation_readback(conv_id)
        need((row.get("account_id"), row.get("inbox_id"), row.get("id"),
              (row.get("meta") or {}).get("sender", {}).get("id")) ==
             (self.chat.account, self.chat.inbox, conv_id, contact_id), "conversation_scope_mismatch")
        return row

    def receive(self, body):
        """One authenticated n8n call; unknown outcomes remain held, never replayed."""
        need(isinstance(body, dict) and set(body) == {"raw_base64", "record_id", "workflow_id", "execution_id"},
             "invalid_intake")
        need(body["workflow_id"] == self.cfg["workflow"] and
             re.fullmatch(r"[0-9]{1,20}", str(body["execution_id"])), "workflow_mismatch")
        raw = base64.b64decode(body["raw_base64"], validate=True)
        need(0 < len(raw) <= 1_000_000 and receipt_id(self.cfg["instance"], raw) == body["record_id"],
             "record_mismatch")
        envelope = json.loads(raw)
        need(isinstance(envelope, dict) and envelope.get("instance") == self.cfg["instance"] and
             str(envelope.get("event", "")).lower().replace("_", ".") == "messages.upsert", "event_mismatch")
        data = envelope.get("data")
        need(isinstance(data, dict) and isinstance(data.get("key"), dict), "message_missing")
        key = data["key"]
        native = key.get("id")
        sender = key.get("remoteJid")
        need(key.get("fromMe") is False and not key.get("participant") and
             not key.get("remoteJidAlt") and not key.get("participantAlt") and
             isinstance(native, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,160}", native) and
             isinstance(sender, str) and re.fullmatch(r"55[0-9]{10,11}@s\.whatsapp\.net", sender),
             "native_sender_unverified")
        message = data.get("message") or {}
        text = message.get("conversation") or (message.get("extendedTextMessage") or {}).get("text")
        need(isinstance(text, str) and 0 < len(text.strip()) <= 600, "unsupported_content")
        address_hash, native_hash = digest(sender.encode()), digest(native.encode())
        decision = resolve(self.directory(), [address_hash], self.lookup)[address_hash]
        identity = json.dumps(decision, sort_keys=True, separators=(",", ":"))
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jp-isolated-intake:{self.cfg['instance']}:{native}"))
        fingerprint = (native_hash, body["record_id"], address_hash, digest(raw), digest(text.encode()))
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO intakes(request,native_hash,record,address_hash,raw_hash,text_hash,state) "
                       "VALUES(?,?,?,?,?,?,'new')", (request_id, *fingerprint))
            row = db.execute("SELECT * FROM intakes WHERE request=?", (request_id,)).fetchone()
            need(row and tuple(row[k] for k in ("native_hash", "record", "address_hash", "raw_hash", "text_hash")) == fingerprint,
                 "native_id_conflict")
            if row["state"] != "new":
                return self.current(request_id)
            db.execute("UPDATE intakes SET state='working' WHERE request=?", (request_id,))
        try:
            contact, source, conv = self._contact(address_hash, identity)
            owner = self._check_conversation(conv, contact)
            with self.db() as db:
                db.execute("UPDATE intakes SET contact=?,conversation=?,state='posting' WHERE request=?",
                           (contact, conv, request_id))
            incoming = self.chat.incoming(source, conv, text, request_id)
            mid = incoming.get("id")
            need(type(mid) is int and mid > 0, "incoming_outcome_unknown")
            matches = [m for m in self.chat.messages(conv) if m.get("id") == mid and
                       m.get("message_type") == 0 and m.get("content") == text and
                       (m.get("sender") or {}).get("id") == contact]
            need(len(matches) == 1, "incoming_readback_failed")
            owner = self._check_conversation(conv, contact)
            meta = owner.get("meta") or {}
            labels = owner.get("labels") or []
            human = bool(meta.get("assignee") or meta.get("team") or "jp-atendimento-humano" in labels)
            if human:
                reason = "human_ownership"
            elif decision["status"] == "identified":
                reason = "customer_inactive" if decision["inactive"] else "opt_out" if decision["opt_out"] else None
            elif decision["status"] == "no_customer_match":
                reason = None
            else:
                reason = decision["status"]
            state = "held" if reason else "public_only" if decision["status"] == "no_customer_match" else "ready"
            with self.db() as db:
                db.execute("UPDATE intakes SET state=?,reason=?,message=? WHERE request=? AND state='posting'",
                           (state, reason, mid, request_id))
            return self.current(request_id)
        except Exception:
            with self.db() as db:
                db.execute("UPDATE intakes SET state='held',reason='outcome_unknown' "
                           "WHERE request=? AND state IN ('working','posting')", (request_id,))
            raise


def serve(config_path):
    cfg = json.loads(pathlib.Path(config_path).read_text())
    token = pathlib.Path(cfg["token_file"]).read_bytes().strip()
    need(len(token) >= 32, "short_token")
    directory = lambda: json.loads(pathlib.Path(cfg["directory_file"]).read_text())
    lookup_rows = json.loads(pathlib.Path(cfg["lookup_file"]).read_text())
    lookup = lambda numbers: [r for r in lookup_rows if str(r.get("number")) in numbers]
    chat = Chatwoot(cfg["account"], cfg["inbox"], cfg["channel"],
                    pathlib.Path(cfg["chatwoot_token_file"]).read_text().strip())
    app = Intake(cfg["db_file"], cfg, chat, directory, lookup)
    planner = PlannerReservation(cfg["reservations_db_file"], cfg["db_file"], chat)
    manifest = pathlib.Path(cfg["asset_manifest_file"]).read_bytes()
    assets = pathlib.Path(cfg["asset_dir"])
    def asset_bytes(filename):
        need(isinstance(filename, str) and pathlib.Path(filename).name == filename,
             "invalid_asset_path")
        path = assets / filename
        need(not path.is_symlink() and path.is_file(), "invalid_asset_file")
        return path.read_bytes()
    held_plan = HeldPlanner(cfg["plans_db_file"], cfg["reservations_db_file"], chat,
        manifest_bytes=manifest, manifest_sha256=cfg["asset_manifest_sha256"],
        asset_bytes=asset_bytes,
        selected_asset=cfg["selected_asset"])
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            try:
                need(self.path in ("/inbound", "/reserve", "/plan"), "wrong_path")
                supplied = self.headers.get("Authorization", "")
                need(hmac.compare_digest(supplied.encode(), b"Bearer " + token), "unauthorized")
                size = int(self.headers.get("Content-Length", "0"))
                need(0 < size <= 1_400_000, "size_limit")
                body = json.loads(self.rfile.read(size))
                result = (app.receive(body) if self.path == "/inbound" else
                          planner.reserve(body) if self.path == "/reserve" else held_plan.prepare(body))
                payload, status = json.dumps(result).encode(), 200
            except (Held, ReservationError, PlanHeld) as exc:
                payload, status = json.dumps({"status": "held", "reason": str(exc)}).encode(), 409
            except Exception:
                payload, status = b'{"status":"held","reason":"outcome_unknown"}', 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = http.server.ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler)
    server.serve_forever()


if __name__ == "__main__":
    need(len(sys.argv) == 2, "config_path_required")
    serve(sys.argv[1])
