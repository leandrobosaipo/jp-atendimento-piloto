import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"channels"))
from service_request import decide
from policy import classify

class Decisions(unittest.TestCase):
    def test_stop_contact_precedes_availability_and_knowledge(self):
        def unexpected(*args):
            self.fail("stop-contact request reached an external reader")
        for text in ("Pare de me enviar mensagens","Não quero mais mensagens, quanto custa?", "STOP", "Quero sair. Tem horário amanhã?"):
            with self.subTest(text=text):
                self.assertEqual(classify(text,dict(intent="auto",service="",professional="",date=""),{})[0],"sensitive")
                result=decide(text,"request","case",{},unexpected,unexpected)
                self.assertEqual(result,{"action":"human","reason":"pedido para interromper mensagens"})
    def test_reads_only_for_a_specific_public_slot_query(self):
        provider={"services":{"maintenance":{}},"professionals":{"pedro":{"name":"Pedro"},"jp":{"name":"JP"}}}
        calls=[]
        def query(op,args,cfg):
            calls.append(op)
            return dict(args,status="ok",source="appbarber",reservation_performed=False,observed_at="2026-11-10T20:00:00+00:00",
                        slots=["08:00","11:00","12:30","13:00","18:30"])
        decide_one=lambda q:decide(q,"test","case",provider,query,lambda _:self.fail("unexpected model"))
        result=decide_one("Tem horário de manutenção com Pedro em 2026-11-11?")
        self.assertEqual(result["slots"],["11:00","13:00"])
        self.assertEqual(decide_one("Qual horário amanhã?")["evidence"],"service_required")
        for q in ("Quero remarcar meu horário","Tenho uma ferida","Quero marcar manutenção com Pedro em 2026-11-11","Tem horário com JP?"):
            self.assertEqual(decide_one(q)["action"],"human")
        self.assertEqual(calls,["availability"])
