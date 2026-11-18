"""Use verified dispatch identities for replies; preserve the human-intervention index."""
import datetime as dt
import re
import time

import reply_capture as cap


def verified_pairs(ledger, authority, now):
    result = set()
    for attempt, native, jid in ledger.execute(
        "SELECT attempt,native,destination FROM dispatch_attempts WHERE state IN ('delivered','read') AND attempted>=?",
        (now - 9 * 86400,),
    ):
        instance, m = authority(dict(attempt=attempt,native=native,destination=jid))
        if not m:
            continue
        pn = cap.digest(jid)
        lid = m.get("lid", "")
        if (m.get("attempt") == attempt and m.get("native") == native and m.get("instance") == instance
                and m.get("pn") == m.get("reverse_pn") == pn and re.fullmatch("[0-9a-f]{64}", lid)
                and m.get("forward_lid") == lid):
            result.add((pn, lid))
    return result


def team_aliases(queue, db, read):
    db.execute("CREATE TABLE IF NOT EXISTS reply_alias_cursor(slot INTEGER PRIMARY KEY, seq INTEGER, record TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS reply_team_alias(alias TEXT, mid TEXT, stamp REAL, PRIMARY KEY(alias,mid))")
    seq, anchor = db.execute("SELECT seq,record FROM reply_alias_cursor WHERE slot=1").fetchone() or (0, None)
    if anchor and queue.execute("SELECT id FROM receipts WHERE seq=?", (seq,)).fetchone() != (anchor,):
        raise ValueError("reply_alias_source_changed")
    rows = queue.execute("SELECT seq,id FROM receipts WHERE seq>? ORDER BY seq LIMIT 64", (seq,)).fetchall()
    for seq, anchor in rows:
        meta, raw = read(anchor)
        for env in raw if isinstance(raw, list) else [raw]:
            if env.get("instance") != cap.INSTANCE or str(env.get("event")).lower().replace("_", ".") != "messages.upsert":
                continue
            data = env.get("data")
            for row in data if isinstance(data, list) else [data]:
                if not isinstance(row, dict):
                    continue
                key = row.get("key") or {}
                if key.get("fromMe") is not True or not key.get("id") or key.get("participant"):
                    continue
                stamp = dt.datetime.fromisoformat(meta["receivedAt"].replace("Z", "+00:00")).timestamp()
                for addr in (key.get("remoteJid"), key.get("remoteJidAlt"), row.get("remoteJidAlt")):
                    if isinstance(addr, str) and cap.DIRECT.fullmatch(addr):
                        db.execute("INSERT OR IGNORE INTO reply_team_alias VALUES(?,?,?)", (cap.digest(addr), key["id"], stamp))
    if rows:
        db.execute("INSERT OR REPLACE INTO reply_alias_cursor VALUES(1,?,?)", (seq, anchor))
    return not queue.execute("SELECT 1 FROM receipts WHERE seq>? LIMIT 1", (seq,)).fetchone()


def extend(queue, db, read, cfg, ledger):
    from dispatch_service import CONFIG
    from receipt_catalog import authority
    db.execute("CREATE TABLE IF NOT EXISTS reply_alias_start(slot INTEGER PRIMARY KEY, seq INTEGER NOT NULL)")
    high = queue.execute("SELECT coalesce(max(seq),0) FROM receipts").fetchone()[0]
    db.execute("INSERT OR IGNORE INTO reply_alias_start VALUES(1,?)", (high,))
    cfg["receiptPairStart"] = db.execute("SELECT seq FROM reply_alias_start WHERE slot=1").fetchone()[0]
    cfg["originalPairs"] = list(cfg["pairs"])
    if not team_aliases(queue, db, read):
        return
    seen = {(p["pn"], p["lid"]) for p in cfg["pairs"]}
    for pn, lid in sorted(verified_pairs(ledger, lambda row: authority(CONFIG, row, 0), time.time()) - seen):
        db.execute("INSERT OR IGNORE INTO team_events SELECT ?,mid,stamp FROM reply_team_alias WHERE alias IN (?,?)", (pn, pn, lid))
        cfg["pairs"].append({"pn": pn, "lid": lid})
