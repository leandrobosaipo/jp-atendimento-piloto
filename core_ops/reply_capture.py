"""Retain new direct replies from the JP receiver without scheduling any effect."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

ROOT = Path(os.environ.get("JP_CAPTURE_SOURCE", "/var/lib/jp-migracao-revisao/entrada-operacional"))
DB = Path(os.environ.get("JP_CAPTURE_DB", "/var/lib/jp-reply-capture/replies.sqlite3"))
INSTANCE = "jp-protese-atendimento"
DIRECT = re.compile(r"^[0-9]+(?::[0-9]+)?@(s\.whatsapp\.net|lid)$")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def connect(path=DB):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=8)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS cursor(slot INTEGER PRIMARY KEY, seq INTEGER NOT NULL, record_id TEXT);
      CREATE TABLE IF NOT EXISTS captured(
        instance TEXT NOT NULL, message_id TEXT NOT NULL, seq INTEGER NOT NULL, record_id TEXT NOT NULL,
        envelope_pos INTEGER, item_pos INTEGER, kind TEXT, disposition TEXT, quoted_id TEXT,
        address_hashes TEXT, content_sha256 TEXT, received_at TEXT,
        UNIQUE(instance,message_id,content_sha256));
    """)
    return con


def init(queue, db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = queue.execute("SELECT seq,id FROM receipts ORDER BY seq DESC LIMIT 1").fetchone() or (0, None)
        db.execute("INSERT OR IGNORE INTO cursor VALUES(1,?,?)", row)
        return db.execute("SELECT seq FROM cursor WHERE slot=1").fetchone()[0]


def classify(message):
    reaction = message.get("reactionMessage")
    if isinstance(reaction, dict):
        text = reaction.get("text")
        quoted = (reaction.get("key") or {}).get("id")
        if not isinstance(quoted, str) or not quoted or not isinstance(text, str):
            return "reaction", "review", "", ""
        return "reaction", ("removed" if not text else intent(text)), quoted, text
    if "stickerMessage" in message:
        return "sticker", "review", "", json.dumps(message["stickerMessage"].get("fileSha256"), sort_keys=True)
    extended = message.get("extendedTextMessage") or {}
    text = message.get("conversation") or extended.get("text")
    if isinstance(text, str) and text.strip():
        quoted = (extended.get("contextInfo") or {}).get("stanzaId") or ""
        emoji = not any(c.isalnum() for c in text) and any(unicodedata.category(c) == "So" for c in text)
        return "emoji_text" if emoji else "text", intent(text), quoted, text
    return "other", "review", "", ""


def intent(text):
    t = unicodedata.normalize("NFC", text).casefold().replace("\ufe0f", "").strip()
    t = "".join(c for c in t if not 0x1f3fb <= ord(c) <= 0x1f3ff)
    return "possible_confirmation" if t in {"👍", "✅", "sim", "confirmo", "confirmado"} else "review"


def tick(queue, db, reader):
    counts = {}
    with db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute("SELECT seq,record_id FROM cursor WHERE slot=1").fetchone()
        if cursor is None:
            raise RuntimeError("capture_not_initialized")
        water, anchor = cursor
        if anchor is not None and queue.execute("SELECT id FROM receipts WHERE seq=?", (water,)).fetchone() != (anchor,):
            raise RuntimeError("receiver_cursor_diverged")
        rows = queue.execute("SELECT seq,id FROM receipts WHERE seq>? ORDER BY seq LIMIT 32",(water,)).fetchall()
        for seq, rid in rows:
            meta, raw = reader(rid)
            for ei, env in enumerate(raw if isinstance(raw, list) else [raw]):
                if not isinstance(env, dict) or env.get("instance") != INSTANCE or str(env.get("event")).lower().replace("_",".") != "messages.upsert":
                    continue
                batch = env.get("data")
                for di, data in enumerate(batch if isinstance(batch, list) else [batch]):
                    if not isinstance(data,dict):
                        continue
                    key = data.get("key") or {}
                    mid, jid = key.get("id"), key.get("remoteJid")
                    if key.get("fromMe") is not False or not isinstance(mid, str) or not mid or not isinstance(jid,str) or not DIRECT.fullmatch(jid):
                        continue
                    kind, disposition, quoted, content = classify(data.get("message") or {})
                    aliases = sorted({digest(a) for a in (jid,key.get("remoteJidAlt"),data.get("remoteJidAlt")) if isinstance(a,str) and DIRECT.fullmatch(a)})
                    fingerprint = digest(json.dumps([kind,disposition,quoted,content,aliases],ensure_ascii=False))
                    prior = db.execute("SELECT 1 FROM captured WHERE instance=? AND message_id=? LIMIT 1",(INSTANCE,mid)).fetchone()
                    row = db.execute("INSERT OR IGNORE INTO captured VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (INSTANCE,mid,seq,rid,ei,di,kind,"changed_review" if prior else disposition,quoted,json.dumps(aliases),fingerprint,meta.get("receivedAt")))
                    label = ("changed_review" if prior else kind) if row.rowcount else "duplicate"
                    counts[label] = counts.get(label,0)+1
            db.execute("UPDATE cursor SET seq=?,record_id=? WHERE slot=1",(seq,rid))
        return {"watermark":rows[-1][0] if rows else water,"processed":len(rows),"kinds":counts}


if __name__ == "__main__":
    os.umask(0o077)
    group = (parser := argparse.ArgumentParser()).add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true")
    group.add_argument("--tick", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect((ROOT/"data/index/receive.sqlite3").as_uri()+"?mode=ro",uri=True) as q, connect() as db:
        if args.init:
            result = {"watermark":init(q,db)}
        else:
            import sys
            sys.path.insert(0,"/opt/jp-dispatch/current")
            from dispatch_service import read_record
            result = tick(q,db,lambda rid:read_record(ROOT,rid,65534))
        print(json.dumps(result))
