import datetime as dt
import unittest
from provider import query

NOW = dt.datetime(2026,11,6,16,30,tzinfo=dt.timezone.utc)
CONFIG = {'services':{'maintenance':{'id':'100'}},'professionals':{'one':{'id':'200','human_only':False},'human':{'id':'300','human_only':True}}}

class Tests(unittest.TestCase):
    def test_fresh_price_and_reject_conflicting_rows(self):
        answers=['150,00','160,00']
        def read(kind, params):
            self.assertEqual(kind,'prices');self.assertEqual(params,{})
            return {'data':[{'Ser_Codigo':'100','Ser_Descricao':'Synthetic service','Ser_Valor':answers.pop(0),'Moeda':'R$ '}]}
        for expected in (15000,16000):
            self.assertEqual(query('prices',{'service_key':'maintenance'},CONFIG,read=read,now=NOW)['price_cents'],expected)
        duplicate={'data':[{'Ser_Codigo':'100'},{'Ser_Codigo':'100'}]}
        result=query('prices',{'service_key':'maintenance'},CONFIG,read=lambda *_:duplicate,now=NOW)
        self.assertEqual(result['status'],'unavailable');self.assertNotIn('price_cents',result)

    def test_slots_are_official_bounded_and_strip_private_fields(self):
        def read(kind, params):
            if kind=='catalog':
                return {'servicos':[{'Ser_Codigo':'100','TipoItem':'1','Ser_Intervalo_Padrao':'45'}]}
            self.assertEqual(params,{'servico':'100','profissional':'200','dia':'7','mes':'11','ano':'2026'})
            return {'data':[{'PesCodigoPrincipal':'200','private_name':'SECRET_SENTINEL','horarios':[{'DataIni':f'{h:02}:00','private_phone':'SECRET_SENTINEL'} for h in range(8,20)]}]}
        result=query('availability',{'service_key':'maintenance','date':'2026-11-07','professional_key':'one'},CONFIG,read=read,now=NOW)
        self.assertEqual(result['status'],'ok');self.assertEqual(len(result['slots']),8)
        self.assertFalse(result['reservation_performed']);self.assertNotIn('SECRET_SENTINEL',str(result))

    def test_writes_arbitrary_identity_and_out_of_window_are_not_queried(self):
        def fail(*_):self.fail('Unexpected provider request')
        cases=[('cancel',{}),('prices',{'service_key':'maintenance','client_id':'1'}),('prices',{'service_key':{}}),('availability',{'service_key':'maintenance','date':'2026-12-31','professional_key':'one'}),('availability',{'service_key':'maintenance','date':'2026-11-07','professional_key':'human'})]
        for op,params in cases:
            self.assertNotEqual(query(op,params,CONFIG,read=fail,now=NOW)['status'],'ok')

    def test_duration_change_and_malformed_slot_fail_closed(self):
        def read(kind, params):
            if kind=='catalog':return {'servicos':[{'Ser_Codigo':'100','TipoItem':'1','Ser_Intervalo_Padrao':'45'}]}
            return {'data':[{'PesCodigoPrincipal':'200','tempo':'30','horarios':[{'DataIni':'08:00'}]}]}
        result=query('availability',{'service_key':'maintenance','date':'2026-11-07','professional_key':'one'},CONFIG,read=read,now=NOW)
        self.assertEqual(result['status'],'unavailable');self.assertNotIn('slots',result)

    def test_unavailable_does_not_use_a_historic_price(self):
        def read(*_):raise OSError('PRIVATE_SESSION_SENTINEL')
        result=query('prices',{'service_key':'maintenance'},CONFIG,read=read,now=NOW)
        self.assertEqual(result['status'],'unavailable');self.assertNotIn('price_cents',result)
        self.assertNotIn('PRIVATE_SESSION_SENTINEL',str(result))

if __name__=='__main__':unittest.main()
