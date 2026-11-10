"""Strict native HTTP adapter for the pinned personal recipient only.

This module has no executable entry point. Tests inject a fake opener; no
network request occurs merely by importing or constructing the client.
"""
import base64
import binascii
import hashlib
import json
import re
import urllib.request

from bridge import need
from personal import INSTANCE, ORIGIN, Native
from personal_media import DIRECT, NATIVE_ID, approved_manifest


class PersonalMediaNative(Native):
    """Extend the existing native client only with verified personal JPEGs."""

    def __init__(self, config, phone, *, manifest_bytes, manifest_sha256,
                 opener=None, media_readback=None):
        super().__init__(config)
        need(isinstance(phone, str) and re.fullmatch(r"\+55[0-9]{10,11}", phone), "personal_pin_required")
        self.number = phone[1:]
        self.jid = None
        assets = approved_manifest(manifest_bytes, manifest_sha256)
        self.approved_files = {a["filename"]: (a["bytes"], a["sha256"]) for a in assets}
        self.allowed_native_ids = set()
        if opener is not None:
            need(hasattr(opener, "open"), "invalid_http_opener")
            self.opener = opener
        self.media_readback = media_readback

    def bind_recipient(self, jid):
        allowed = {self.number + "@s.whatsapp.net"}
        if len(self.number) == 13 and self.number[4] == "9":
            allowed.add(self.number[:4] + self.number[5:] + "@s.whatsapp.net")
        need(isinstance(jid, str) and DIRECT.fullmatch(jid) and jid in allowed, "wrong_personal_jid")
        need(self.jid is None or self.jid == jid, "personal_jid_changed")
        self.jid = jid

    def bind_native_id(self, native_id):
        need(isinstance(native_id, str) and NATIVE_ID.fullmatch(native_id), "invalid_native_id")
        self.allowed_native_ids.add(native_id)

    def _allow(self, path, body):
        need(isinstance(body, dict), "invalid_native_body")
        if path == "chat/whatsappNumbers":
            need(body == {"numbers": [self.number]}, "wrong_personal_lookup")
            return
        need(self.jid is not None, "personal_recipient_not_bound")
        number = self.jid.split("@")[0]
        if path == "message/sendText":
            need(set(body) == {"number", "text", "linkPreview"} and body["number"] == number and
                 isinstance(body["text"], str) and 0 < len(body["text"]) <= 250 and
                 body["linkPreview"] is False, "text_outside_personal_pin")
            return
        if path == "message/sendMedia":
            need(set(body) == {"number", "mediatype", "mimetype", "caption", "fileName", "media", "linkPreview"} and
                 body["number"] == number and body["mediatype"] == "image" and
                 body["mimetype"] == "image/jpeg" and body["caption"] == "Tabela" and
                 body["linkPreview"] is False and isinstance(body["fileName"], str) and
                 body["fileName"] in self.approved_files and
                 isinstance(body["media"], str) and len(body["media"]) <= 2_000_000,
                 "media_outside_personal_pin")
            try:
                raw = base64.b64decode(body["media"], validate=True)
            except (ValueError, binascii.Error):
                need(False, "invalid_media_encoding")
            size, sha = self.approved_files[body["fileName"]]
            need(len(raw) == size and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9") and
                 hashlib.sha256(raw).hexdigest() == sha, "unapproved_media_bytes")
            return
        if path == "chat/findMessages":
            where = body.get("where")
            key = where.get("key") if isinstance(where, dict) else None
            native_id = key.get("id") if isinstance(key, dict) else None
            need(native_id in self.allowed_native_ids and
                 body == {"where": {"key": {"id": native_id}}, "limit": 5}, "unbound_native_lookup")
            return
        need(False, "unsupported_operation")

    def __call__(self, path, body):
        self._allow(path, body)
        request = urllib.request.Request(f"{ORIGIN}/{path}/{INSTANCE}", headers=self.headers,
                                         data=json.dumps(body, separators=(",", ":")).encode(), method="POST")
        with self.opener.open(request, timeout=15) as response:
            need(getattr(response, "status", 200) in range(200, 300), "native_http_not_accepted")
            raw = response.read(512001)
            need(len(raw) <= 512000, "native_response_too_large")
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError):
                need(False, "native_response_invalid")

    def read_native(self, native_id):
        need(native_id in self.allowed_native_ids, "unbound_native_lookup")
        value = self("chat/findMessages", {"where": {"key": {"id": native_id}}, "limit": 5})
        records = value.get("messages", {}).get("records", []) if isinstance(value, dict) else []
        need(isinstance(records, list), "native_readback_invalid")
        matches = [row for row in records if isinstance(row, dict) and isinstance(row.get("key"), dict)
                   and row["key"].get("id") == native_id]
        need(len(matches) == 1, "native_record_not_unique")
        row = matches[0]
        key, message = row.get("key"), row.get("message")
        need(isinstance(key, dict) and isinstance(message, dict) and
             key.get("remoteJid") == self.jid and key.get("fromMe") is True,
             "native_record_wrong_recipient")
        result = {"id": native_id, "instance": INSTANCE, "destination": self.jid, "fromMe": True}
        if isinstance(message.get("conversation"), str):
            result["text"] = message["conversation"]
        elif isinstance(message.get("extendedTextMessage"), dict):
            result["text"] = message["extendedTextMessage"].get("text")
        elif isinstance(message.get("imageMessage"), dict):
            image = message["imageMessage"]
            result.update(mime=image.get("mimetype"), filename=image.get("fileName"),
                          caption=image.get("caption"))
            need(callable(self.media_readback), "media_readback_required")
            result["bytes"] = self.media_readback(native_id)
        return result
