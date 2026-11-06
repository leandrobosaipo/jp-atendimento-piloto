"""Provision only the isolated pilot's synthetic intake. First application is deliberately single-use."""
import argparse
import http.cookiejar
import json
import os
import re
import secrets
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from install import ROOT, SECRET, dc, run, wait_http

PRIVATE=SECRET/"flows-v1"
SRC=ROOT/"flows"
def dump(name,data):
    with (PRIVATE/name).open("x") as f:
        json.dump(data,f,indent=2)

def process(args,stdin=""):
    p=subprocess.run(args,input=stdin,text=True,cwd=ROOT,capture_output=True,timeout=180)
    if p.returncode:
        # Do not log stdout: bootstrap output may contain a scoped token.
        safe=re.findall(r"(?:[A-Za-z0-9_:]+(?:Error|Exception))", p.stderr)
        (PRIVATE/"last-process-error").write_text(p.stderr)
        raise RuntimeError("Scoped command failed: "+",".join(safe[-3:]))
    return p.stdout

class Local:
    def __init__(self,port):
        self.port=port
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args): return None
        self.client=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def req(self,method,path,data=None,headers=None,form=False):
        assert path.startswith("/") and not path.startswith("//")
        hdr={"Content-Type":"application/x-www-form-urlencoded" if form else "application/json", **(headers or {})}
        b=None if data is None else (urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode())
        req=urllib.request.Request(f"http://127.0.0.1:{self.port}"+path,data=b,headers=hdr,method=method)
        try:
            with self.client.open(req,timeout=65) as r:
                raw=r.read();return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw=e.read()
            if PRIVATE.is_dir():
                (PRIVATE/"last-http-error").write_bytes(raw)
            # Identity and tokens never appear in the public error.
            raise RuntimeError(f"{method} {path} returned HTTP {e.code}") from None

def n8n_login():
    api=Local(15678)
    api.req("POST","/rest/login",{"emailOrLdapLoginId":"fixture@example.invalid","password":(SECRET/"n8n-owner").read_text().strip()})
    return api

def postgres_setup(password):
    # Only a newly generated hexadecimal password is interpolated into this static, private stdin.
    assert re.fullmatch(r"[a-f0-9]{64}",password)
    cmd=["docker","compose","exec","-T","postgres","psql","-v","ON_ERROR_STOP=1","-U","postgres"]
    process([*cmd,"-d","postgres"],f"CREATE ROLE synthetic_intake LOGIN PASSWORD '{password}';\nCREATE DATABASE synthetic_intake OWNER synthetic_intake;\nREVOKE ALL ON DATABASE synthetic_intake FROM PUBLIC;\n")
    process([*cmd,"-d","synthetic_intake"],"""SET ROLE synthetic_intake;
CREATE TABLE pilot_events(event_id text PRIMARY KEY,payload jsonb NOT NULL,attempts integer NOT NULL DEFAULT 1,state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','simulated','handoff','uncertain')),route text,message_id bigint,reply text,created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz);
""")

