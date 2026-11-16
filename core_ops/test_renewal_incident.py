import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from capture_health import observe
from reply_triage_http import observe_renewal

def test_silent_history_and_single_incident():
    db=sqlite3.connect(':memory:')
    observe(db,'1',1000,True)
    with TemporaryDirectory() as dirname:
        root=Path(dirname)
        session=root/'config/session.json';session.parent.mkdir();session.touch()
        os.utime(session,(900,900))
        folder=root/'state/renewal-intents';folder.mkdir(parents=True)
        for name,ts in [('past',800),('current',1000)]:
            f=folder/(name+'.json');f.write_text(json.dumps(dict(job=name,execution='12')));os.utime(f,(ts,ts))
        for now in [1010,1180,1230]:
            observe_renewal(db,root,now)
        assert db.execute('SELECT kind FROM capture_incidents').fetchall()==[('agenda_acesso',)]
        os.utime(session,(1250,1250))
        for now in [1260,1300]:observe_renewal(db,root,now)
        assert db.execute('SELECT kind FROM capture_incidents ORDER BY rowid').fetchall()==[('agenda_acesso',),('agenda_reler',)]
