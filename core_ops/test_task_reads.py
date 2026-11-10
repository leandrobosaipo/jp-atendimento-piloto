import datetime as dt,hashlib,hmac,json,unittest,uuid
from task_source import Held,Reader,list_data,rows

class Reads(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2030,3,7,15,tzinfo=dt.timezone.utc)
        self.scope=dict(workflow='test',node='next',execution='77',epoch=str(uuid.uuid4()),task=str(uuid.uuid4()),source='0'*64,phone='5511988880000',client='100',stage='reply-next',role='reply')
        self.rows=[{'Codigo':str(i)} for i in range(100)]+[{'Codigo':'100','Celular':'11988880000','DDI':'55','private':'omit'}]
        self.calls=[];self.history=[{'CodCliente':'100','Status':'realizado','private':'omit'}]
        data={'clients':lambda:list_data(self.rows),'history':lambda:list_data(self.history),'visits':lambda:list_data([]),
              'professionals':lambda:list_data([{'Pes_Codigo':'12','Pes_Nome':'Test','private':'omit'},{'Pes_Codigo':'15'}],'profissionais'),
              'services':lambda:list_data([{'Ser_Codigo':'33','TipoItem':'1','Ser_Intervalo_Padrao':'30'}],'servicos'),
              'slots':lambda:list_data([{'PesCodigoPrincipal':'12','tempo':30,'horarios':[{'DataIni':'10:00','private':'omit'}]}])}
        def official(cfg,op,params):
            self.calls.append(op);return data[op]()
        self.reader=Reader({'workflow':'test','nodes':{'next':'reply-next'},'source_generation':120,'professionals':{'auto':{'id':'12'},'human':{'id':'13','human_only':True}}},b'fixture',official)
    def message(self,op,params=None,**kw):
        packet={'scope':self.scope,'nonce':uuid.uuid4().hex,'at':self.now.timestamp(),'generation':120,'op':op,'params':params or {},**kw}
        raw=json.dumps(packet);return {'body':raw,'mac':hmac.new(b'fixture',raw.encode(),hashlib.sha256).hexdigest()}
    def read(self,op,params=None,**kw):return self.reader.invoke(self.message(op,params,**kw),self.now)
    def test_paths(self):
        self.assertEqual(len(self.read('clients')['data']),1);self.assertNotIn('private',self.read('clients')['data'][0])
        self.assertEqual(self.read('visits',dict(profissional='',servico='',cliente='100',dataini='07/03/2030',datafim='07/03/2031',tipo='1'))['data'],[])
        history=dict(edtDataIni='07/03/2029',edtDataFim='07/03/2030',tipoStatus='2',AgeCliente='100',AgeProfissional='',AgeServico='')
        self.assertNotIn('private',self.read('history',history)['data'][0]);self.history[0]['CodCliente']='222'
        with self.assertRaises(Held):self.read('history',history)
        self.assertEqual(len(self.read('professionals')['profissionais']),1)
        self.read('services',dict(profissional='12',tipo='9'))
        self.assertEqual(self.read('slots',dict(dia='8',mes='3',ano='2030',profissional='12',servico='33'))['data'][0]['horarios'],[{'DataIni':'10:00'}])
        self.assertEqual(self.calls.count('clients'),1)
    def test_guards(self):
        for op,params in [('history',{'AgeCliente':'101'}),('visits',{'cliente':'101'}),('services',{'profissional':'13'}),('save',{})]:
            with self.assertRaises(Held):self.read(op,params)
        for what in [{'generation':119},{'at':1},{'scope':{**self.scope,'role':'clock'}},{'scope':{**self.scope,'node':'other'}}]:
            with self.assertRaises(Held):self.read('clients',**what)
        self.assertEqual(self.calls,[])
        p=self.message('clients');self.reader.invoke(p,self.now)
        with self.assertRaises(Held):self.reader.invoke(p,self.now)
        with self.assertRaises(Held):self.read('slots',{'profissional':'12','dia':'7','mes':'3','ano':'2030','servico':'33'})
        self.read('services',dict(profissional='12',tipo='9'))
        with self.assertRaises(Held):self.read('slots',{'profissional':'12','dia':'25','mes':'3','ano':'2030','servico':'33'})
        self.assertNotIn('slots',self.calls)
    def test_ambiguous_destination(self):
        self.rows[1]['Celular']=self.rows[-1]['Celular']
        with self.assertRaises(Held):self.read('clients')
    def test_empty_native_report(self):
        empty={'data':{'erro':'','resultado':'','DT_RowId':0,'Status':''},'recordsTotal':0,'recordsFiltered':0}
        self.assertEqual(rows(empty,'data',True),[])
        for bad in [{**empty,'recordsFiltered':1},{**empty,'data':{**empty['data'],'Status':'realizado'}}]:
            with self.assertRaises(Held):rows(bad,'data',True)

if __name__=='__main__':unittest.main()
