"""Authorize the first public response from the original claimed record and its durable plan."""
import base64
from datetime import datetime, timezone
import re
from uuid import UUID

from bridge import digest, need
from dispatch_contract import INSTANCE
from resume import one, rows, sha


def prepare_first(cfg, request, read_record, conversation, image_bytes, now):
    need(set(request) == {"record", "workflow", "execution", "epoch", "sender"} and
         request["workflow"] == cfg["workflow"] and request["epoch"] == cfg["epoch"] and
         str(UUID(cfg["epoch"])) == cfg["epoch"] and re.fullmatch(r"[0-9]{1,20}", request["execution"]) and
         re.fullmatch(r"[0-9a-f]{64}", request["record"]) and
         re.fullmatch(r"55[1-9][0-9]{9,10}@s\.whatsapp\.net", request["sender"]), "invalid_first_scope")
    record, execution, sender = request["record"], request["execution"], request["sender"]
    address = sha(sender.encode())
    def check_claim():
        owned = rows(cfg["queue"], """SELECT r.id FROM receipts r JOIN owner o ON o.slot=1
            WHERE r.id=? AND r.execution=? AND r.state='claimed' AND r.epoch=o.epoch AND o.epoch=?
            AND (o.fixture IS NULL OR o.fixture=r.id)""", (record, execution, cfg["epoch"]))
        need(len(owned) == 1, "intake_owner_changed")
    check_claim()
    meta, event = read_record(record)
    data = event["data"]
    received = datetime.fromisoformat(meta["receivedAt"].replace("Z", "+00:00"))
    need(received.utcoffset() == timezone.utc.utcoffset(received) and
         cfg["not_before"] <= received.timestamp() <= now and
         str(data["messageTimestamp"]).isdigit() and cfg["not_before"] <= int(data["messageTimestamp"]) <= now,
         "outside_intake_window")
    need(event["instance"] == INSTANCE and event["event"].lower().replace("_", ".") == "messages.upsert" and
         data["key"]["fromMe"] is False and not data["key"].get("participant") and
         sha(data["instanceId"].encode()) == cfg["instance_hash"], "unverified_first_event")
    intake = one(rows(cfg["intakes"], """SELECT i.*, r.decision, r.generation FROM intakes i
        JOIN intake_identity_reads r ON r.request=i.request AND r.record=i.record AND r.execution=i.execution
        WHERE i.record=? AND i.execution=? AND i.workflow=?""", (record, execution, cfg["workflow"])))
    need((intake["state"], intake["decision"]) in {("public_only","no_customer_match"),("ready","identified")} and
         intake["generation"] > 0 and not intake["reason"] and intake["address_hash"] == address and
         intake["raw_hash"] == meta["rawSha256"] and intake["native_hash"] == sha(data["key"]["id"].encode()),
         "first_identity_changed")
    reservation = one(rows(cfg["reservations"], "SELECT * FROM reservations WHERE request=? AND record=? AND state='reserved'",
                           (intake["request"], record)))
    parts = rows(cfg["plans"], "SELECT * FROM held_parts WHERE reservation=? ORDER BY position", (reservation["reservation"],))
    need([p["position"] for p in parts] == [1,2] and all(p["state"] == "held_before_transport" and
         sha(p["content"].encode()) == p["content_sha256"] for p in parts), "invalid_first_plan")
    text, art = parts
    ids = (cfg["account"], cfg["inbox"], intake["contact"], intake["conversation"], intake["message"])
    need(all(tuple(row[k] for k in ("account","inbox","contact","conversation","message")) == ids
             for row in [reservation,*parts]) and reservation["text_hash"] == intake["text_hash"] and
         not text["asset_id"] and text["part_id"] != art["part_id"], "first_plan_binding_changed")
    contact = one(rows(cfg["intakes"], "SELECT * FROM contacts WHERE address_hash=? AND state='ready'", (address,)))
    need((contact["contact"], contact["conversation"]) == ids[2:4], "first_contact_changed")

    def human_free():
        check_claim()
        conv = conversation(ids[3])
        meta = conv["meta"]
        need((conv["account_id"],conv["inbox_id"],meta["sender"]["id"],conv["id"]) == ids[:4] and
             meta["sender"]["identifier"] == "jp-intake-"+address, "first_chat_identity_changed")
        need(conv["status"] == "open" and isinstance(conv["labels"],list) and not meta.get("assignee") and
             not meta.get("team") and "jp-atendimento-humano" not in conv["labels"], "human_ownership")

    image = image_bytes(art["asset_id"], art["asset_sha256"])
    need(sha(image) == art["asset_sha256"] and image.startswith(b"\xff\xd8") and image.endswith(b"\xff\xd9"), "approved_art_changed")
    number = sender.split("@")[0]
    media = dict(number=number,mediatype="image",mimetype="image/jpeg",fileName="tabela.jpg",caption=art["content"],
                 media=base64.b64encode(image).decode())
    body = dict(instance=INSTANCE, workflow=cfg["workflow"], execution=execution, node=cfg["node"], operation="sendText",
                native_path="/message/sendText/"+INSTANCE, source_event_id=reservation["reservation"], attempt_id=text["part_id"],
                payload={"number":number, "text":text["content"]})
    next_part = {"id":art["part_id"], "payload_sha256":digest(media)}
    return body, next_part, human_free
