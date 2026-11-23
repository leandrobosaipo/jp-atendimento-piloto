import hashlib
import json
import sqlite3
import shutil
import tempfile
from contextlib import closing,contextmanager
from datetime import datetime
from pathlib import Path


def inbox_ready(number, verified, opened, queue, captured):
    """Hold a new invitation when the monitor has not processed recent activity."""
    hashes={hashlib.sha256((n+'@s.whatsapp.net').encode()).hexdigest() for n in
            (number, number[:4]+number[5:] if len(number)==13 else number[:4]+'9'+number[4:])}
    matches=[p for p in verified if p['pn'] in hashes]
    if len(matches)!=1:return 'identity_unverified'
    aliases=hashes|{matches[0]['lid']}
    high=queue.execute('select coalesce(max(seq),0) from receipts').fetchone()
    for table in ('cursor','team_cursor','reply_alias_cursor'):
        if captured.execute('select seq from '+table+' where slot=1').fetchone()!=high:
            return 'inbox_catching_up'
    for encoded,received in captured.execute('select address_hashes,received_at from captured'):
        if datetime.fromisoformat(received.replace('Z','+00:00')).timestamp()>=opened and aliases.intersection(json.loads(encoded)):
            return 'conversation_changed'
    if any(captured.execute('select 1 from reply_team_alias where alias=? and stamp>=? limit 1',
                            (ident,opened)).fetchone() for ident in aliases):
        return 'team_message_after_approval'
    return 'matched' if queue.execute('select coalesce(max(seq),0) from receipts').fetchone()==high else 'inbox_catching_up'


@contextmanager
def captured_snapshot(source):
    # SQLite can need a shared-memory file when the last writer has just
    # checkpointed and closed a WAL database. Create it only in a private copy.
    files=[source,Path(str(source)+'-wal')]
    def signature():
        result=[]
        for f in files:
            try:s=f.stat()
            except FileNotFoundError:result.append(None);continue
            result.append((s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
        return result
    with tempfile.TemporaryDirectory(prefix='jp-inbox-') as tmp:
        dest=Path(tmp)/source.name
        before=signature()
        for f,info in zip(files,before):
            if info is not None:shutil.copyfile(f,Path(tmp)/f.name)
        if before!=signature():raise ValueError('capture_changed_during_read')
        with closing(sqlite3.connect(dest)) as c:
            c.execute('pragma query_only=ON')
            yield c

def live_inbox_status(number, verified, opened):
    with closing(sqlite3.connect('file:/run/jp-fixed-return-index/receive.sqlite3?mode=ro',uri=True)) as q, \
         captured_snapshot(Path('/var/lib/jp-reply-capture/replies.sqlite3')) as c:
        return inbox_ready(number,verified,opened,q,c)
