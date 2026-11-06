"""CI's network-free model double. The production gateway has no provider override."""
import json,os,sys
assert os.getenv("PILOT_ACTIONS_FIXTURE")=="yes"
sys.path.insert(0,"/usr/local/libexec/atendimento-piloto-ai")
import gateway
Base=gateway.Gateway
def fake(body,key):
    assert body["store"] is False and body["model"]=="gpt-6-luna"
    inp=json.loads(body["input"]);assert "verified_pilot_facts" in inp
    return {"status":"completed","usage":{"input_tokens":120,"output_tokens":12},"output":[{"type":"message","content":[{"type":"output_text","text":json.dumps({"answer":"Fixture response","decision":"answer","source_ids":[v["id"] for v in inp["context"]][:1]})}]}]}
class TestGateway(Base):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs,send=fake)
gateway.Gateway=TestGateway;gateway.serve()
