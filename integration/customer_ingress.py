"""Collector-to-Chatwoot intake with an optional handoff to the fenced dispatcher."""
import base64
from contextlib import closing, contextmanager
import hashlib
import hmac
import http.client
import http.server
import json
import os
import pathlib
import re
import socket
import sqlite3
import ssl
import struct
import sys
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "channels"))
from customer_identity import resolve
from bridge import human_pause
from customer_planner import PlannerReservation, ReservationError
from customer_plan import HeldPlanner, PlanHeld
from claimed_customer import ClaimClosed, dell_reader


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


PN = re.compile(r"55[0-9]{10,11}@s\.whatsapp\.net")
LID = re.compile(r"[0-9]{6,20}@lid")


def native_sender(data, instance, bindings, instance_id_hash=None, epoch=None, record=None):
    """Resolve a direct sender only with an instance-scoped, two-way native pair."""
    need(isinstance(bindings, dict) and bindings.get("instance") == instance and
         bindings.get("paused") is False, "native_mapping_paused_or_wrong_instance")
    if instance_id_hash is not None:
        need(isinstance(instance_id_hash, str) and re.fullmatch(r"[0-9a-f]{64}", instance_id_hash) and
             bindings.get("instance_id_hash") == instance_id_hash, "native_instance_identity_changed")
    if epoch is not None:
        need(bindings.get("epoch") == epoch and bindings.get("record_id") == record,
             "native_mapping_scope_changed")
    forward, reverse = bindings.get("forward"), bindings.get("reverse")
    need(isinstance(forward, dict) and isinstance(reverse, dict), "native_mapping_missing")
    key = data["key"]
    need(key.get("fromMe") is False and not key.get("participant") and
         not key.get("participantAlt") and not data.get("participant") and
         not data.get("participantAlt"), "native_sender_unverified")
    addresses = [v for v in (key.get("remoteJid"), key.get("remoteJidAlt"),
                              data.get("remoteJidAlt"), data.get("remoteJid")) if v is not None]
    need(addresses and all(isinstance(v, str) and (PN.fullmatch(v) or LID.fullmatch(v))
                           for v in addresses), "native_sender_unverified")
    unique = set(addresses)
    need(len(unique) <= 2, "native_sender_conflict")
    pns = [v for v in unique if PN.fullmatch(v)]
    lids = [v for v in unique if LID.fullmatch(v)]
    need(len(pns) <= 1 and len(lids) <= 1, "native_sender_conflict")
    if not lids:
        need(len(pns) == 1, "native_sender_unverified")
        return pns[0]
    lid = lids[0]
    pn = reverse.get(lid)
    need(isinstance(pn, str) and PN.fullmatch(pn) and forward.get(pn) == lid and
         len([v for v in forward.values() if v == lid]) == 1 and
         len([v for v in reverse.values() if v == pn]) == 1 and
         (not pns or pns[0] == pn), "native_mapping_unverified")
    return pn


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
    def __init__(self, db_path, config, chatwoot, directory, lookup, bindings=None, claimed=None):
        self.db_path, self.cfg, self.chat = str(db_path), config, chatwoot
        self.directory, self.lookup = directory, lookup
        self.claimed = claimed
        self.bindings = bindings or (lambda: {"instance": config["instance"], "paused": False,
                                              "forward": {}, "reverse": {}})
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
                message INTEGER, workflow TEXT, execution TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(intakes)")}
            db.execute("CREATE INDEX IF NOT EXISTS intake_human_by_contact ON intakes(contact,conversation) WHERE reason='human_ownership'")
            for name in ("workflow", "execution"):
                if name not in columns:
                    db.execute(f"ALTER TABLE intakes ADD COLUMN {name} TEXT")
            if self.claimed is not None:
                db.execute("""CREATE TABLE IF NOT EXISTS intake_identity_reads (
                    request TEXT PRIMARY KEY REFERENCES intakes(request), record TEXT NOT NULL,
                    execution TEXT NOT NULL, generation INTEGER NOT NULL, directory_count INTEGER NOT NULL,
                    decision TEXT NOT NULL, checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
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
                **{k: row[k] for k in ("request", "record", "state", "reason", "contact", "conversation", "message",
                                       "workflow", "execution")}}

    def channel_sender(self, data, record, proof=None):
        cfg = self.cfg
        key = data["key"]
        addresses = (key.get("remoteJid"), key.get("remoteJidAlt"), data.get("remoteJidAlt"), data.get("remoteJid"))
        if self.claimed is not None:
            instance_id = data.get("instanceId")
            need(isinstance(instance_id, str) and instance_id and
                 digest(instance_id.encode()) == cfg["native_instance_id_hash"], "native_instance_identity_changed")
        if self.claimed is not None and all(a is None or isinstance(a,str) and PN.fullmatch(a) for a in addresses):
            proof = {"instance":cfg["instance"], "instance_id_hash":cfg["native_instance_id_hash"],
                     "epoch":cfg["channel_epoch"], "record_id":record, "paused":False, "forward":{}, "reverse":{}}
        elif proof is None:
            proof = self.bindings()
        return native_sender(data, cfg["instance"], proof, cfg["native_instance_id_hash"],
                             cfg["channel_epoch"], record)

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

    def release_human(self,contact,conversation,episode):
        row=self._check_conversation(conversation,contact)
        meta=row.get("meta") or {}
        need(row.get("status")=="open" and isinstance(row.get("labels"),list),"human_state_unknown")
        observed=bool(meta.get("assignee") or meta.get("team") or "jp-atendimento-humano" in row["labels"])
        return human_pause(self.db_path,(self.chat.account,self.chat.inbox,contact,conversation),observed,
                           release_episode=episode)

    def receive(self, body, *, claim_epoch=None, native_proof=None):
        """One authenticated n8n call; unknown outcomes remain held, never replayed."""
        need(isinstance(body, dict) and set(body) == {"raw_base64", "record_id", "workflow_id", "execution_id"},
             "invalid_intake")
        need(isinstance(body["workflow_id"], str) and
             body["workflow_id"] in {self.cfg["workflow"], self.cfg.get("channel_workflow")} and
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
        need(isinstance(native, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,160}", native),
             "native_sender_unverified")
        channel = body["workflow_id"] == self.cfg.get("channel_workflow")
        need((channel and claim_epoch is not None and claim_epoch == self.cfg["channel_epoch"])
             or (not channel and claim_epoch is None and self.claimed is None), "channel_claim_required")
        sender = (self.channel_sender(data, body["record_id"], native_proof) if channel else
                  native_sender(data, self.cfg["instance"], self.bindings()))
        message = data.get("message") or {}
        text = message.get("conversation") or (message.get("extendedTextMessage") or {}).get("text")
        need(isinstance(text, str) and 0 < len(text.strip()) <= 600, "unsupported_content")
        address_hash, native_hash = digest(sender.encode()), digest(native.encode())
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jp-isolated-intake:{self.cfg['instance']}:{native}"))
        fingerprint = (native_hash, body["record_id"], address_hash, digest(raw), digest(text.encode()))
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO intakes(request,native_hash,record,address_hash,raw_hash,text_hash,state,workflow,execution) "
                       "VALUES(?,?,?,?,?,?,'new',?,?)", (request_id, *fingerprint,
                           body["workflow_id"], str(body["execution_id"])))
            row = db.execute("SELECT * FROM intakes WHERE request=?", (request_id,)).fetchone()
            need(row and tuple(row[k] for k in ("native_hash", "record", "address_hash", "raw_hash", "text_hash")) == fingerprint,
                 "native_id_conflict")
            need(row["workflow"] in (None, body["workflow_id"]), "native_provenance_conflict")
            if row["state"] != "new":
                return self.current(request_id)
            db.execute("UPDATE intakes SET state='working' WHERE request=?", (request_id,))
        try:
            if self.claimed is None:
                decision = resolve(self.directory(), [address_hash], self.lookup)[address_hash]
            else:
                decision, generation, count = self.claimed.decision(resolve, address_hash, body["record_id"],
                    body["workflow_id"], str(body["execution_id"]), claim_epoch)
                with self.db() as db:
                    db.execute("""INSERT INTO intake_identity_reads
                        (request,record,execution,generation,directory_count,decision) VALUES(?,?,?,?,?,?)""",
                        (request_id,body["record_id"],str(body["execution_id"]),generation,count,decision["status"]))
            identity = json.dumps(decision, sort_keys=True, separators=(",", ":"))
            contact, source, conv = self._contact(address_hash, identity)
            owner = self._check_conversation(conv, contact)
            scope=(self.chat.account,self.chat.inbox,contact,conv)
            meta=owner.get("meta") or {}
            human_pause(self.db_path,scope,bool(meta.get("assignee") or meta.get("team") or
                        "jp-atendimento-humano" in (owner.get("labels") or [])),request_id)
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
            if human_pause(self.db_path,scope,human,request_id):
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


def first_dispatch(path, record):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(35)
        sock.connect(path)
        peer=sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize("3i"))
        need(struct.unpack("3i",peer)[1]==0,"unexpected_dispatcher_uid")
        with closing(http.client.HTTPConnection("localhost")) as client:
            client.sock=sock
            client.request("POST","/first-record",json.dumps(record).encode(),{"Content-Type":"application/json"})
            response=client.getresponse()
            raw=response.read(1025)
            need(len(raw)<=1024 and response.status in (200,409),"first_outcome_unknown")
            result=json.loads(raw)
            if response.status==409:
                need(result=={"status":"held"},"invalid_first_response")
            return result


def process_channel(raw, record, workflow, execution, epoch, cfg, app, planner, held_plan):
    """Consume one verified collector handoff and retain the same execution throughout."""
    need(cfg.get("channel_epoch") == epoch and cfg.get("channel_workflow") == workflow and
         isinstance(execution, str) and re.fullmatch(r"[0-9]{1,20}", execution) and
         isinstance(record, str) and re.fullmatch(r"[0-9a-f]{64}", record) and
         isinstance(raw, bytes) and 0 < len(raw) <= 1_000_000 and
         receipt_id(cfg["instance"], raw) == record, "channel_scope_invalid")
    if app.claimed is None or "channel_fixture_records" in cfg:
        need(record in cfg.get("channel_fixture_records", []), "channel_fixture_scope")
    if app.claimed is not None:
        app.claimed.check(record, workflow, execution, epoch)
    proof=None
    try:
        envelope = json.loads(raw)
        need(envelope.get("instance") == cfg["instance"] and
             str(envelope.get("event", "")).lower().replace("_", ".") == "messages.upsert",
             "channel_event_invalid")
        data=envelope["data"]
        key=data["key"]
        addresses=(key.get("remoteJid"),key.get("remoteJidAlt"),data.get("remoteJid"),data.get("remoteJidAlt"))
        if cfg.get("native_pair_reader") and any(isinstance(a,str) and LID.fullmatch(a) for a in addresses):
            need(app.claimed is not None,"claim_required")
            proof=read_claimed_pair(cfg["native_pair_reader"],raw,record,epoch)
            app.claimed.check(record,workflow,execution,epoch)
        sender=app.channel_sender(data, record, proof)
    except (Held, ValueError, KeyError, TypeError, OSError, http.client.HTTPException):
        # The encrypted collector record remains the durable review source.
        return {"ok": True, "record_id": record, "workflow": workflow,
                "execution": execution, "status": "retained", "reason": "native_identity_unverified"}
    intake = app.receive({"raw_base64": base64.b64encode(raw).decode(), "record_id": record,
                          "workflow_id": workflow, "execution_id": execution}, claim_epoch=epoch,native_proof=proof)
    if app.claimed is not None:
        app.claimed.check(record, workflow, execution, epoch)
    submitted = {k: intake[k] for k in
                 ("request", "record", "account", "inbox", "contact", "conversation", "message", "state")}
    reservation = planner.reserve(submitted)
    if reservation.get("status") == "reserved":
        plan = held_plan.prepare({k: reservation[k] for k in
                                 ("reservation", "request", "account", "inbox", "contact",
                                  "conversation", "message", "mode")})
    else:
        plan = {"status": "held"}
    result={"ok": True, "record_id": record, "workflow": workflow,
            "execution": execution, "first_execution": intake["execution"],
            "status": "retained", "intake": intake["request"],
            "reservation": reservation.get("reservation"),
            "plan": plan.get("status"), "transport_attempts": 0}
    if plan.get("service_record"):
        need(plan["service_record"]==record,"wrong_service_record")
        result.update(service_record=record,service_state=plan["service_state"])
    if cfg.get("first_dispatch_socket") and plan.get("status")=="prepared_held":
        need(app.claimed is not None and intake["execution"]==execution,"first_claim_required")
        app.claimed.check(record,workflow,execution,epoch)
        receipt=first_dispatch(cfg["first_dispatch_socket"],dict(record=record,workflow=workflow,
            execution=execution,epoch=epoch,sender=sender))
        need(receipt["status"] in ("held","accepted","delivery_unknown","human") and
             (receipt["status"]=="held" or receipt.get("attempt")==plan["parts"][0]["part_id"]), "invalid_first_response")
        result.pop("transport_attempts")
        result["first_part"]=receipt["status"]
        if receipt["status"]=="accepted":result["status"]="processed"
    return result


def read_claimed_pair(cfg, raw, record, epoch):
    # Direct LAN mutual TLS. No proxy, redirects, credentials for Evolution, or selectable URL.
    import ipaddress
    host = str(ipaddress.IPv4Address(cfg["host"]))
    need(ipaddress.ip_address(host).is_private and type(cfg["port"]) is int and
         1024 <= cfg["port"] <= 65535, "invalid_native_reader")
    tls = ssl.create_default_context(cafile=cfg["server_ca"])
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(cfg["client_cert"], cfg["client_key"])
    connection = http.client.HTTPSConnection(host, cfg["port"], timeout=4, context=tls)
    try:
        connection.request("POST", "/v1/claimed-pair", body=raw, headers={
            "Content-Type": "application/octet-stream", "X-Record-Id":record, "X-Owner-Epoch":epoch})
        response=connection.getresponse()
        result=response.read(4097)
        need(response.status==200 and len(result)<=4096,"native_reader_unavailable")
        return json.loads(result)
    finally:
        connection.close()


def serve(config_path):
    cfg = json.loads(pathlib.Path(config_path).read_text())
    claimed = None
    if "claimed_queue_db" in cfg:
        need(cfg.get("channel_epoch") and cfg.get("channel_workflow"), "channel_claim_required")
        claimed = dell_reader(cfg["claimed_queue_db"], cfg["channel_workflow"],
                              cfg["channel_epoch"], cfg["instance"])
    if cfg.get("channel_epoch"):
        fixtures = cfg.get("channel_fixture_records")
        need((cfg.get("native_bindings_file") or claimed is not None) and
             isinstance(cfg.get("native_instance_id_hash"), str) and
             re.fullmatch(r"[0-9a-f]{64}", cfg["native_instance_id_hash"]) and
             isinstance(cfg.get("channel_workflow"), str) and
             re.fullmatch(r"[A-Za-z0-9]{8,32}", cfg["channel_workflow"]) and
             re.fullmatch(r"[0-9a-f-]{36}", cfg["channel_epoch"]) and
             ((claimed is not None and fixtures is None) or
              (isinstance(fixtures, list) and len(fixtures) > 0 and len(fixtures) == len(set(fixtures)) and
               all(isinstance(r, str) and re.fullmatch(r"[0-9a-f]{64}", r) for r in fixtures))),
             "channel_proof_configuration_required")
    token = pathlib.Path(cfg["token_file"]).read_bytes().strip()
    need(len(token) >= 32, "short_token")
    directory = lambda: json.loads(pathlib.Path(cfg["directory_file"]).read_text())
    lookup_rows = [] if claimed is not None else json.loads(pathlib.Path(cfg["lookup_file"]).read_text())
    lookup = lambda numbers: [r for r in lookup_rows if str(r.get("number")) in numbers]
    def bindings():
        target = cfg.get("native_bindings_file")
        if not target:
            return {"instance": cfg["instance"], "paused": False, "forward": {}, "reverse": {}}
        path = pathlib.Path(target)
        info = path.lstat()
        need(path.is_file() and not path.is_symlink() and info.st_uid == os.geteuid() and
             info.st_mode & 0o777 == 0o600 and info.st_nlink == 1, "private_native_mapping_required")
        return json.loads(path.read_text())
    chat = Chatwoot(cfg["account"], cfg["inbox"], cfg["channel"],
                    pathlib.Path(cfg["chatwoot_token_file"]).read_text().strip())
    app = Intake(cfg["db_file"], cfg, chat, directory, lookup, bindings, claimed)
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
                need(self.path in ("/inbound", "/reserve", "/plan", "/channel-inbound"), "wrong_path")
                supplied = self.headers.get("Authorization", "")
                need(hmac.compare_digest(supplied.encode(), b"Bearer " + token), "unauthorized")
                size = int(self.headers.get("Content-Length", "0"))
                need(0 < size <= 1_400_000, "size_limit")
                body = self.rfile.read(size)
                if self.path == "/channel-inbound":
                    result = process_channel(body, self.headers.get("X-JP-Record-ID"),
                        self.headers.get("X-JP-n8n-workflow-id"),
                        self.headers.get("X-JP-n8n-execution-id"),
                        self.headers.get("X-JP-Owner-Epoch"), cfg, app, planner, held_plan)
                else:
                    data = json.loads(body)
                    result = (app.receive(data) if self.path == "/inbound" else
                              planner.reserve(data) if self.path == "/reserve" else held_plan.prepare(data))
                payload, status = json.dumps(result).encode(), 200
            except (Held, ReservationError, PlanHeld, ClaimClosed) as exc:
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
    if len(sys.argv)==6 and sys.argv[1]=="release-human":
        need(os.geteuid()==0 and re.fullmatch(r"[0-9a-f]{32}",sys.argv[5]),"local_operator_required")
        cfg=json.loads(pathlib.Path(sys.argv[2]).read_bytes())
        chat=Chatwoot(cfg["account"],cfg["inbox"],cfg["channel"],pathlib.Path(cfg["chatwoot_token_file"]).read_text().strip())
        intake=Intake(cfg["db_file"],cfg,chat,None,None)
        need(intake.release_human(int(sys.argv[3]),int(sys.argv[4]),sys.argv[5]) is False,"release_unconfirmed")
        print(json.dumps({"released":True,"replayed":0}))
    else:
        need(len(sys.argv) == 2, "config_path_required")
        serve(sys.argv[1])
