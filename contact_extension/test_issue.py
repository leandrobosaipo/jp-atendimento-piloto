import datetime as dt
import json, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
import issue
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core_ops'))
import effect_source as host

class Permit(unittest.TestCase):
    def test_wire(self):
        body={'instance':'fixture','phone':'5511999990000','name':'Olá','expectedPreviousName':None,
              'sourceProof':{'source':'whatsapp_contact_screen_human','phone':'5511999990000','name':None,
                             'proofId':'fictional','observedAt':dt.datetime.now(dt.timezone.utc).isoformat()}}
        key='a'*64;grant={'task':key,'body':body,'role':'registration','operation':'contact','cacheExists':False,
            'operator':'fixture','approved':True,'until':int(time.time())+90}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'issued';m=issue.issue(grant,out);(out/'dell').rename(root/'contact-permits')
            with patch.object(host,'CF',root),patch.object(host,'secure',side_effect=lambda p:json.loads(p.read_text())):
                h=host.contact_headers(key,body)
                self.assertEqual(issue.sha(h['X-Contact-Permit'].encode()),m['secretSha']);self.assertEqual(h['X-Contact-Task'],key)
                self.assertNotIn('secret',m)
                with self.assertRaises(ValueError):host.contact_headers(key,dict(body,name='other'))
                os.chmod(root/'contact-permits'/(key+'.json'),0o644)
                with self.assertRaises(ValueError):host.contact_headers(key,body)
if __name__=='__main__':unittest.main()
