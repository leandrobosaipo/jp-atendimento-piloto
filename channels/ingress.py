"""Durable ingress component only: no HTTP listener, dispatcher or clock.

Return values acknowledge local SQLite commit, never processing or delivery.
Credentials must come from the future trusted receiver's private configuration.
"""
from dataclasses import asdict
import hmac
import json
from bridge import Outbox, canonical, evolution_event, instagram_events, need


class Journal:
    def __init__(self, path):
        self.box = Outbox(path)
        with self.box.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS ingress_journal (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE REFERENCES channel_events(event_key))""")

    def _commit(self, events):
        # A batch either commits completely or produces no acknowledgement.
        acknowledgements = []
        with self.box.db() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            for event in events:
                value = canonical(asdict(event.validate()))
                db.execute("INSERT OR IGNORE INTO channel_events(event_key,conversation,event) VALUES(?,?,?)",
                           (event.key, event.conversation, value))
                saved = db.execute("SELECT event FROM channel_events WHERE event_key=?", (event.key,)).fetchone()
                need(saved[0] == value, "event_identity_conflict")
                inserted = db.execute("INSERT OR IGNORE INTO ingress_journal(event_key) VALUES(?)", (event.key,)).rowcount
                sequence = db.execute("SELECT sequence FROM ingress_journal WHERE event_key=?", (event.key,)).fetchone()[0]
                acknowledgements.append({"sequence": sequence, "event_key": event.key, "duplicate": not bool(inserted)})
        return {"status": "committed_locally", "events": acknowledgements}

    def evolution(self, raw, authorization, *, secret, instance):
        need(isinstance(secret, bytes) and len(secret) >= 32, "private_secret_required")
        need(isinstance(authorization, bytes) and hmac.compare_digest(secret, authorization), "unauthenticated")
        need(isinstance(raw, bytes) and len(raw) <= 1_000_000, "invalid_body")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            need(False, "invalid_json")
        return self._commit([evolution_event(body, expected_instance=instance, authenticated=True)])

    def instagram(self, raw, signature, *, app_secret, account):
        return self._commit(instagram_events(raw, signature, app_secret=app_secret, expected_account=account))

    def high_water(self):
        with self.box.db() as db:
            return db.execute("SELECT COALESCE(MAX(sequence),0) FROM ingress_journal").fetchone()[0]

    def read(self, *, after, through, limit=100):
        need(type(after) is int and type(through) is int and 0 <= after <= through, "invalid_cursor")
        need(type(limit) is int and 1 <= limit <= 1000, "invalid_limit")
        with self.box.db() as db:
            return [{"sequence": row[0], "event_key": row[1], "event": json.loads(row[2])}
                    for row in db.execute("""SELECT j.sequence,j.event_key,e.event FROM ingress_journal j
                    JOIN channel_events e ON e.event_key=j.event_key
                    WHERE j.sequence>? AND j.sequence<=? ORDER BY j.sequence LIMIT ?""", (after, through, limit))]
