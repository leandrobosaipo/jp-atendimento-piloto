"""Render a hash-bound, non-applying release manifest for the Dell core."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from package import wrap_reply

ROOT = Path(__file__).resolve().parent
FILES = ('autonomous_same_day_reschedule.mjs', 'same_day_reschedule_integration.mjs',
         'effect_client.mjs', 'effect_guard.mjs', 'task_reads.mjs', 'package.py')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def render(adapter):
    raw = adapter.read_bytes()
    patched = wrap_reply(raw, hashlib.sha256(raw).hexdigest())
    return {
        'schema': 1,
        'kind': 'jp_same_day_reschedule_prepare_only',
        'activation': 'none',
        'transport': 'unchanged',
        'requires': {
            'reply_role': 'already owned and admitted by existing workflow',
            'reader': 'existing task-scoped official reader',
            'effect': 'existing move grant plus native readback',
            'capture_wake': 'existing reply workflow must consume the captured inbound event',
        },
        'adapter': {'sha256': hashlib.sha256(raw).hexdigest(), 'patched_sha256': hashlib.sha256(patched).hexdigest()},
        'ops_app': {name: sha(ROOT / name) for name in FILES},
    }

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--output', type=Path)
    p.add_argument('--write', action='store_true')
    a = p.parse_args()
    manifest = render(a.adapter)
    if a.write:
        if not a.output or a.output.exists():
            raise SystemExit('exclusive_output_required')
        os.umask(0o077)
        with a.output.open('x') as out:
            json.dump(manifest, out, indent=2, sort_keys=True)
            out.flush(); os.fsync(out.fileno())
        a.output.chmod(0o600)
    print(json.dumps({'adapter_sha256': manifest['adapter']['sha256'],
                      'patched_sha256': manifest['adapter']['patched_sha256'],
                      'activation': manifest['activation'], 'files': len(manifest['ops_app'])}))
