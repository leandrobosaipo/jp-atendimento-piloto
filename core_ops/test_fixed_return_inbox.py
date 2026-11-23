import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from fixed_return_inbox import captured_snapshot,inbox_ready

class Coverage(unittest.TestCase):
    def test_private_snapshot_includes_committed_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.db';writer=sqlite3.connect(source)
            try:
                writer.execute('pragma journal_mode=WAL')
                writer.execute('create table inbox(n)');writer.execute('insert into inbox values(12)');writer.commit()
                with captured_snapshot(source) as copy:
                    self.assertEqual(copy.execute('select n from inbox').fetchone(),(12,))
                    with self.assertRaises(sqlite3.OperationalError):copy.execute('delete from inbox')
            finally:writer.close()
            with captured_snapshot(source) as copy:
                self.assertEqual(copy.execute('select n from inbox').fetchone(),(12,))

    def test_new_team_message_customer_message_and_lag_each_hold(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        phone='5511999990000';pn=hashlib.sha256((phone+'@s.whatsapp.net').encode()).hexdigest()
        pairs=[{'pn':pn,'lid':'fixture_alias'}];stamp=datetime.now(timezone.utc)
        db.executescript('create table receipts(seq);insert into receipts values(3);'
                         'create table captured(address_hashes,received_at);create table reply_team_alias(alias,stamp);')
        for tab in ('cursor','team_cursor','reply_alias_cursor'):
            db.execute('create table '+tab+'(slot,seq)');db.execute('insert into '+tab+' values(1,3)')
        check=lambda:inbox_ready(phone,pairs,stamp.timestamp()-10,db,db)
        self.assertEqual(check(),'matched')
        db.execute('insert into reply_team_alias values(?,?)',('fixture_alias',stamp.timestamp()))
        self.assertEqual(check(),'team_message_after_approval')
        db.execute('insert into captured values(?,?)',(json.dumps([pn]),stamp.isoformat()))
        self.assertEqual(check(),'conversation_changed')
        db.execute('insert into receipts values(4)')
        self.assertEqual(check(),'inbox_catching_up')
        self.assertEqual(inbox_ready(phone,[],0,db,db),'identity_unverified')

if __name__=='__main__':unittest.main()
