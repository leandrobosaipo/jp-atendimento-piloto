"""Use the normal operator input and reconcile a native Chatwoot message and attachment."""
import argparse,datetime as dt,hashlib,json,os,pathlib,subprocess,uuid
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parents[1];RECEIPTS=ROOT/"private/integration-install";CLIENT="/usr/local/libexec/atendimento-piloto-integracao/client.py"
def call(body):
    p=subprocess.run(["runuser","-u","delladmin","--",CLIENT],input=json.dumps(body),capture_output=True,text=True,timeout=130);return json.loads(p.stdout)
def main(a):
    os.umask(0o077);ready=json.loads((RECEIPTS/"ready.json").read_text());dir=RECEIPTS/"consumer";dir.mkdir(mode=0o700)
    if a.ci and os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("Expected CI")
    catalog=json.loads(pathlib.Path("/etc/atendimento-piloto-integracao/assets.json").read_text())
    for art in catalog["assets"]:
        b=(pathlib.Path("/etc/atendimento-piloto-integracao/assets")/art["filename"]).read_bytes();assert hashlib.sha256(b).hexdigest()==art["sha256"] and len(b)==art["bytes"]
    day=str(dt.datetime.now(ZoneInfo("America/Cuiaba")).date()+dt.timedelta(days=1))
    tests=[("general","A","Como funciona a manutenção da peça?","auto","","",""),("service","A","Qual o preço da manutenção?","price","maintenance","",""),("club","B","Quero ver a tabela do clube.","club_art","","",""),("availability","B","Quais horários de manutenção estão disponíveis amanhã?","availability","maintenance","fixture" if a.ci else "pedro",day)]
    results={}
    for case,actor,q,op,srv,prof,date in tests:
        rid=str(uuid.uuid5(uuid.NAMESPACE_URL,"isolated-integrated-test-v1-"+case))
        body={"op":"ask","actor":actor,"request":rid,"text":q,"intent":op,"service":srv,"professional":prof,"date":date}
        r=call(body)
        with (dir/(case+".json")).open("x") as f:json.dump(r,f,ensure_ascii=False)
        if r.get("status")!="ok":raise RuntimeError("Reconcile "+case+" state="+str(r.get("state",r.get("code")))+" reason="+str(r.get("reason")))
        if case in ("service","club"):
            if case=="service" and r.get("provider",{}).get("status")!="ok":raise RuntimeError("No confirmed service price")
            att=r["attachment"];assert att and not r["private"] and att["group"]==("servicos" if case=="service" else "clube-jp")
            import base64
            preview=call({"op":"asset","request":rid});assert hashlib.sha256(base64.b64decode(preview["image"])).hexdigest()==att["sha256"]
        if case=="availability":
            assert r["provider"].get("status")=="ok" and r["provider"].get("reservation_performed") is False
        if case=="general":assert r["ai"]["mode"]=="api"
        again=call(body);assert (again.get("outgoing"),again.get("incoming"),again.get("attachment"))==(r["outgoing"],r["incoming"],r["attachment"])
        results[case]={k:r[k] for k in ("actor","conversation","incoming","outgoing","private","attachment","ai","provider")}
    conflict=call(dict(body,text="Outra mensagem fictícia"));assert conflict.get("code")=="id_conflict"
    if a.ci:
        def count():
            query="SELECT count(*) FROM messages WHERE message_type=1"
            return int(subprocess.check_output(["docker","compose","exec","-T","postgres","psql","-U","postgres","-d","chat","-Atc",query],cwd=ROOT,text=True).strip())
        baseline=count();uncertain={"op":"ask","actor":"B","request":str(uuid.uuid4()),"text":"CI ambiguous delivery fixture"}
        one=call(uncertain);two=call(uncertain)
        assert one["status"]==two["status"]=="held" and one["state"]==two["state"]=="sending" and count()==baseline+1
    with (dir/"results.json").open("x") as f:json.dump(dict(wf=ready["workflow"],cases=results,duplicateSuppressed=True,conflictHeld=True),f,ensure_ascii=False)
    print("Four genuine pilot consumer paths passed, including native attachment hashes and replay.")
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--ci",action="store_true");main(p.parse_args())
