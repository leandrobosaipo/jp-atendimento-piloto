"""One physical WhatsApp dispatcher for the Central and the Chatwoot bridge."""
import base64
import datetime as dt
import hashlib
import re
import time
from zoneinfo import ZoneInfo

from bridge import canonical, digest, need
from dispatch_contract import INSTANCE, accepted_response, recipient_binding, validate_request
from dispatch_receipt import submitted_content
from evolution import Fence


class Dispatcher:
    def __init__(self, box, authorize, transport, *, verify_number, clock=time.time):
        self.box=box
        self.fence=Fence(box)
        self.authorize=authorize
        self.transport=transport
        self.verify_number=verify_number
        self.clock=clock
        with box.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS dispatch_attempts (
                    attempt TEXT PRIMARY KEY, source TEXT NOT NULL, role TEXT NOT NULL, body_hash TEXT NOT NULL,
                    destination TEXT NOT NULL, epoch TEXT NOT NULL, attempted INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('sending','unknown','accepted','delivered','read')),
                    native TEXT UNIQUE, UNIQUE(source,body_hash));
                CREATE INDEX IF NOT EXISTS dispatch_usage ON dispatch_attempts(attempted);
                CREATE INDEX IF NOT EXISTS dispatch_pending ON dispatch_attempts(destination,state);
                CREATE TABLE IF NOT EXISTS dispatch_content (attempt TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_next_grants (
                    first TEXT PRIMARY KEY, next TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, until INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_resume_holds (next TEXT PRIMARY KEY, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_first_holds (attempt TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS dispatch_observations (
                    attempt TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('echo','delivered','read')),
                    record TEXT NOT NULL, PRIMARY KEY(attempt,kind));
                CREATE TABLE IF NOT EXISTS dispatch_receipts (record TEXT NOT NULL, echo TEXT NOT NULL,
                    attempt TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(record,attempt));
                CREATE TABLE IF NOT EXISTS dispatch_baseline (
                    id TEXT PRIMARY KEY, attempted INTEGER NOT NULL, weight INTEGER NOT NULL CHECK(weight>0));
                CREATE INDEX IF NOT EXISTS dispatch_baseline_usage ON dispatch_baseline(attempted);
                CREATE TABLE IF NOT EXISTS dispatch_snapshot (
                    slot INTEGER PRIMARY KEY CHECK(slot=1), marker TEXT NOT NULL, cutoff INTEGER NOT NULL);
            """)

    def import_baseline(self, marker, cutoff, rows):
        """Replace only the source budget projection while fenced. Never overwrite local attempts."""
        need(type(cutoff) is int and 0 < cutoff <= self.clock(), "invalid_cutoff")
        rows = sorted(rows)
        need(marker == digest([cutoff, rows]), "invalid_budget_snapshot")
        with self.fence.lock(),self.box.db() as db:
            need(db.execute("SELECT owner FROM owner_epoch").fetchone()[0] is None,"live_budget_replacement")
            previous = db.execute("SELECT marker,cutoff FROM dispatch_snapshot").fetchone()
            if previous and previous["marker"] == marker:
                return
            need(not previous or cutoff > previous["cutoff"], "stale_budget_snapshot")
            db.execute("DELETE FROM dispatch_baseline")
            for entry in rows:
                key,at,count=entry
                need(isinstance(key,str) and key and type(at) is int and type(count) is int and count>0,"invalid_baseline")
                db.execute("INSERT INTO dispatch_baseline VALUES(?,?,?)",(key,at,count))
            db.execute("INSERT OR REPLACE INTO dispatch_snapshot VALUES(1,?,?)",(marker,cutoff))

    @staticmethod
    def check_budget(db,now,grant):
        current=db.execute("SELECT marker FROM dispatch_snapshot WHERE slot=1").fetchone()
        need(current and current[0]==grant["budget_marker"],"unreconciled_budget")
        at=dt.datetime.fromtimestamp(now,ZoneInfo("America/Cuiaba"))
        cutoffs={"minute": now-59, "hour": now-3599,
                 "day": int(at.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())}
        for period,stamp in cutoffs.items():
            cap=grant["limits"][period]
            need(type(cap) is int and cap>0,"invalid_budget")
            used=db.execute("SELECT count(*) FROM dispatch_attempts WHERE attempted>=?",(stamp,)).fetchone()[0]
            used+=db.execute("SELECT coalesce(sum(weight),0) FROM dispatch_baseline WHERE attempted>=?",(stamp,)).fetchone()[0]
            need(used<cap,"budget_exhausted")

    def deliver(self,body,roles,*,continuation=None):
        validate_request(body,roles=roles)
        authorize=self.authorize if continuation is None else continuation[0]
        attempt=body["attempt_id"];source=body["source_event_id"];payload=body["payload"]
        body_hash=digest(payload);role=canonical([body["workflow"],body["node"],body["operation"]])
        with self.fence.lock():
            with self.box.db() as db:
                prev=db.execute("SELECT * FROM dispatch_attempts WHERE attempt=?",(attempt,)).fetchone()
                if prev:
                    need((prev["source"],prev["role"],prev["body_hash"])==(source,role,body_hash),"attempt_conflict")
                    if prev["state"] in ("accepted","delivered","read"):
                        return 201,accepted_response({"key":{"id":prev["native"],"fromMe":True,"remoteJid":prev["destination"]}},prev["destination"],payload["number"])
                    return 409,{"status":"delivery_unknown"}
                now=int(self.clock())
                owner=db.execute("SELECT owner FROM owner_epoch").fetchone()[0]
                need(owner,"no_sender")
                grant=authorize(body,owner,now)
                jid=grant["destination_jid"]
                need(grant["epoch"]==owner and grant["until"]>now and jid==payload["number"]+"@s.whatsapp.net","invalid_dispatch_proof")
                need(grant["payload_sha256"]==body_hash,"payload_changed")
                if body["operation"]=="sendMedia":
                    raw=base64.b64decode(payload["media"],validate=True)
                    need(hashlib.sha256(raw).hexdigest() in grant["approved_asset_hashes"],"unapproved_asset")
                self.check_budget(db,now,grant)
                submitted=payload["number"]
                jid=self.verify_number(submitted,jid)
                need(isinstance(jid,str),"unverified_native_identity")
                if grant.get("require_exact_recipient"):
                    need(jid==grant["destination_jid"],"native_sender_changed")
                recipient_binding(submitted,jid)
                db.execute("BEGIN IMMEDIATE")
                pending=db.execute("SELECT 1 FROM dispatch_attempts WHERE destination=? AND state IN ('sending','unknown','accepted') LIMIT 1",(jid,)).fetchone()
                need(not pending,"previous_delivery_unresolved")
                prior=grant.get("prior_attempt_id")
                if prior:
                    completed=db.execute("SELECT state,destination,source FROM dispatch_attempts WHERE attempt=?",(prior,)).fetchone()
                    need(completed and completed["state"] in ("delivered","read") and completed["destination"]==jid and completed["source"]==source,"previous_part_unconfirmed")
                need(not db.execute("SELECT 1 FROM dispatch_attempts WHERE source=? AND body_hash=?",(source,body_hash)).fetchone(),"duplicate_business_effect")
                self.check_budget(db,now,grant)
                now=int(self.clock())
                if continuation is not None:
                    continuation[1]()
                need(authorize(body,owner,int(self.clock()))==grant and grant["until"]>now,"dispatch_proof_changed")
                self.check_budget(db,now,grant)
                need(grant["until"]>int(self.clock()),"dispatch_proof_expired")
                db.execute("INSERT INTO dispatch_attempts VALUES(?,?,?,?,?,?,?,'sending',NULL)",(attempt,source,role,body_hash,jid,owner,now))
                db.execute("INSERT INTO dispatch_content VALUES(?,?)",(attempt,submitted_content(payload)))
                nxt=grant.get("next_part")
                if nxt is not None:
                    need(body["operation"]=="sendText" and isinstance(nxt,dict) and set(nxt)=={"id","payload_sha256"} and
                         isinstance(nxt["id"],str) and re.fullmatch(r"[0-9a-f-]{36}",nxt["id"]) and nxt["id"]!=attempt and
                         isinstance(nxt["payload_sha256"],str) and re.fullmatch(r"[0-9a-f]{64}",nxt["payload_sha256"]),
                         "invalid_continuation_grant")
                    db.execute("INSERT INTO dispatch_next_grants VALUES(?,?,?,?)",(attempt,nxt["id"],nxt["payload_sha256"],grant["until"]))
            try:
                native_payload={**payload,"number":jid.split("@")[0]}
                reply=accepted_response(self.transport(body["native_path"],native_payload),jid,submitted)
                with self.box.db() as db:
                    need(db.execute("UPDATE dispatch_attempts SET state='accepted',native=? WHERE attempt=? AND state='sending'",
                                    (reply["key"]["id"],attempt)).rowcount==1,"attempt_changed")
                return 201,reply
            except Exception:
                with self.box.db() as db:db.execute("UPDATE dispatch_attempts SET state='unknown' WHERE attempt=? AND state='sending'",(attempt,))
                return 409,{"status":"delivery_unknown"}

    def observe(self, native, verify):
        """verify must read the authenticated provider record, observed destination and original content."""
        with self.fence.lock(),self.box.db() as db:
            row=db.execute("SELECT * FROM dispatch_attempts WHERE native=?",(native,)).fetchone()
            need(row and row["state"] in ("accepted","delivered","read"),"unbound_native_receipt")
            content=db.execute("SELECT content_hash FROM dispatch_content WHERE attempt=?",(row["attempt"],)).fetchone()
            proof=verify({**dict(row),"content_hash":content[0] if content else None})
            need(proof.get("instance")==INSTANCE and proof.get("native")==native and proof.get("fromMe") is True and
                 proof.get("destination")==row["destination"] and proof.get("payload_sha256")==row["body_hash"] and
                 proof.get("state") in ("delivered","read"),"unverified_delivery")
            state="read" if "read" in (row["state"],proof["state"]) else "delivered"
            if "record" in proof:
                db.execute("INSERT OR IGNORE INTO dispatch_receipts VALUES(?,?,?,?)",(proof["record"],proof["echo"],row["attempt"],proof["state"]))
                prior=db.execute("SELECT echo,attempt,state FROM dispatch_receipts WHERE record=? AND attempt=?",(proof["record"],row["attempt"])).fetchone()
                need(tuple(prior)==(proof["echo"],row["attempt"],proof["state"]),"receipt_record_conflict")
            db.execute("UPDATE dispatch_attempts SET state=? WHERE attempt=?",(state,row["attempt"]))
            return state