def configure(args):
    os.umask(0o077)
    if run("git","rev-parse","HEAD")!=args.source_commit or run("git","status","--porcelain") or not re.fullmatch("[a-f0-9]{40}",args.source_commit):
        raise SystemExit("Expected clean published release")
    if args.ci:
        if os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("CI required")
    elif not (os.geteuid()==0 and socket.gethostname()=="agentes-lab" and args.machine_id==Path("/etc/machine-id").read_text().strip() and run("systemd-detect-virt")=="microsoft"):
        raise SystemExit("Expected identified Dell guest")
    baseline=json.loads((SECRET/"flow-memory-ready.json").read_text())
    if baseline['source']!=args.from_commit or not baseline['volumePreserved'] or PRIVATE.exists():
        raise SystemExit("Unexpected baseline or existing application. Reconcile before proceeding.")
    services=["postgres","redis","chat","worker","clock","flow"]
    ids={n:dc("ps","-q",n) for n in services}
    if not all(ids.values()) or dc("exec","-T","postgres","psql","-U","postgres","-d","postgres","-Atc","SELECT count(*) FROM pg_database WHERE datname='synthetic_intake'")!="0":
        raise SystemExit("Expected fresh isolated flow data")
    if not args.apply:
        print("Synthetic workflow preflight passed.")
        return
    PRIVATE.mkdir(mode=0o700)
    dump("intent.json",{"source":args.source_commit,"from":args.from_commit})
    dc("up","-d","--no-deps","--force-recreate","clock")
    wait_http("clock")
    if any(dc("ps","-q",n)!=c for n,c in ids.items() if n!="clock") or dc("exec","-T","clock","node","-p","[process.env.TZ,process.env.GENERIC_TIMEZONE].join('/')")!="America/Cuiaba/America/Cuiaba":
        raise RuntimeError("Clock recreation drift")
    print("Scoped timezone and five sibling IDs verified",flush=True)
    cfg={"dbPassword":secrets.token_hex(32),"hookSecret":secrets.token_hex(32)}
    postgres_setup(cfg["dbPassword"])
    ruby=(SRC/"bootstrap-chat.rb").read_text()
    print("Bootstrapping synthetic Chatwoot inbox",flush=True)
    out=process(["docker","compose","run","--rm","-T","--no-deps","chat","bundle","exec","rails","runner","-"],ruby)
    cfg.update(json.loads(next(l.partition("=")[2] for l in out.splitlines() if l.startswith("PILOT_CONFIG="))))
    print("Registering model-free Langflow graph",flush=True)
    lf=Local(17860)
    password=next(l.partition("=")[2] for l in (SECRET/"flow.env").read_text().splitlines() if l.startswith("LANGFLOW_SUPERUSER_PASSWORD="))
    access=lf.req("POST","/api/v1/login",{"username":"fixture","password":password},form=True)["access_token"]
    hdr={"Authorization":"Bearer "+access}
    graph=json.loads(process(["docker","compose","exec","-T","flow","python","-"],(SRC/"langflow_graph.py").read_text()))
    created=lf.req("POST","/api/v1/flows/",graph,hdr)
    cfg["flowId"]=created["id"]
    cfg["flowToken"]=lf.req("POST","/api/v1/api_key/",{"name":"Pilot offline intake"},hdr)["api_key"]
    sample=lf.req("POST","/api/v1/run/"+cfg["flowId"],{"input_value":"faq","input_type":"chat","output_type":"chat","session_id":"demo-preflight"},{"x-api-key":cfg["flowToken"]})
    val=sample["outputs"][0]["outputs"][0]["results"]["message"]["text"]
    if val!="FAQ do piloto fictício: atendimento de segunda a sexta, das 09h às 17h. Categoria: faq":
        raise RuntimeError("Unexpected offline graph output")
    dump("resources.json",cfg)
    print("Binding local n8n credentials and checking connections",flush=True)
    n=n8n_login()
    for key,typ,name,data in [
      ("dbCredential","postgres","Pilot synthetic ledger",{"host":"postgres","port":5432,"database":"synthetic_intake","user":"synthetic_intake","password":cfg["dbPassword"],"ssl":"disable"}),
      ("flowCredential","httpHeaderAuth","Pilot offline FAQ",{"name":"x-api-key","value":cfg["flowToken"]}),
      ("chatCredential","httpHeaderAuth","Pilot synthetic inbox",{"name":"api_access_token","value":cfg["chatToken"]}),
      ("inboundCredential","httpHeaderAuth","Pilot intake header",{"name":"X-Pilot-Fixture","value":cfg["hookSecret"]})]:
        c=n.req("POST","/rest/credentials",{"name":name,"type":typ,"data":data})["data"]
        cfg[key]={"id":c["id"],"name":c["name"]}
    bindings={k:v for k,v in cfg.items() if k.endswith("Credential") or k in ("flowId","accountId","knownConversation","reviewConversation")}
    graph=json.loads(process(["docker","compose","exec","-T","clock","node","-e",(SRC/"intake.cjs").read_text()],json.dumps(bindings)))
    expected=graph["connections"]
    if len(expected)!=8 or len(graph["nodes"])!=10:raise RuntimeError(f"Incomplete authored graph (nodes={len(graph['nodes'])}, wires={len(expected)})")
    w=n.req("POST","/rest/workflows",graph)["data"]
    check=n.req("GET","/rest/workflows/"+w["id"])["data"]
    if check["connections"]!=expected or check["active"]:raise RuntimeError("Readback graph mismatch")
    n.req("POST","/rest/workflows/"+w["id"]+"/activate",{"versionId":check["versionId"]})
    live=n.req("GET","/rest/workflows/"+w["id"])["data"]
    if not live["active"] or live["settings"]["timezone"]!="America/Cuiaba":raise RuntimeError("Activation readback failed")
    dump("ready.json",{"source":args.source_commit,"workflowId":w["id"],"versionId":live["versionId"],"flowId":cfg["flowId"],"nodes":len(graph["nodes"]),"accountId":cfg["accountId"],"knownConversation":cfg["knownConversation"],"reviewConversation":cfg["reviewConversation"],"clockTimezone":"America/Cuiaba"})
    print("Synthetic webhook active in local pilot")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for k in ("source-commit","from-commit"):p.add_argument("--"+k,required=True)
    p.add_argument("--machine-id");p.add_argument("--ci",action="store_true");p.add_argument("--apply",action="store_true")
    configure(p.parse_args())
