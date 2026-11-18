"""Publish a verified physical receipt to the existing private Central process."""
import json
import os
from pathlib import Path
import tempfile

ROLE = json.dumps(["7Ur9XdO2FiFEnMhY","42e396aa-41b9-4ca3-be12-f73f66177528","sendText"],separators=(",",":"))


def project(ledger, db, read, call, conf, root):
    from dispatch_service import CONFIG, receipt_authority
    from dispatch_receipt import match_event, delivery_state
    scope = json.loads((conf / "central-receipt-scope.json").read_bytes())
    db.execute("CREATE TABLE IF NOT EXISTS central_projected(record TEXT PRIMARY KEY, attempt TEXT NOT NULL)")
    result = {"applied":0, "waiting":0}
    for attempt, native, destination, source, stamp, kind, record in ledger.execute("""
        SELECT a.attempt,a.native,a.destination,a.source,a.attempted,o.kind,o.record
        FROM dispatch_observations o JOIN dispatch_attempts a ON o.attempt=a.attempt
        WHERE a.role=? AND a.state IN ('delivered','read') AND o.kind IN ('delivered','read')
        AND (a.attempted>=? OR a.native IN (?,?)) ORDER BY a.attempted, o.kind""",
        (ROLE, scope["start"], *scope["originals"])):
        if db.execute("SELECT 1 FROM central_projected WHERE record=? AND attempt=?",(record,attempt)).fetchone():
            continue
        meta, raw = read(record)
        target = dict(attempt=attempt,native=native,destination=destination,attempted=stamp)
        row = match_event((meta,raw),target,"messages.update",*receipt_authority(CONFIG,attempt,0))
        if delivery_state(row)!=kind:
            raise ValueError("physical_receipt_mismatch")
        proof=dict(record=record,attempt=attempt,native=native,source=source,at=meta["receivedAt"],kind=kind)
        path=root/"ops-config/read-grants/central-receipt.json"
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,"w") as f:
                os.fchown(f.fileno(),0,(root/"ops-config/reply-ingress.key").stat().st_gid)
                os.fchmod(f.fileno(),0o440)
                json.dump(proof,f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            Path(tmp).unlink(missing_ok=True)
        if call("delivery-status",{"record":record}).get("applied") is True:
            db.execute("INSERT OR IGNORE INTO central_projected VALUES(?,?)",(record,attempt))
            result["applied"]+=1
        else:
            result["waiting"]+=1
        if sum(result.values())>=8:
            break
    return result
