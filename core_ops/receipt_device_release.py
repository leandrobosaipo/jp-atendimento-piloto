"""Update the running reader without changing its recipient or message checks."""
from hashlib import sha256

BASE = 'bcf27fdc87382b5aae8bc53947692802623ac425e4905e5ff21e79a5891cb3da'
ANCHOR = '    for address in addresses:\n'


def transform(source):
    if sha256(source).hexdigest() != BASE or source.count(ANCHOR.encode()) != 1:
        raise ValueError('receipt_reader_changed')
    return source.replace(ANCHOR.encode(), (ANCHOR +
        '        device = re.fullmatch(r"([0-9]{6,20}):[0-9]{1,3}@lid", address)\n'
        '        if device:\n'
        '            address = device.group(1) + "@lid"\n').encode())


def configure_monitor(nodes):
    health, = [n for n in nodes if n.get('parameters', {}).get('url') == 'http://jp-core-operacional:8791/healthz']
    if health['type'] != 'n8n-nodes-base.httpRequest' or health['parameters'].get('method','GET') != 'GET':
        raise ValueError('unexpected_monitor_operation')
    health.update(retryOnFail=True, maxTries=4, waitBetweenTries=5000)
