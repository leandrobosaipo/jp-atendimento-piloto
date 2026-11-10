import datetime as dt
import unittest
from source import AGENDA,CLIENTS,HISTORY,SERVICES,CALENDAR,allowed

class Contract(unittest.TestCase):
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
