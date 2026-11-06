"""Single initial integrated inbox, worker route and Cockpit package for an identified pilot."""
import argparse,grp,hashlib,ipaddress,json,os,pathlib,re,secrets,shutil,subprocess,sys,time
SRC=pathlib.Path(__file__).resolve().parent;ROOT=SRC.parent;sys.path.insert(0,str(ROOT))
from configure_flows import n8n_login
ETC=pathlib.Path("/etc/atendimento-piloto-integracao");BIN=pathlib.Path("/usr/local/libexec/atendimento-piloto-integracao");WEB=pathlib.Path("/usr/local/share/cockpit/conversaspiloto")
UNIT=pathlib.Path("/etc/systemd/system");INBOX=ROOT/"private/integration-inbox";R=ROOT/"private/integration-install"
def checked(*a,stdin="",timeout=100):
    p=subprocess.run(a,cwd=ROOT,text=True,input=stdin,capture_output=True,timeout=timeout)
    if p.returncode:
        if R.is_dir():(R/"diagnostic-private").write_text(p.stderr)
        raise RuntimeError("Private command diagnostic: "+str(p.returncode))
    return p.stdout.strip()
def git(*a):return checked("git","-c","safe.directory="+str(ROOT),*a)
def newfile(p,b,mode=0o600):
    with p.open("xb") as f:f.write(b)
    p.chmod(mode)
def private(p,maxbytes):
    if not p.is_file() or p.is_symlink() or p.stat().st_mode&0o077 or p.stat().st_uid!=0 or p.stat().st_size>maxbytes:raise SystemExit("Invalid private file")
    return p.read_bytes()
