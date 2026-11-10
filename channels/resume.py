"""Resume an approved two-part plan from its sealed, destination-bound receipt."""
import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from bridge import digest, need
from dispatch_contract import INSTANCE


def rows(path, sql, params):
    with closing(sqlite3.connect(Path(path).as_uri()+"?mode=ro",uri=True,timeout=3)) as db:
        db.row_factory=sqlite3.Row
        return [dict(r) for r in db.execute(sql,params)]


def one(values):
    need(len(values)==1,"continuation_identity_ambiguous")
    return values[0]


def sha(value):
    return hashlib.sha256(value).hexdigest()


class ResumePlan:
    def __init__(self, cfg, conversation, image_bytes):
        self.cfg=cfg
        self.conversation=conversation
        self.image_bytes=image_bytes

    def prepare(self, request):
        cfg=self.cfg
        need(set(request)=={"record","workflow","execution","epoch"} and
             request["workflow"]==cfg["workflow"] and request["epoch"]==cfg["epoch"] and
             str(UUID(cfg["epoch"]))==cfg["epoch"] and re.fullmatch(r"[0-9]{1,20}",request["execution"]) and
             re.fullmatch(r"[a-f0-9]{64}",request["record"]),"invalid_continuation_scope")
        record=request["record"]
        matches=rows(cfg["ledger"],"""SELECT DISTINCT a.attempt,a.source,a.destination,a.epoch,a.role,c.content_hash,
            g.next,g.payload_hash,g.until
            FROM dispatch_receipts r JOIN dispatch_attempts a ON a.attempt=r.attempt
            JOIN dispatch_next_grants g ON g.first=a.attempt
            JOIN dispatch_content c ON c.attempt=a.attempt WHERE (r.record=? OR r.echo=?)
            AND a.state IN ('delivered','read') AND r.state IN ('delivered','read') AND a.epoch=?""",
            (record,record,cfg["epoch"]))
        # A batched receipt for multiple text messages requires individual reconciliation.
        if not matches:return None
        prior=one(matches)
        need(json.loads(prior["role"])==cfg["text_role"] and cfg["text_role"][2]=="sendText" and
             str(UUID(prior["source"]))==prior["source"] and re.fullmatch(r"[1-9][0-9]{7,14}@s\.whatsapp\.net",prior["destination"]),
             "unbound_first_part")
        reservation=one(rows(cfg["reservations"],"SELECT * FROM reservations WHERE reservation=? AND state='reserved'",(prior["source"],)))
        intake=one(rows(cfg["intakes"],"SELECT * FROM intakes WHERE request=? AND record=? AND state IN ('ready','public_only')",
                        (reservation["request"],reservation["record"])))
        parts=rows(cfg["plans"],"SELECT * FROM held_parts WHERE reservation=? ORDER BY position",(prior["source"],))
        need([p["position"] for p in parts]==[1,2],"incomplete_continuation")
        text,art=parts
        identity=(cfg["account"],cfg["inbox"],intake["contact"],intake["conversation"],intake["message"])
        for row in (reservation,text,art):
            need(tuple(row[k] for k in ("account","inbox","contact","conversation","message"))==identity,"continuation_chat_changed")
        need(all(p["state"]=="held_before_transport" and sha(p["content"].encode())==p["content_sha256"] for p in parts)
             and text["part_id"]==prior["attempt"] and text["asset_id"] is None and
             digest(["text",text["content"]])==prior["content_hash"] and
             str(UUID(art["part_id"]))==art["part_id"] and text["part_id"]!=art["part_id"],"continuation_plan_changed")
        address=sha(prior["destination"].encode())
        need(address==intake["address_hash"] and not intake["reason"],"continuation_recipient_changed")
        contact=one(rows(cfg["intakes"],"SELECT * FROM contacts WHERE address_hash=?",(address,)))
        need(contact["state"]=="ready" and (contact["contact"],contact["conversation"])==identity[2:4],
             "continuation_contact_changed")

        def human_free():
            conv=self.conversation(identity[3])
            meta=conv["meta"];sender=meta["sender"]
            need((conv["account_id"],conv["inbox_id"],sender["id"],conv["id"])==identity[:4] and
                 sender["identifier"]=="jp-intake-"+address,"continuation_recipient_changed")
            need(conv["status"]=="open" and meta.get("assignee") is None and meta.get("team") is None and
                 isinstance(conv["labels"],list) and "jp-atendimento-humano" not in conv["labels"],"human_ownership")

        need(art["part_id"]==prior["next"],"approved_part_changed")
        raw=self.image_bytes(art["asset_id"],art["asset_sha256"])
        need(raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9") and sha(raw)==art["asset_sha256"],"approved_art_changed")
        payload=dict(number=prior["destination"].split("@")[0],mediatype="image",mimetype="image/jpeg",
                     caption=art["content"],fileName="tabela.jpg",media=base64.b64encode(raw).decode())
        body=dict(instance=INSTANCE,workflow=cfg["workflow"],node=cfg["node"],execution=request["execution"],
                  source_event_id=prior["source"],attempt_id=art["part_id"],operation="sendMedia",
                  native_path="/message/sendMedia/"+INSTANCE,payload=payload)
        need(digest(payload)==prior["payload_hash"],"approved_payload_changed")
        return body,prior,human_free
