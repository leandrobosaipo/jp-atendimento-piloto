import hashlib
import unittest
from return_list import bind


class FixedList(unittest.TestCase):
    def test_exclusions(self):
        rows=[]
        directory=[]
        checks=[]
        held=['h1','h2','h3','h4']
        for i in range(11):
            text=f'Convite fictício {i}'
            rows.append(dict(client=str(i),approval_key=hashlib.sha256(text.encode()).hexdigest(),
                             text=text,options=[[24,'14:00']]))
            directory.append(dict(Codigo=i,Celular=f'659800000{i:02}'))
            checks.append(dict(client=str(i),status='slots_available',options=[[24,'14:00']]))
        checks[1]['status']='booked'
        checks[2]['options']=[]
        directory.append(dict(Codigo='other',Celular=directory[3]['Celular']))
        checks[5]['status']='opted_out'
        state={'reactivation':{'requests':[]},
               'delay_support_cases':[{'destination_e164':directory[6]['Celular'],'status':'answered'}],
               'reminders':[{'destination_e164':directory[8]['Celular'],'status':'paused_customer_request'}],
               'conversations':[{'conversation_id':directory[7]['Celular'],'human_handoff':True}]}
        result=bind(rows,held,directory,checks,state,[(directory[4]['Celular'],'unknown')])
        self.assertEqual([r['status'] for r in result[:6]],['matched','booked','offered_slot_changed',
                                                          'identity_conflict','previous_uncertain','opted_out'])
        self.assertEqual([r['text'] for r in result],[r['text'] for r in rows])
        self.assertEqual([r['status'] for r in result[6:8]],['human_handling','human_handling'])
        self.assertEqual(result[8]['status'],'customer_request_pending')
        with self.assertRaises(ValueError):
            bind(rows,['0',*held[1:]],directory,checks,state,[])

if __name__=='__main__':
    unittest.main()
