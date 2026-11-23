import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime


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


def live_inbox_status(number, verified, opened):
    with closing(sqlite3.connect('file:/run/jp-fixed-return-index/receive.sqlite3?mode=ro',uri=True)) as q, \
         closing(sqlite3.connect('file:/var/lib/jp-reply-capture/replies.sqlite3?mode=ro',uri=True)) as c:
        return inbox_ready(number,verified,opened,q,c)