def install(args):
    os.umask(0o077)
    if os.geteuid()!=0 or git("rev-parse","HEAD")!=args.source_commit or git("status","--porcelain") or not re.fullmatch("[a-f0-9]{40}",args.source_commit):raise SystemExit("Expected clean identified checkout")
    if args.ci:
        if os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("CI required")
    elif not (checked("hostname")=="agentes-lab" and checked("systemd-detect-virt")=="microsoft" and pathlib.Path("/etc/machine-id").read_text().strip()==args.machine_id):raise SystemExit("Guest identity mismatch")
    if not (ROOT/"private/flows-v1/verified.json").exists() or not (ROOT/"private/assistant-install/ready.json").exists():raise SystemExit("Expected established pilot")
    mf=private(INBOX/"assets.json",80000);catalog=json.loads(mf)
    if hashlib.sha256(mf).hexdigest()!=args.manifest_sha256:raise SystemExit("Artwork manifest mismatch")
    files={}
    for a in catalog["assets"]:
        n=a["filename"]
        if pathlib.Path(n).name!=n or a.get("approved") is not True or a["group"] not in ("servicos","clube-jp"):raise SystemExit("Unexpected asset")
        b=private(INBOX/"assets"/n,1_500_000)
        if len(b)!=a["bytes"] or not b.startswith(b"\xff\xd8") or hashlib.sha256(b).hexdigest()!=a["sha256"]:raise SystemExit("Artwork fingerprint mismatch")
        files[n]=b
    if len(files)!=6 or sorted(a["group"] for a in catalog["assets"])!=["clube-jp"]*3+["servicos"]*3:raise SystemExit("Expected exactly six approved images")
    raw=private(INBOX/"appbarber-readonly.json",32000);source=json.loads(raw)
    if source.get("origin")!="https://sistema.appbarber.com.br" or not source.get("cookieHeader") or not source.get("services") or not source.get("professionals"):raise SystemExit("Missing read-only provider")
    n=json.loads(checked("docker","network","inspect","atendimento-piloto_pilot"))[0]
    bridge=n["IPAM"]["Config"][0]["Gateway"]
    if not n["Internal"] or n["Driver"]!="bridge" or not ipaddress.IPv4Address(bridge).is_private:raise SystemExit("Expected existing isolated bridge")
    drop=UNIT/"atendimento-piloto-ai.service.d/40-integration.conf";service=UNIT/"atendimento-piloto-integracao.service"
    for p in (R,ETC,BIN,WEB,drop,service,pathlib.Path("/var/lib/atendimento-piloto-integracao")):
        if os.path.lexists(p):raise SystemExit("Existing integration object; reconcile receipts")
    if checked("ss","-Hltn","sport = :19735"):raise SystemExit("Port in use")
    if not args.apply:print("Verified six images, isolated bridge and new integration");return
    R.mkdir(mode=0o700);newfile(R/"intent.json",json.dumps({"source":args.source_commit,"assets":args.manifest_sha256}).encode())
    n8=n8n_login()
    print("Creating isolated native API inbox",flush=True)
    out=checked("docker","compose","run","--rm","--no-deps","-T","chat","bundle","exec","rails","runner","-",stdin=(SRC/"bootstrap-chat.rb").read_text(),timeout=180)
    cfg=json.loads(next(l.partition("=")[2] for l in out.splitlines() if l.startswith("PILOT_CONFIG=")))
    if set(cfg["actors"])!={"A","B","C"} or not all(type(a["conversation"]) is int and a["conversation"]>0 for a in cfg["actors"].values()):raise RuntimeError("Incomplete inbox references")
    cfg.update(bridge=bridge,hmac=secrets.token_hex(32),hook_token=secrets.token_hex(32),adapter_token=secrets.token_hex(32))
    gid=grp.getgrnam("delladmin").gr_gid
    for p,mode in ((ETC,0o750),(BIN,0o755),(WEB,0o755)):
        p.mkdir();p.chmod(mode)
    os.chown(ETC,0,gid);assets=ETC/"assets";assets.mkdir();assets.chmod(0o750);os.chown(assets,0,gid)
    for n,b in files.items():newfile(assets/n,b,0o640);os.chown(assets/n,0,gid)
    newfile(ETC/"runtime.json",json.dumps(cfg).encode());newfile(ETC/"assets.json",mf);newfile(ETC/"appbarber-readonly.json",raw);newfile(ETC/"integration.key",cfg["hmac"].encode())
    for n in ("service.py","client.py","policy.py","provider.py"):newfile(BIN/n,(SRC/n).read_bytes(),0o755)
    for n in ("manifest.json","index.html","app.js","style.css"):newfile(WEB/n,(SRC/"cockpit"/n).read_bytes(),0o644)
    if not drop.parent.exists():drop.parent.mkdir();drop.parent.chmod(0o755)
    newfile(drop,b"[Service]\nLoadCredential=integration-hmac:/etc/atendimento-piloto-integracao/integration.key\n",0o644)
    newfile(service,(SRC/service.name).read_bytes(),0o644);checked("systemctl","daemon-reload")
    checked("systemctl","restart","atendimento-piloto-ai.service")
    checked("systemctl","enable","--now",service.name)
    ids={"bridge":"http://"+bridge+":19735"}
    for label,key,header in [("hook","hook_token","X-Pilot-Integrated"),("adapter","adapter_token","X-Pilot-Adapter")]:
        name="Integrated pilot "+label
        c=n8.req("POST","/rest/credentials",{"name":name,"type":"httpHeaderAuth","data":{"name":header,"value":cfg[key]}})["data"]
        ids[label]={"name":name,"id":c["id"]}
    wf=json.loads(checked("docker","compose","exec","-T","clock","node","-e",(SRC/"workflow.cjs").read_text(),stdin=json.dumps(ids)))
    if len(wf["nodes"])!=4 or len(wf["connections"])!=3:raise RuntimeError("Incorrect stage count")
    w=n8.req("POST","/rest/workflows",wf)["data"];read=n8.req("GET","/rest/workflows/"+w["id"])["data"]
    if read["active"] or read["connections"]!=wf["connections"]:raise RuntimeError("Graph mismatch")
    n8.req("POST","/rest/workflows/"+w["id"]+"/activate",{"versionId":read["versionId"]})
    read=n8.req("GET","/rest/workflows/"+w["id"])["data"];assert read["active"] and read["settings"]["saveDataErrorExecution"]=="none"
    ui=json.loads(checked("runuser","-u","delladmin","--",str(BIN/"client.py"),stdin='{"op":"history","actor":"A"}'));assert ui["status"]=="ok"
    result={"source":args.source_commit,"workflow":w["id"],"version":read["versionId"],"inbox":cfg["inbox"],"conversations":{k:x["conversation"] for k,x in cfg["actors"].items()},"approvedImages":len(files),"manifest":args.manifest_sha256}
    newfile(R/"ready.json",json.dumps(result).encode());print(json.dumps(result))
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--source-commit",required=True);p.add_argument("--manifest-sha256",required=True);p.add_argument("--machine-id");p.add_argument("--apply",action="store_true");p.add_argument("--ci",action="store_true");install(p.parse_args())
