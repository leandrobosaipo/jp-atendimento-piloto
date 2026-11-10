"""Durable reservation consumed from an isolated Chatwoot intake, with no sender."""
import hashlib
from contextlib import contextmanager
import json
import os
import pathlib
import sqlite3
import uuid


class ReservationError(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise ReservationError(reason)


class PlannerReservation:
    def __init__(self, path, intake_path, chat):
        self.path, self.intake_path, self.chat = str(path), str(intake_path), chat
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reservations (
                request TEXT PRIMARY KEY, reservation TEXT UNIQUE NOT NULL, record TEXT NOT NULL,
                account INTEGER NOT NULL, inbox INTEGER NOT NULL, contact INTEGER NOT NULL,
                conversation INTEGER NOT NULL, message INTEGER NOT NULL, mode TEXT NOT NULL,
                text_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'reserved',
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

    def reserve(self, submitted):
        need(isinstance(submitted, dict) and set(submitted) == {
            "request", "record", "account", "inbox", "contact", "conversation", "message", "state"},
            "reservation_contract_invalid")
        request = submitted["request"]
        need(isinstance(request, str) and str(uuid.UUID(request)) == request,
             "reservation_request_invalid")
        source = sqlite3.connect(f"file:{self.intake_path}?mode=ro", uri=True)
        try:
            source.row_factory = sqlite3.Row
            source.execute("BEGIN")
            row = source.execute("SELECT * FROM intakes WHERE request=?", (request,)).fetchone()
            source.rollback()
        finally:
            source.close()
        need(row and row["record"] == submitted["record"] and
             submitted["account"] == self.chat.account and submitted["inbox"] == self.chat.inbox and
             all(type(submitted[k]) is int and submitted[k] > 0 and submitted[k] == row[k]
                 for k in ("contact", "conversation", "message")) and
             row["state"] == submitted["state"], "reservation_identity_mismatch")
        if row["state"] not in ("public_only", "ready"):
            return {"status": "held", "request": request, "reason": row["reason"] or "intake_not_ready"}
        conv = self.chat.conversation_readback(row["conversation"])
        meta = conv.get("meta") or {}
        need((conv.get("account_id"), conv.get("inbox_id"), conv.get("id"),
              (meta.get("sender") or {}).get("id")) ==
             (self.chat.account, self.chat.inbox, row["conversation"], row["contact"]),
             "reservation_conversation_mismatch")
        from bridge import human_pause
        need(isinstance(conv.get("labels"),list),"human_state_unknown")
        observed=bool(meta.get("assignee") or meta.get("team") or "jp-atendimento-humano" in conv["labels"])
        if human_pause(self.intake_path,(self.chat.account,self.chat.inbox,row["contact"],row["conversation"]),observed,request):
            return {"status": "held", "request": request, "reason": "human_ownership"}
        matches = [m for m in self.chat.messages(row["conversation"]) if
                   m.get("id") == row["message"] and m.get("message_type") == 0 and
                   (m.get("sender") or {}).get("id") == row["contact"] and
                   isinstance(m.get("content"), str) and
                   hashlib.sha256(m["content"].encode()).hexdigest() == row["text_hash"]]
        need(len(matches) == 1, "reservation_message_mismatch")
        mode = "public_information" if row["state"] == "public_only" else "identified_customer"
        reservation = str(uuid.uuid5(uuid.NAMESPACE_URL, "jp-isolated-planner:" + request))
        values = (request, reservation, row["record"], self.chat.account, self.chat.inbox,
                  row["contact"], row["conversation"], row["message"], mode, row["text_hash"])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO reservations(request,reservation,record,account,inbox,contact,conversation,message,mode,text_hash) VALUES(?,?,?,?,?,?,?,?,?,?)", values)
            saved = db.execute("SELECT request,reservation,record,account,inbox,contact,conversation,message,mode,text_hash,state FROM reservations WHERE request=?", (request,)).fetchone()
            need(saved and tuple(saved) == (*values, "reserved"), "reservation_conflict")
        return {"status": "reserved", "request": request, "reservation": reservation,
                "account": self.chat.account, "inbox": self.chat.inbox,
                "contact": row["contact"], "conversation": row["conversation"],
                "message": row["message"], "mode": mode,
                "capabilities": ["public_price", "human_handoff"] if mode == "public_information" else
                                ["customer_context", "public_price", "human_handoff"],
                "outgoing_calls": 0}
