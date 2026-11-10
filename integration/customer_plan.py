"""Plan approved public information from a real reservation; retain every part."""
from contextlib import contextmanager
import hashlib
import json
import os
import pathlib
import sqlite3
import uuid

from policy import prepare as pilot_prepare


class PlanHeld(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise PlanHeld(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class HeldPlanner:
    def __init__(self, path, reservations, chat, *, manifest_bytes, manifest_sha256,
                 asset_bytes, selected_asset="servicos-03"):
        self.path, self.reservations, self.chat = str(path), str(reservations), chat
        need(digest(manifest_bytes) == manifest_sha256, "manifest_changed")
        catalog = json.loads(manifest_bytes)
        need(isinstance(catalog.get("assets"), list), "manifest_invalid")
        pools = {}
        for group in ("servicos", "clube-jp"):
            variants = sorted((a for a in catalog["assets"] if a.get("group") == group),
                              key=lambda a: a.get("id", ""))
            need(len(variants) == 3 and len({a.get("id") for a in variants}) == 3 and
                 all(a.get("approved") is True and isinstance(a.get("filename"), str) and
                     pathlib.Path(a["filename"]).name == a["filename"] and
                     type(a.get("bytes")) is int and a["bytes"] > 4 and
                     isinstance(a.get("sha256"), str) and len(a["sha256"]) == 64
                     for a in variants), "three_approved_variants_required")
            pools[group] = variants
        self.catalog, self.pools, self.asset_bytes = catalog, pools, asset_bytes
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS held_parts (
                reservation TEXT NOT NULL, position INTEGER NOT NULL,
                part_id TEXT UNIQUE NOT NULL, account INTEGER NOT NULL, inbox INTEGER NOT NULL,
                contact INTEGER NOT NULL, conversation INTEGER NOT NULL, message INTEGER NOT NULL,
                content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                asset_id TEXT, asset_sha256 TEXT, state TEXT NOT NULL CHECK(state='held_before_transport'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(reservation,position))""")
            db.execute("""CREATE TABLE IF NOT EXISTS service_requests (
                record TEXT PRIMARY KEY, reservation TEXT NOT NULL UNIQUE, account INTEGER NOT NULL,
                inbox INTEGER NOT NULL, contact INTEGER NOT NULL, conversation INTEGER NOT NULL,
                message INTEGER NOT NULL, text_hash TEXT NOT NULL, mode TEXT NOT NULL,
                category TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        parent = pathlib.Path(self.path).resolve().parent
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _reservation(self, submitted):
        need(isinstance(submitted, dict) and set(submitted) == {
            "reservation", "request", "account", "inbox", "contact", "conversation", "message", "mode"},
            "plan_contract_invalid")
        rid = submitted["reservation"]
        need(isinstance(rid, str) and str(uuid.UUID(rid)) == rid, "reservation_id_invalid")
        source = sqlite3.connect(f"file:{self.reservations}?mode=ro", uri=True)
        try:
            source.row_factory = sqlite3.Row
            source.execute("BEGIN")
            row = source.execute("SELECT * FROM reservations WHERE reservation=?", (rid,)).fetchone()
            source.rollback()
        finally:
            source.close()
        need(row and row["state"] == "reserved" and
             all(row[k] == submitted[k] for k in submitted) and
             (row["account"], row["inbox"]) == (self.chat.account, self.chat.inbox),
             "reservation_binding_invalid")
        return row

    def _human_free(self, row):
        conv = self.chat.conversation_readback(row["conversation"])
        meta = conv.get("meta") or {}
        need((conv.get("account_id"), conv.get("inbox_id"), conv.get("id"),
              (meta.get("sender") or {}).get("id")) ==
             (row["account"], row["inbox"], row["conversation"], row["contact"]),
             "plan_conversation_identity_changed")
        need(not meta.get("assignee") and not meta.get("team") and
             "jp-atendimento-humano" not in (conv.get("labels") or []), "human_ownership")

    def prepare(self, submitted):
        """Read approved art, derive two parts, recheck ownership per part, commit no-send receipt."""
        if submitted.get("status") == "held" and not submitted.get("reservation"):
            return {"status": "held", "request": submitted.get("request"), "reason": "intake_not_reserved"}
        row = self._reservation(submitted)
        messages = [m for m in self.chat.messages(row["conversation"]) if
                    m.get("id") == row["message"] and m.get("message_type") == 0 and
                    (m.get("sender") or {}).get("id") == row["contact"] and
                    isinstance(m.get("content"), str) and
                    digest(m["content"].encode()) == row["text_hash"]]
        need(len(messages) == 1, "plan_inbound_readback_failed")
        query_calls = []
        def forbidden_query(*args):
            query_calls.append(args)
            raise PlanHeld("personal_lookup_forbidden")
        options = {"intent": "auto", "service": "", "professional": "", "date": ""}
        context, text, decision, group = pilot_prepare(
            messages[0]["content"], options, {}, self.catalog, forbidden_query)
        public_table = (context.get("category") == "services_art" and group == "servicos" and
                        text == "Aqui está a tabela de serviços.")
        public_club = (context.get("category") == "club" and group == "clube-jp" and
                       isinstance(text, str) and 0 < len(text) <= 600)
        if not (decision == "answer" and (public_table or public_club)):
            # Retain the native request for the scheduling, service or human worker.
            # The original incoming Chatwoot message remains the content authority.
            columns=("record","reservation","account","inbox","contact","conversation","message","text_hash","mode")
            values=tuple(row[k] for k in columns)+(context["category"],)
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""INSERT OR IGNORE INTO service_requests
                    (record,reservation,account,inbox,contact,conversation,message,text_hash,mode,category)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",values)
                saved=db.execute("SELECT * FROM service_requests WHERE record=?",(row["record"],)).fetchone()
                need(saved and tuple(saved[k] for k in (*columns,"category"))==values,"service_request_conflict")
            return {"status": "held", "request": row["request"], "reservation": row["reservation"],
                    "reason": "public_plan_not_applicable","service_record":row["record"],"service_state":saved["state"]}
        need(not query_calls, "personal_lookup_forbidden")
        pool = self.pools[group]
        with self.db() as db:
            prior = db.execute("SELECT asset_id,asset_sha256 FROM held_parts WHERE reservation=? ORDER BY position",
                               (row["reservation"],)).fetchall()
        need(len(prior) in (0, 2), "partial_prior_plan")
        if prior:
            selected = [a for a in pool if a["id"] == prior[1]["asset_id"] and
                        a["sha256"] == prior[1]["asset_sha256"]]
            need(prior[0]["asset_id"] is None and len(selected) == 1,
                 "prior_variant_changed")
            asset = selected[0]
        else:
            asset = pool[int(digest(row["reservation"].encode()), 16) % len(pool)]
        raw = self.asset_bytes(asset["filename"])
        need(isinstance(raw, bytes) and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9") and
             len(raw) == asset["bytes"] and digest(raw) == asset["sha256"],
             "approved_artwork_bytes_changed")
        pieces = [(text, None, None),
                  ("Tabela de serviços" if public_table else "Clube JP", asset["id"], asset["sha256"])]
        prepared = []
        for position, (content, asset_id, asset_sha) in enumerate(pieces, 1):
            self._human_free(row)  # A human may claim the conversation between parts.
            part_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jp-isolated-part:{row['reservation']}:{position}"))
            prepared.append((row["reservation"], position, part_id, row["account"], row["inbox"],
                row["contact"], row["conversation"], row["message"], content,
                digest(content.encode()), asset_id, asset_sha, "held_before_transport"))
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany("INSERT OR IGNORE INTO held_parts(reservation,position,part_id,account,inbox,contact,conversation,message,content,content_sha256,asset_id,asset_sha256,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", prepared)
            saved = [tuple(x) for x in db.execute("SELECT reservation,position,part_id,account,inbox,contact,conversation,message,content,content_sha256,asset_id,asset_sha256,state FROM held_parts WHERE reservation=? ORDER BY position", (row["reservation"],))]
            need(saved == prepared, "held_parts_conflict")
        return {"status": "prepared_held", "request": row["request"],
                "reservation": row["reservation"], "account": row["account"], "inbox": row["inbox"],
                "contact": row["contact"], "conversation": row["conversation"], "message": row["message"],
                "mode": row["mode"], "parts": [{"position": x[1], "part_id": x[2],
                "content_sha256": x[9], "asset_id": x[10], "asset_sha256": x[11],
                "state": x[12]} for x in prepared], "transport_attempts": 0}
