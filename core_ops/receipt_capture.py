"""Forward authenticated provider delivery receipts on the existing n8n tick."""
import json
import subprocess
import time

import reply_capture as capture

SOCKET = "/run/jp-dispatch/dispatch.sock"


def _event(reader, record_id):
    _, raw = reader(record_id)
    envelopes = raw if isinstance(raw, list) else [raw]
    eligible = []
    for envelope in envelopes:
        if not isinstance(envelope, dict) or envelope.get("instance") != capture.INSTANCE:
            continue
        if str(envelope.get("event", "")).lower().replace("_", ".") != "messages.update":
            continue
        rows = envelope.get("data")
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = row.get("key") or {}
            status = row.get("status") or (row.get("update") or {}).get("status")
            address = row.get("remoteJid") or key.get("remoteJid") or row.get("remoteJidAlt") or key.get("remoteJidAlt")
            if status in ("DELIVERY_ACK", "READ") and isinstance(key.get("id") or row.get("keyId"), str) and isinstance(address, str):
                eligible.append(row)
    return bool(eligible)


def _forward(record_id):
    payload = json.dumps({"record": record_id}).encode()
    command = ["curl", "--fail-with-body", "--silent", "--show-error", "--max-time", "10",
               "--unix-socket", SOCKET, "-H", "Content-Type: application/json",
               "--data-binary", payload.decode(), "http://localhost/receipt-event"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError("receipt_observer_unavailable")
    reply = json.loads(result.stdout)
    if not isinstance(reply, dict) or not all(isinstance(reply.get(key), int) for key in ("matched", "waiting", "confirmed")):
        raise RuntimeError("invalid_receipt_observer_reply")
    return reply


def tick(queue, db, reader):
    db.execute("CREATE TABLE IF NOT EXISTS receipt_cursor(slot INTEGER PRIMARY KEY, seq INTEGER NOT NULL, record TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS receipt_pending(record TEXT PRIMARY KEY, next_check INTEGER NOT NULL, tries INTEGER NOT NULL DEFAULT 0)")
    cursor = db.execute("SELECT seq,record FROM receipt_cursor WHERE slot=1").fetchone()
    if cursor is None:
        row = db.execute("SELECT seq,record_id FROM cursor WHERE slot=1").fetchone()
        if row is None:
            row = queue.execute("SELECT seq,id FROM receipts ORDER BY seq DESC LIMIT 1").fetchone() or (0, None)
        db.execute("INSERT INTO receipt_cursor VALUES(1,?,?)", row)
        db.commit()
        return {"processed": 0, "initialized": True}
    sequence, anchor = cursor
    if anchor is not None and queue.execute("SELECT id FROM receipts WHERE seq=?", (sequence,)).fetchone() != (anchor,):
        raise RuntimeError("receipt_cursor_diverged")
    rows = queue.execute("SELECT seq,id FROM receipts WHERE seq>? ORDER BY seq LIMIT 32", (sequence,)).fetchall()
    totals = {"matched": 0, "waiting": 0, "confirmed": 0}
    with db:
        for sequence, record_id in rows:
            if _event(reader, record_id):
                db.execute("INSERT OR IGNORE INTO receipt_pending(record,next_check) VALUES(?,0)",(record_id,))
            db.execute("INSERT OR REPLACE INTO receipt_cursor VALUES(1,?,?)", (sequence, record_id))
    now=int(time.time())
    pending=db.execute("SELECT record,tries FROM receipt_pending WHERE next_check<=? ORDER BY next_check,rowid LIMIT 8",(now,)).fetchall()
    for record_id,tries in pending:
        unavailable=False
        try:
            result=_forward(record_id)
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
            result={"matched":0,"waiting":1,"confirmed":0}
            unavailable=True
        for key in totals:
            totals[key]+=result[key]
        with db:
            if not result["waiting"] and (result["matched"] or tries>=2):
                db.execute("DELETE FROM receipt_pending WHERE record=?",(record_id,))
            else:
                delay=min(3600,60*2**min(tries,6))
                db.execute("UPDATE receipt_pending SET tries=tries+1,next_check=? WHERE record=?",(now+delay,record_id))
        if unavailable:
            break
    return {"processed":len(rows),"pending":db.execute("SELECT count(*) FROM receipt_pending").fetchone()[0],**totals}
