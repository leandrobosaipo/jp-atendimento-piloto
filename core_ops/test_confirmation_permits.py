import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from confirmation_permits import digest,issue
from reply_capture import intent


class ConfirmationPolicy(unittest.TestCase):
    def test_customer_language(self):
        for text in ['👍🏽','✅','Confirmo sim!','Sim, confirmado.','Ok!']:
            self.assertEqual(intent(text),'possible_confirmation',text)
        for text in ['👍?','Queria mudar para 11h','sim, se puder às 11h','não confirmo','👎','2']:
            self.assertEqual(intent(text),'review',text)

    def test_grant_binds_visit_source_and_hold(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for directory in ['ops-config/grants','ops-state','conf']:
                (root/directory).mkdir(parents=True)
            now=dt.datetime.now(dt.timezone.utc)
            def save(path,obj): (root/path).write_text(json.dumps(obj))
            save('conf/automatic-confirmations.json',dict(enabled=True,afterSequence=9,afterUtc=(now-dt.timedelta(minutes=1)).isoformat()))
            save('ops-state/ownership.json',dict(mode='owned',roles=['reply'],epoch='test',until=(now+dt.timedelta(hours=1)).isoformat()))
            save('ops-config/manifest.json',{'reply':{'workflow':'workflow','nodes':{'node':'/api/n8n/stages/reply-execute'}}})
            (root/'ops-config/reply-ingress.key').touch()
            db=sqlite3.connect(':memory:')
            db.executescript("CREATE TABLE cursor(slot,seq);INSERT INTO cursor VALUES(1,10);CREATE TABLE receipts(seq);INSERT INTO receipts VALUES(10);CREATE TABLE captured(seq,message_id,content_sha256,address_hashes);INSERT INTO captured VALUES(10,'m','hash','[\"person\"]');")
            row=dict(rowid=1,seq=10,message_id='m',content_sha256='hash',address_hashes='["person"]')
            event=dict(at=now.isoformat(),disposition='possible_confirmation',providerTime=True,intervened=False,conflict=False,
                       mid='m',content='hash',delivered=['notice'],humanNotices=[])
            req=dict(notice='notice',phone='+5511999999999',client='33',key=digest(['m','hash']),ids=['34','35'])
            with patch('os.fchown'):
                issue(root,root/'conf',row,{**event,'intervened':True},req,db,db)
                issue(root,root/'conf',{**row,'seq':9},event,req,db,db)
                self.assertEqual(list((root/'ops-config/grants').iterdir()),[])
                issue(root,root/'conf',row,event,req,db,db)
                gs=[json.loads(p.read_bytes()) for p in (root/'ops-config/grants').iterdir()]
                self.assertEqual({g['reference'] for g in gs},{'34','35'})
                self.assertTrue(all(g['operation']=='confirm' and g['source']==req['key'] for g in gs))

if __name__=='__main__':
    unittest.main()
