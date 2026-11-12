"""Build an isolated receiver journal to exercise the real n8n timer, with no network."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from reply_capture import INSTANCE, connect, init

def build(folder):
    src = folder / "source"
    for child in ("access", "data/index", "data/records"):
        (src/child).mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    keyfile = src/"access/key"
    keyfile.write_bytes(key)
    os.chmod(keyfile,0o600)
    os.chown(keyfile,65534,65534)
    fixed = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(src/"data/index/receive.sqlite3") as q:
        q.execute("CREATE TABLE receipts(seq INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE)")
        q.execute("INSERT INTO receipts VALUES(1,'before-test')")
        with connect(folder/"fixture.sqlite3") as archive:
            assert init(q,archive) == 1
        def item(mid, msg, jid="5511000000000@s.whatsapp.net"):
            return {"key":{"fromMe":False,"id":mid,"remoteJid":jid},"message":msg}
        packet = [
            item("fiction-text", {"conversation":"Sim"}),
            item("fiction-emoji", {"conversation":"✅"}),
            item("fiction-react", {"reactionMessage":{"text":"👍","key":{"id":"fiction-notice"}}}),
            item("fiction-remove", {"reactionMessage":{"text":"","key":{"id":"fiction-notice"}}}),
            item("fiction-sticker", {"stickerMessage":{}}),
            item("fiction-emoji", {"conversation":"✅"}),
            item("fiction-group", {"conversation":"sim"},"120000000@g.us"),
        ]
        raw=json.dumps({"instance":INSTANCE,"event":"messages.upsert","data":packet}).encode()
        name=INSTANCE.encode()
        rid=hashlib.sha256(len(name).to_bytes(4,"big")+name+raw).hexdigest()
        meta={"version":1,"source":"evolution","instance":INSTANCE,"receivedAt":fixed,
              "rawSha256":hashlib.sha256(raw).hexdigest(),"workflowId":None,"executionId":None}
        iv=secrets.token_bytes(12)
        sealed=AESGCM(key).encrypt(iv,raw,json.dumps(meta,ensure_ascii=False,separators=(",",":")).encode())
        b64=lambda v:base64.b64encode(v).decode()
        f=src/"data/records"/(rid+".json")
        f.write_text(json.dumps(dict(version=1,meta=meta,iv=b64(iv),ciphertext=b64(sealed[:-16]),tag=b64(sealed[-16:]))))
        os.chmod(f,0o600)
        os.chown(f,65534,65534)
        q.execute("INSERT INTO receipts VALUES(2,?)",(rid,))
