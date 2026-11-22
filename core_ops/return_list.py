"""Bind private invitation texts to their approved clients and current checks."""
import hashlib
import re
from collections import defaultdict


def phone(value):
    value = re.sub(r'\D', '', str(value or '').split('@')[0])
    if len(value) in (12, 13) and value.startswith('55'):
        value = value[2:]
    if not re.fullmatch(r'[1-9]\d(?:9\d{8}|\d{8})', value):
        return ''
    return value[:2] + value[-8:]


def bind(approved, held, directory, checks, state, physical):
    ids = [r['client'] for r in approved]
    keys = [r['approval_key'] for r in approved]
    if (len(ids) != 11 or len(set(ids)) != 11 or len(set(keys)) != 11
            or any(not re.fullmatch('[a-f0-9]{64}', k) for k in keys)
            or len(set(held)) != 4 or set(ids).intersection(held)
            or len(checks) != 11 or {r['client'] for r in checks} != set(ids)):
        raise ValueError('approved_scope_changed')
    by_client, owners = defaultdict(set), defaultdict(set)
    for person in directory:
        cid = str(person['Codigo'])
        for field in ('Celular', 'Cli_Celular', 'Telefone'):
            number = phone(person.get(field))
            if number:
                by_client[cid].add(number)
                owners[number].add(cid)
    history = state['reactivation']['requests']
    uncertain = {phone(n) for n, status in physical if status in ('unknown','sending','accepted')}
    sent = set()
    for row in history:
        number = phone(row.get('destination_e164'))
        if number:
            owners[number].add(str(row['client_code']))
        for attempt in row.get('dispatches', []):
            if attempt.get('status') in ('sending','delivery_unknown','provider_accepted') and not attempt.get('delivery_confirmed'):
                uncertain.add(number)
            if attempt.get('attempted_at'):
                sent.add((number, attempt.get('text_sha256') or attempt.get('content_sha256')))
    for field in ('individual_dispatches','manual_dispatches','diagnostic_dispatches','jp_reply_attempts','reminders'):
        for attempt in state.get(field, []):
            if attempt.get('status') == 'delivery_unknown' and not attempt.get('delivery_confirmed'):
                uncertain.add(phone(attempt.get('destination_e164')))
    checks = {r['client']: r for r in checks}
    result = []
    for row in approved:
        cid = row['client']
        digest = hashlib.sha256(row['text'].encode()).hexdigest()
        current = checks[cid]
        numbers = by_client[cid]
        number = next(iter(numbers)) if len(numbers) == 1 else ''
        status = current['status']
        if status == 'slots_available':
            if not number or owners[number] != {cid}:
                status = 'identity_conflict'
            elif number in uncertain:
                status = 'previous_uncertain'
            elif (number, digest) in sent:
                status = 'already_attempted'
            elif not row['options'] or current['options'] != row['options']:
                status = 'offered_slot_changed'
            else:
                status = 'matched'
        result.append(dict(client=cid, approval_key=row['approval_key'], text_sha256=digest,
                           text=row['text'], options=row['options'], status=status))
    return result
