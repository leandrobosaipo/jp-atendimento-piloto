"""One physical WhatsApp dispatcher for the Central and the Chatwoot bridge."""
import base64
import datetime as dt
import hashlib
import time
from zoneinfo import ZoneInfo

from bridge import canonical, digest, need
from dispatch_contract import INSTANCE, accepted_response, validate_request
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

    def deliver(self,body,roles):
        validate_request(body,roles=roles)
        attempt=body["attempt_id"];source=body["source_event_id"];payload=body["payload"]
        body_hash=digest(payload);role=canonical([body["workflow"],body["node"],body["operation"]])
        with self.fence.lock():
            with self.box.db() as db:
                prev=db.execute("SELECT * FROM dispatch_attempts WHERE attempt=?",(attempt,)).fetchone()
                if prev:
                    need((prev["source"],prev["role"],prev["body_hash"])==(source,role,body_hash),"attempt_conflict")
                    if prev["state"] in ("accepted","delivered","read"):
                        return 201,accepted_response({"key":{"id":prev["native"],"fromMe":True,"remoteJid":prev["destination"]}},prev["destination"])
                    return 409,{"status":"delivery_unknown"}
                now=int(self.clock())
                owner=db.execute("SELECT owner FROM owner_epoch").fetchone()[0]
                need(owner,"no_sender")
                grant=self.authorize(body,owner,now)
                jid=grant["destination_jid"]
                need(grant["epoch"]==owner and grant["until"]>now and jid==payload["number"]+"@s.whatsapp.net","invalid_dispatch_proof")
                need(grant["payload_sha256"]==body_hash,"payload_changed")
                if body["operation"]=="sendMedia":
                    raw=base64.b64decode(payload["media"],validate=True)
                    need(hashlib.sha256(raw).hexdigest() in grant["approved_asset_hashes"],"unapproved_asset")
                db.execute("BEGIN IMMEDIATE")
                pending=db.execute("SELECT 1 FROM dispatch_attempts WHERE destination=? AND state IN ('sending','unknown','accepted') LIMIT 1",(jid,)).fetchone()
                need(not pending,"previous_delivery_unresolved")
                prior=grant.get("prior_attempt_id")
                if prior:
                    completed=db.execute("SELECT state,destination,source FROM dispatch_attempts WHERE attempt=?",(prior,)).fetchone()
                    need(completed and completed["state"] in ("delivered","read") and completed["destination"]==jid and completed["source"]==source,"previous_part_unconfirmed")
                need(not db.execute("SELECT 1 FROM dispatch_attempts WHERE source=? AND body_hash=?",(source,body_hash)).fetchone(),"duplicate_business_effect")
                self.check_budget(db,now,grant)
                self.verify_number(payload["number"], jid)
                now=int(self.clock())
                need(self.authorize(body,owner,now)==grant and grant["until"]>now,"dispatch_proof_changed")
                self.check_budget(db,now,grant)
                db.execute("INSERT INTO dispatch_attempts VALUES(?,?,?,?,?,?,?,'sending',NULL)",(attempt,source,role,body_hash,jid,owner,now))
            try:
                reply=accepted_response(self.transport(body["native_path"],payload),jid)
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
            proof=verify(dict(row))
            need(proof.get("instance")==INSTANCE and proof.get("native")==native and proof.get("fromMe") is True and
                 proof.get("destination")==row["destination"] and proof.get("payload_sha256")==row["body_hash"] and
                 proof.get("state") in ("delivered","read"),"unverified_delivery")
            state="read" if "read" in (row["state"],proof["state"]) else "delivered"
            db.execute("UPDATE dispatch_attempts SET state=? WHERE attempt=?",(state,row["attempt"]))
            return state
