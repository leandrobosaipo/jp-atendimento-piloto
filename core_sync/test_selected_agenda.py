import unittest,datetime as dt
from source import AGENDA,HISTORY,CALENDAR,check_pending,selected_read,selected_calendar

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

    def test_future_calendar_scoped_to_selected_services(self):
        day=dt.date(2026,11,12)
        one=dict(id='20',date='2026-11-13',time='13:30',pro='1',service='7',client='3')
        two={**one,'id':'21','time':'14:00','service':'8'}
        plan=dict(day=day.isoformat(),members=[one,two,{**one,'id':'25','pro':'2'}])
        params={'profissional[]':'1','tipo':'1','dia':'13/11/2026'}
        selected=selected_read(CALENDAR,params,day,plan)
        self.assertEqual(selected,[one,two])
        for change in [{'dia':'14/11/2026'},{'profissional[]':'4'},{'tipo':'2'},{'extra':'1'}]:
            self.assertIsNone(selected_read(CALENDAR,{**params,**change},day,plan))
        self.assertIsNone(selected_read(CALENDAR,params,day+dt.timedelta(days=1),plan))
        def row(m):
            return dict(id=m['id'],start=m['date']+'T'+m['time']+':00',resources=m['pro'],sercodigo=m['service'],codCliente=m['client'],codStatus=1,Age_Confirmado=0)
        a,b=row(one),row(two)
        self.assertEqual(selected_calendar([a,{**a,'id':'99'},b],selected),[a,b])
        for invalid in [[a],[a,b,b],[a,{**b,'Age_Confirmado':1}]]:
            with self.assertRaises(ValueError):selected_calendar(invalid,selected)
