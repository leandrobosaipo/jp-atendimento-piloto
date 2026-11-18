"""Transform only the audited dispatcher version. The operational release pins its digest."""
def transform(source):
    start=source.index("def receipt_authority(")
    end=source.index("def observe_records(", start)
    if 'future-receipt-pairs.json' not in source[start:end]:
        raise ValueError("unknown_receipt_base")
    source=source[:start]+'''def receipt_authority(config, attempt, uid):
    from receipt_catalog import authority
    return authority(config, attempt, uid)


'''+source[end:]
    old='receipt_authority(config,attempt["attempt"],admin_uid)'
    if source.count(old)!=2:raise ValueError("unrecognized_receipt_callers")
    source=source.replace(old,"receipt_authority(config,attempt,admin_uid)")
    before='            authority = receipt_authority(config,attempt,admin_uid)'
    if source.count(before)!=1:raise ValueError("unrecognized_event_parser")
    return source.replace(before,'''            if kind == "messages.update" and (item.get("status") or (item.get("update") or {}).get("status")) not in ("DELIVERY_ACK", "READ"):
                continue
'''+before)
