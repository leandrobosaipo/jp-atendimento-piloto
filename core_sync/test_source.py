import datetime as dt
import unittest
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from renewal_lease import reserve
from rearm_worker import replace_config
from source import AGENDA,CLIENTS,HISTORY,SERVICES,CALENDAR,allowed

class Contract(unittest.TestCase):
    def test_rearm_preserves_worker_group_read(self):
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'config.json';f.write_text('{}');f.chmod(0o440)
            info=SimpleNamespace(st_mode=0o100440,st_uid=0,st_gid=781)
            with patch.object(Path,'lstat',return_value=info),patch('os.fchown') as owner:
                replace_config(f,{'generation':3})
                self.assertEqual(owner.call_args.args[1:],(0,781))
            self.assertEqual(f.stat().st_mode&0o777,0o440)
            self.assertEqual(json.loads(f.read_bytes()),{'generation':3})
            f.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'unexpected_private_config_acl'):replace_config(f,{})
    def test_one_native_recovery_per_generation(self):
        now=dt.datetime.now(dt.timezone.utc);old=now-dt.timedelta(hours=1)
        jid='3fb76cf0-2830-4a1e-9fe5-794d9681f26a'
        with tempfile.TemporaryDirectory() as d:
            args=[d,{'job':jid},{'job_id':jid,'status':'requested'},2,old,now,[True,True],False]
            for position,value in [(1,{'job':'other'}),(2,{'job_id':jid,'status':'completed'}),(4,now),(6,[True,False]),(7,True)]:
                blocked=args.copy();blocked[position]=value
                with self.assertRaises(ValueError):reserve(*blocked)
            self.assertEqual(reserve(*args)['job'],jid)
            with self.assertRaises(FileExistsError):reserve(*args)
    def test_read_capabilities(self):
        d=dt.date(2026,11,10)
        p=lambda path,params:allowed(path,params,d,{'7'})
        self.assertTrue(p(CLIENTS,{}));self.assertFalse(p(CLIENTS,{'filter':'x'}))
        period={'profissional':'','servico':'','cliente':'','tipo':'1','dataini':'09/11/2026','datafim':'22/11/2026'}
        self.assertTrue(p(AGENDA,period));self.assertFalse(p(AGENDA,dict(period,datafim='01/01/2029')))
        block={'edtDataIni':'11/11/2025','edtDataFim':'24/11/2025','tipoStatus':'2','AgeCliente':'','AgeProfissional':'','AgeServico':''}
        self.assertTrue(p(HISTORY,block));self.assertFalse(p(HISTORY,dict(block,AgeCliente='1')))
        self.assertTrue(p(SERVICES,{'profissional':'7','tipo':'9'}));self.assertFalse(p('/pages/actions/gravaAgendamento.php',{}))
        self.assertTrue(p(CALENDAR,{'profissional[]':'7','tipo':'1','dia':'09/11/2026'}))
        self.assertFalse(p(CALENDAR,{'profissional[]':'8','tipo':'1','dia':'09/11/2026'}))

if __name__=='__main__':unittest.main()
