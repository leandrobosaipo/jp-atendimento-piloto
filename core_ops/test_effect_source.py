import datetime as dt,json,os,tempfile,time,unittest,uuid
from pathlib import Path
import effect_source as e

class Effects(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.log=self.root/'ledger'
        for d in ('ops-config/grants','ops-config/read-grants','ops-state/admissions','ledger'):(self.root/d).mkdir(parents=True)
        self.epoch=str(uuid.uuid4());self.until=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=4)).isoformat()
        self.s={'role':'reply','stage':'reply-execute','workflow':'W1','execution':'7','node':'N1','task':str(uuid.uuid4()),'source':'d'*64,'client':'19','phone':'+5511912345000','operation':'confirm','reference':'999','epoch':self.epoch}
        self.mode={'mode':'fixture','allowed':{'contact':True,'confirm':True,'create':True,'move':True}}
        self.calls=[]
        # The test runner owns its synthetic root. Production keeps the strict root/read-only checks.
        from unittest.mock import patch
        x=patch.object(e,'secure',side_effect=lambda p:json.loads(p.read_bytes()));x.start();self.addCleanup(x.stop)
    def put(self,f,d):p=self.root/f;p.write_text(json.dumps(d));return p
    def arm(self,s,b):
        self.put('ops-state/ownership.json',{'mode':'rehearsal','roles':[s['role']],'epoch':s['epoch'],'until':self.until})
        route='/api/contact-registration/update' if s['role']=='registration' else '/api/n8n/stages/reply-execute'
        self.put('ops-config/manifest.json',{s['role']:{'workflow':s['workflow'],'nodes':{s['node']:route}}})
        self.put('ops-state/admissions/'+s['workflow']+'-'+s['execution']+'-'+s['node']+'.intent',s)
        normalized={**b}
        if s['operation']=='create':normalized['observacao']='JP nova visita {native-uuid}'
        return self.put('ops-config/grants/'+e.domain(s)+'.json',{**s,'target':'fixture','instance':'example','payload_sha256':e.digest(normalized),'approved_at':dt.datetime.now(dt.timezone.utc).isoformat(),'until':self.until,'authorization':True,'schema':1,'approved_by':'test'})
    def invoke(self,s,b,fn=None):
        def fixture(cfg,op,body):self.calls.append(op);return {'ok':True}
        return e.execute(self.root,self.log,self.mode,dict(action='write',scope=s,body=b),fn or fixture)
    def test_each_operation(self):
        for op,b in [('confirm',{'agendamento':'999','tipo':'5'}),('create',{'cliente':'19','observacao':'JP nova visita '+str(uuid.uuid4()),'sms':'0','whats':'0','lembrete':'0'}),('move',{'codItem':'7'}),('contact',{'instance':'example','phone':self.s['phone'][1:],'name':'fixture','expectedPreviousName':None,'sourceProof':{'source':'whatsapp_contact_screen_human','phone':self.s['phone'][1:],'name':None,'proofId':'I1','observedAt':dt.datetime.now(dt.timezone.utc).isoformat()}})]:
            if op=='create':b.update(item='5',tipoitem='1',profissional='6',dataagendamento='2030-01-10 10:00',duracao='30',cupom='',numitens='1',ageorigem='2',tempolembrete='60')
            if op=='move':b.update(dataAlteraAgendamento='10/01/2030',horaAlteraAgendamento='10:30',tipoAlteraAgendamento='4',profissionalAlteraAgendamento='6')
            s={**self.s,'operation':op}
            if op=='contact':s.update(role='registration',stage='update')
            self.arm(s,b);self.assertEqual(self.invoke(s,b),{'ok':True})
            with self.assertRaises(FileExistsError):self.invoke(s,b)
        self.assertEqual(len(self.calls),4)
    def test_unknown_held_after_restart(self):
        b={'agendamento':'999','tipo':'5'};self.arm(self.s,b)
        def lost(*args):self.calls.append('once');raise TimeoutError
        with self.assertRaises(TimeoutError):self.invoke(self.s,b,lost)
        with self.assertRaises(FileExistsError):self.invoke(self.s,b,lost)
        self.assertEqual(self.calls,['once']);self.assertFalse(any(self.log.glob('*.observed')))
    def test_scope_and_disabled(self):
        b={'agendamento':'999','tipo':'5'};self.arm(self.s,b)
        for changes in [{'source':'other'},{'role':'registration'},{'task':'other'},{'client':'20'},{'epoch':str(uuid.uuid4())},{'execution':'8'},{'operation':'cancel'}]:
            with self.assertRaises((ValueError,FileNotFoundError)):self.invoke({**self.s,**changes},b)
        for invalid in [{**b,'tipo':'4'},dict(b,extra='1')]:
            with self.assertRaises(ValueError):self.invoke(self.s,invalid)
        self.mode['mode']='disabled'
        with self.assertRaises(ValueError):self.invoke(self.s,b)
        self.assertFalse(self.calls)
if __name__=='__main__':unittest.main()
