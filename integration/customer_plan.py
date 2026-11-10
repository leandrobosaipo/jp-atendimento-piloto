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
        selected = [a for a in catalog["assets"] if a.get("id") == selected_asset]
        need(len(selected) == 1 and selected[0].get("group") == "servicos" and
             selected[0].get("approved") is True and
             isinstance(selected[0].get("filename"), str) and
             pathlib.Path(selected[0]["filename"]).name == selected[0]["filename"],
             "approved_service_art_required")
        self.catalog, self.asset, self.asset_bytes = catalog, selected[0], asset_bytes
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS held_parts (
                reservation TEXT NOT NULL, position INTEGER NOT NULL,
                part_id TEXT UNIQUE NOT NULL, account INTEGER NOT NULL, inbox INTEGER NOT NULL,
                contact INTEGER NOT NULL, conversation INTEGER NOT NULL, message INTEGER NOT NULL,
                content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                asset_id TEXT, asset_sha256 TEXT, state TEXT NOT NULL CHECK(state='held_before_transport'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(reservation,position))""")
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
        if not (context.get("category") == "services_art" and decision == "answer" and
                group == "servicos" and text == "Aqui está a tabela de serviços."):
            return {"status": "held", "request": row["request"], "reservation": row["reservation"],
                    "reason": "public_plan_not_applicable"}
        need(not query_calls, "personal_lookup_forbidden")
        raw = self.asset_bytes(self.asset["filename"])
        need(isinstance(raw, bytes) and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9") and
             len(raw) == self.asset["bytes"] and digest(raw) == self.asset["sha256"],
             "approved_artwork_bytes_changed")
        pieces = [(text, None, None), ("Tabela de serviços", self.asset["id"], self.asset["sha256"])]
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
