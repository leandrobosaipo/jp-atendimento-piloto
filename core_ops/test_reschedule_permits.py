import datetime as dt,json,tempfile,unittest
from pathlib import Path
import reschedule_permits as permits

class PermitTest(unittest.TestCase):
 def test_issues_real_readable_grant_and_brazilian_date(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);conf=root/'ops-config';(conf/'grants').mkdir(parents=True);(conf/'read-grants').mkdir();(root/'ops-state').mkdir()
   (conf/'automatic-reschedules.json').write_text(json.dumps({'enabled':True,'afterSequence':0,'afterUtc':(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat()}))
   (conf/'manifest.json').write_text('{"reply":{"workflow":"w","nodes":{"n":"/api/n8n/stages/reply-execute"}}}')
   (root/'ops-state/ownership.json').write_text(json.dumps({'mode':'owned','roles':['reply'],'epoch':'e','until':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat()}))
   p={'task_id':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee','source':'a'*64,'appointment_id':'1','client':'2','phone':'+5565999999999','capture_sequence':1,'message_id':'m','fingerprint':'b'*64,'source_at':dt.datetime.now(dt.timezone.utc).isoformat(),'person':'c'*64,'before':{'date':'2030-01-02','time':'10:00','professional_code':'p','service_code':'s','duration_minutes':45,'item_code':'item'},'slot':{'date':'2030-01-02','time':'11:00','professional_code':'p','service_code':'s','duration_minutes':45}}
   grant=permits.issue(root,conf,p,latest_is_source=lambda _:True)
   self.assertEqual(grant['payload_sha256'],permits.digest({'dataAlteraAgendamento':'02/01/2030','horaAlteraAgendamento':'11:00','tipoAlteraAgendamento':'4','codItem':'item','profissionalAlteraAgendamento':'p'}))
   path=next((conf/'grants').glob('*.json'));self.assertEqual(path.stat().st_mode&0o777,0o640);self.assertEqual(json.loads(path.read_text())['operation'],'move')
   read=next((conf/'read-grants').glob('*.json'));self.assertEqual(json.loads(read.read_text())['reads'],[{'profissional[]':'p','dia':'02/01/2030','tipo':'1'}])
   self.assertEqual(permits.issue(root,conf,p,latest_is_source=lambda _:True),grant)
if __name__=='__main__':unittest.main()
