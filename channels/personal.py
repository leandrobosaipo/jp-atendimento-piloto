"""Operator CLI for the single authorized JP personal homologation."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import urllib.request

from bridge import Event, Held, Outbox, digest, need

INSTANCE = 'jp-protese-atendimento'
ORIGIN = 'https://evolution.codigo5.com.br'
REQUEST = 'jp-personal-homologation-20261110-v1'
TEXT = 'Oi! É o Marcelinho. Esta é a mensagem de teste do novo atendimento da JP no seu número pessoal.'
ACCESS = Path('/etc/jp-evolution-readonly/access.json')
PIN = Path('/etc/jp-homologacao-pessoal/destination.json')
STATE = Path('/var/lib/jp-homologacao-pessoal/outbox.sqlite3')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def private_json(path):
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and
         info.st_uid == 0 and info.st_nlink == 1, 'private_config_required')
    return json.loads(path.read_text())


class Native:
    def __init__(self, config):
        need(config['origin'] == ORIGIN and config['instance'] == INSTANCE, 'wrong_instance')
        self.headers = {'apikey': config['token'], 'CF-Access-Client-Id': config['client_id'],
                        'CF-Access-Client-Secret': config['client_secret'], 'Content-Type': 'application/json'}
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    def __call__(self, path, body):
        need(path in ('message/sendText', 'chat/findMessages', 'chat/whatsappNumbers'), 'unsupported_operation')
        request = urllib.request.Request(f'{ORIGIN}/{path}/{INSTANCE}', headers=self.headers,
                                         data=json.dumps(body).encode(), method='POST')
        with self.opener.open(request, timeout=15) as response:
            raw = response.read(512001)
            need(len(raw) <= 512000, 'response_too_large')
            return json.loads(raw)


class Personal:
    def __init__(self, phone, native):
        need(isinstance(phone, str) and re.fullmatch(r'\+55[0-9]{10,11}', phone), 'personal_pin_required')
        self.number = phone[1:]
        self.jid = self.number + '@s.whatsapp.net'
        self.native = native

    def verify_recipient(self):
        rows = self.native('chat/whatsappNumbers', {'numbers': [self.number]})
        need(isinstance(rows, list) and len(rows) == 1 and rows[0].get('exists') is True and
             str(rows[0].get('number')) == self.number, 'unverified_personal_number')
        jid = rows[0].get('jid')
        allowed = {self.jid}
        if len(self.number) == 13 and self.number[4] == '9':
            allowed.add(self.number[:4] + self.number[5:] + '@s.whatsapp.net')
        need(jid in allowed, 'unverified_personal_jid')
        self.jid = jid

    def event(self):
        return Event('whatsapp', INSTANCE, self.jid, REQUEST, 'Homologação pessoal JP')

    def scope(self, event, payload):
        need(event == vars(self.event()) and payload == {'kind': 'text', 'text': TEXT}, 'outside_personal_test')

    def send(self, event, payload):
        self.scope(event, payload)
        result = self.native('message/sendText', {'number': self.jid.split('@')[0], 'text': TEXT, 'linkPreview': False})
        key = result.get('key', {})
        need(key.get('fromMe') is True and key.get('remoteJid') == self.jid and
             re.fullmatch(r'[A-Za-z0-9_-]{6,160}', key.get('id', '')), 'unverified_acceptance')
        return {'id': key['id'], 'destination': self.jid, 'account': INSTANCE}

    def proof(self, event, native_id, payload):
        self.scope(event, payload)
        result = self.native('chat/findMessages', {'where': {'key': {'id': native_id}}, 'limit': 5})
        rows = result.get('messages', {}).get('records', [])
        matches = [row for row in rows if row.get('key', {}).get('id') == native_id]
        need(len(matches) == 1, 'native_record_not_unique')
        row = matches[0]
        key = row['key']
        message = row.get('message') or {}
        need(key.get('fromMe') is True and key.get('remoteJid') == self.jid and
             (message.get('conversation') or message.get('extendedTextMessage', {}).get('text')) == TEXT,
             'native_record_mismatch')
        statuses = {str(row.get('status')).upper()}
        for update in row.get('MessageUpdate') or []:
            if (update.get('keyId') == native_id and update.get('remoteJid') == self.jid
                    and update.get('fromMe') is True):
                statuses.add(str(update.get('status')).upper())
        read = bool(statuses & {'READ', 'READ_ACK', '4'})
        need(read or statuses & {'DELIVERY_ACK', 'DELIVERED', '3'}, 'delivery_pending')
        return {'account': INSTANCE, 'id': native_id, 'destination': self.jid,
                'payload_sha256': digest(payload), 'status': 'read' if read else 'delivered'}


def run(action, transport, state):
    transport.verify_recipient()
    if action == 'check':
        return {'recipient_verified': True}
    box = Outbox(state)
    event = transport.event()
    key = box.receive(event)
    box.prepare(key, [TEXT])
    if action == 'send':
        box.advance(key, transport)
    part = box.parts(key)[0]
    if action == 'receipt' and part['state'] == 'accepted':
        box.confirm(key, 1, transport.proof)
        part = box.parts(key)[0]
    return {'state': part['state'], 'id': json.loads(part['native_id'])[2] if part['native_id'] else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'send', 'receipt'])
    args = parser.parse_args()
    try:
        os.umask(0o077)
        pin = private_json(PIN)
        need(pin['instance'] == INSTANCE, 'wrong_personal_pin')
        print(json.dumps(run(args.action, Personal(pin['destination'], Native(private_json(ACCESS))), STATE)))
    except Exception as exc:
        print(json.dumps({'held': str(exc) if isinstance(exc, Held) else type(exc).__name__}))
        raise SystemExit(1)
