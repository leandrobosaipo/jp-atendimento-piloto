"""Bind a real ledger row to the read-only catalog of verified native WhatsApp pairs."""
import hashlib
import re


def authority(config,row,uid):
    from dispatch_service import read_private
    from dispatch_contract import INSTANCE
    from bridge import need
    attempt=row if isinstance(row,str) else row["attempt"]
    digest=lambda s:hashlib.sha256(s.encode()).hexdigest()
    scope=read_private(config/"receipt-scope.json",uid)
    instance=scope["instance_hash"]
    need(scope["instance"]==INSTANCE and re.fullmatch("[0-9a-f]{64}",instance),"receipt_scope")
    try:
        return instance,read_private(config/"receipt-mappings"/(digest(attempt)+".json"),uid)
    except FileNotFoundError:
        if isinstance(row,str):
            return instance,None
    try:
        catalog=read_private(config/"future-receipt-pairs.json",uid)
    except FileNotFoundError:
        return instance,None
    pn=digest(row["destination"])
    if catalog.get("instanceHash")!=instance:
        return instance,None
    pairs=catalog["pairs"]
    matches=[v for v in pairs if v.get("pn")==pn]
    if len(matches)!=1:
        return instance,None
    lid=matches[0].get("lid","")
    if not re.fullmatch("[a-f0-9]{64}",lid) or sum(v.get("lid")==lid for v in pairs)!=1:
        return instance,None
    return instance,dict(instance=instance,attempt=attempt,native=row["native"],pn=pn,reverse_pn=pn,lid=lid,forward_lid=lid)
