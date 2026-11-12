import sqlite3,tempfile,unittest,json
from pathlib import Path
from capture_link import has_newer_identity
class CaptureSchema(unittest.TestCase):
 def test_real_captured_schema_uses_address_hashes_not_pn(self):
  with tempfile.TemporaryDirectory() as d:
   db=sqlite3.connect(Path(d)/'r.sqlite');db.execute('CREATE TABLE captured(instance TEXT,message_id TEXT,seq INTEGER,record_id TEXT,envelope_pos INTEGER,item_pos INTEGER,kind TEXT,disposition TEXT,quoted_id TEXT,address_hashes TEXT,content_sha256 TEXT,received_at TEXT)')
   db.execute('INSERT INTO captured VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('jp','a',4,'r',0,0,'text','review','',json.dumps(['p']), 'x',''))
   rowid=db.execute('SELECT rowid FROM captured').fetchone()[0];row={'seq':4,'rowid':rowid}
   self.assertFalse(has_newer_identity(db,row,'p'));db.execute('INSERT INTO captured VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('jp','b',5,'r',0,0,'text','review','',json.dumps(['p']),'y',''))
   self.assertTrue(has_newer_identity(db,row,'p'))
if __name__=='__main__':unittest.main()
