"""Authenticate the Dell ingress envelope and correlate native echoes and receipts by ID."""
import base64
import datetime as dt
import hashlib
import json
import re
import struct

from bridge import digest, need
from dispatch_contract import INSTANCE


def sha(value):
    return hashlib.sha256(value).hexdigest()


def submitted_content(payload):
    if "text" in payload:
        return digest(["text", payload["text"]])
    return digest([payload["mimetype"], payload.get("caption", ""), sha(base64.b64decode(payload["media"], validate=True))])


def open_record(record_id, sealed, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    meta = sealed["meta"]
    need(sealed["version"] == meta["version"] == 1 and meta["source"] == "evolution" and
         meta["instance"] == INSTANCE and meta["workflowId"] is None and meta["executionId"] is None, "receipt_provenance")
    def decode(name):
        return base64.b64decode(sealed[name], validate=True)
    try:
        raw = AESGCM(key).decrypt(decode("iv"), decode("ciphertext") + decode("tag"),
                                  json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode())
    except InvalidTag:
        need(False, "receipt_authentication_failed")
    instance = INSTANCE.encode()
    need(sha(raw) == meta["rawSha256"] and sha(struct.pack(">I", len(instance)) + instance + raw) == record_id,
         "receipt_content_changed")
    return meta, json.loads(raw)


def match_event(record, attempt, kind, instance_hash, mapping):
    meta, envelope = record
    if isinstance(envelope, list):
        need(len(envelope) == 1, "ambiguous_envelope")
        envelope = envelope[0]
    need(envelope["instance"] == INSTANCE and envelope["event"].lower().replace("_", ".") == kind,
         "unexpected_native_event")
    at = dt.datetime.fromisoformat(meta["receivedAt"].replace("Z", "+00:00"))
    need(at.utcoffset() == dt.timedelta(0) and at.timestamp() >= attempt["attempted"], "receipt_precedes_attempt")
    data = envelope["data"]
    rows = data if isinstance(data, list) else [data]
    matched = [r for r in rows if isinstance(r, dict) and
               (r.get("keyId") or r.get("key", {}).get("id")) == attempt["native"]]
    need(len(matched) == 1, "native_event_not_unique")
    row = matched[0]
    key = row.get("key", {})
    native = attempt["native"]
    need(all(x == native for x in (row.get("keyId",native),key.get("id",native))) and
         row.get("fromMe",key.get("fromMe")) is True and key.get("fromMe",True) is True, "wrong_direction_or_id")
    if kind == "messages.update" or "instanceId" in row:
        need(sha(row["instanceId"].encode()) == instance_hash, "foreign_instance")
    addresses = {v for v in (row.get("remoteJid"), key.get("remoteJid"), key.get("remoteJidAlt"),
                              row.get("remoteJidAlt")) if v is not None}
    need(addresses, "native_destination_missing")
    for address in addresses:
        if address == attempt["destination"]:
            continue
        need(re.fullmatch(r"[0-9]{6,20}@lid", address) and mapping is not None and
             mapping["instance"] == instance_hash and mapping["attempt"] == attempt["attempt"] and
             mapping["native"] == native and mapping["pn"] == mapping["reverse_pn"] == sha(attempt["destination"].encode()) and
             mapping["lid"] == mapping["forward_lid"] == sha(address.encode()), "native_destination_mismatch")
    return row


def verify_echo(row, content):
    message = row["message"]
    kinds = [k for k in ("conversation","extendedTextMessage","imageMessage","videoMessage") if k in message]
    need(len(kinds) == 1, "ambiguous_native_content")
    kind = kinds[0]
    if kind in ("conversation", "extendedTextMessage"):
        actual = digest(["text", message[kind] if kind == "conversation" else message[kind]["text"]])
    else:
        media = message[kind]
        native_hash = base64.b64decode(media["fileSha256"], validate=True)
        need(len(native_hash) == 32, "invalid_media_digest")
        actual = digest([media["mimetype"], media.get("caption",""), native_hash.hex()])
    need(actual == content, "native_content_changed")


def delivery_state(update):
    status = update.get("status") or update.get("update",{}).get("status")
    need(status in ("READ","DELIVERY_ACK"), "delivery_unconfirmed")
    return "read" if status == "READ" else "delivered"


def evidence(attempt, content, echo, ack, instance_hash, mapping=None):
    verify_echo(match_event(echo, attempt, "messages.upsert", instance_hash, mapping), content)
    state = delivery_state(match_event(ack, attempt, "messages.update", instance_hash, mapping))
    return dict(instance=INSTANCE, native=attempt["native"], fromMe=True, destination=attempt["destination"],
                payload_sha256=attempt["body_hash"], state=state)
