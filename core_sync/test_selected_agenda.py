import unittest,datetime as dt
from source import AGENDA,HISTORY,check_pending,selected_read

class Selected(unittest.TestCase):
    def test_exact_identity_date_and_current_status(self):
        day=dt.date(2026,11,11)
        m=dict(id='11',date='2026-11-12',time='09:00',pro='1',service='7',client='3')
        plan=dict(day=day.isoformat(),members=[m])
        query=dict(profissional='1',servico='7',cliente='3',dataini='12/11/2026',datafim='12/11/2026',tipo='1')
        self.assertEqual(selected_read(AGENDA,query,day,plan),[m])
        for k in query:
            self.assertIsNone(selected_read(AGENDA,{**query,k:'other'},day,plan))
        self.assertIsNone(selected_read(AGENDA,query,day+dt.timedelta(days=1),plan))
        self.assertIsNone(selected_read(HISTORY,query,day,plan))
        row=dict(id=11,start='2026-11-12T09:00:00',resources=1,sercodigo=7,codCliente=3,codStatus=1,Age_Confirmado=0)
        check_pending([row],[m])
        for mutations in ([row,row],[],[{**row,'Age_Confirmado':1}],[{**row,'codCliente':'9'}],[{**row,'start':'2026-11-12T10:00:00'}]):
            with self.assertRaises(ValueError):check_pending(mutations,[m])
