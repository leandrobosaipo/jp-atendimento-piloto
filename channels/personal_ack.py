"""Root-only import of an authenticated Mac Mini collector readback."""
import hashlib
import json
import os
import re
import sys
from personal import ACCESS, INSTANCE, PIN, STATE, TEXT, Native, Personal, private_json
from bridge import Outbox, Held, digest, need


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def reconcile(box, transport, witness):
    event = transport.event()
    part = box.parts(event.key)
    need(len(part) == 1 and part[0]['state'] in ('accepted', 'delivered'), 'accepted_part_required')
    expected = json.loads(part[0]['native_id'])
    need(expected[:2] == ['whatsapp', INSTANCE], 'wrong_account')
    native_id = expected[2]
    receipt, mapping = witness['receipt'], witness['mapping']
    need(receipt['instance'] == INSTANCE and receipt['event'] == 'messages.update' and
         receipt['keyId'] == native_id and receipt['fromMe'] is True and
         receipt['status'] in ('DELIVERY_ACK', 'READ') and
         re.fullmatch('[a-f0-9]{64}', receipt['record_id']), 'wrong_receipt')
    phone_hash = sha(transport.jid.split('@')[0])
    need(mapping['instance_id_hash'] == receipt['instance_id_hash'] and
         mapping['pn'] == mapping['reverse_pn'] == phone_hash and
         mapping['forward_lid'] == receipt['lid'] == mapping['reverse_lid'],
         'recipient_mapping_mismatch')

    def proof(source, identifier, payload):
        transport.scope(source, payload)
        need(identifier == native_id, 'wrong_ledger_id')
        return {'id': identifier, 'destination': transport.jid, 'account': INSTANCE,
                'payload_sha256': digest(payload), 'status': 'read' if receipt['status'] == 'READ' else 'delivered',
                'source': 'authenticated_mac_collector', 'received_at': receipt['at'],
                'collector_record_id': receipt['record_id']}

    result = box.confirm(event.key, 1, proof)
    return {'id': native_id, 'state': result['status'], 'received_at': receipt['at']}


if __name__ == '__main__':
    try:
        need(os.geteuid() == 0, 'operator_required')
        os.umask(0o077)
        pin = private_json(PIN)
        need(pin['instance'] == INSTANCE, 'wrong_instance')
        transport = Personal(pin['destination'], Native(private_json(ACCESS)))
        transport.verify_recipient()
        print(json.dumps(reconcile(Outbox(STATE), transport, json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({'held': str(exc) if isinstance(exc, Held) else type(exc).__name__}))
        raise SystemExit(1)
