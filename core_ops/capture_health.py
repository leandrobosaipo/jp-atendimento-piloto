"""Transitions driven only by the existing Dell n8n capture tick."""
def observe(db, execution, now, ok):
    db.execute("CREATE TABLE IF NOT EXISTS capture_health(slot INTEGER PRIMARY KEY,last INTEGER,start INTEGER,opened INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS capture_incidents(start INTEGER,kind TEXT,execution TEXT,stamp INTEGER,noted INTEGER DEFAULT 0,PRIMARY KEY(start,kind))")
    row=db.execute("SELECT last,start,opened FROM capture_health WHERE slot=1").fetchone()
    last,start,opened=row or (now,None,0)
    if now-last>=180 and start is None:
        start=last
    if not ok and start is None:
        start=now
    if start is not None and now-start>=180 and not opened:
        db.execute("INSERT OR IGNORE INTO capture_incidents(start,kind,execution,stamp) VALUES(?,'interrompido',?,?)",(start,execution,now))
        opened=1
    if ok and opened:
        db.execute("INSERT OR IGNORE INTO capture_incidents(start,kind,execution,stamp) VALUES(?,'retomado',?,?)",(start,execution,now))
    db.execute("INSERT OR REPLACE INTO capture_health VALUES(1,?,?,?)",(now,None if ok else start,0 if ok else opened))
