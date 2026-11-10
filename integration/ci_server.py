"""Only explicitly installed by CI; the deployed service always uses the official provider."""
import os,sys
assert os.getenv("PILOT_ACTIONS_FIXTURE")=="yes"
sys.path.insert(0,"/usr/local/libexec/atendimento-piloto-integracao")
import service
def facts(op,p,c):
    import datetime as dt
    base={"status":"ok","source":"appbarber","observed_at":dt.datetime.now(dt.timezone.utc).isoformat(),"service_key":p["service_key"],"service_id":"1"}
    if op=="prices":return dict(base,name="Manutenção fictícia",price_cents=14000,currency="BRL")
    return dict(base,date=p["date"],professional_key=p["professional_key"],duration_minutes=40,slots=["15:00"],timezone="America/Cuiaba",reservation_performed=False)
service.provider.query=facts
post=service.App.post_part
def lose_test_ack(self,row,part):
    oid=post(self,row,part)
    if row["question"]=="CI ambiguous delivery fixture" and part["pos"]==2:raise service.Stop("test_missing_ack")
    return oid
service.App.post_part=lose_test_ack
service.serve()
