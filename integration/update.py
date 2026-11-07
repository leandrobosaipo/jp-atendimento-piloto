"""In-place migration with a quiet admission window. Roll back code only."""
import argparse, contextlib, fcntl, hashlib, json, os, pathlib, re, sqlite3, subprocess, tempfile, time
from install import ROOT, BIN, WEB, ETC, INBOX, R, git, checked, newfile, private

UNIT="atendimento-piloto-integracao.service"
AI="atendimento-piloto-ai.service"
STATE=pathlib.Path("/var/lib/atendimento-piloto-integracao/events.sqlite3")
LEDGER=pathlib.Path("/var/lib/atendimento-piloto-ai/trial.sqlite3")
AI_BIN=pathlib.Path("/usr/local/libexec/atendimento-piloto-ai")
PANEL_SOCKET=pathlib.Path("/run/atendimento-piloto-integracao/panel.sock")
AI_SOCKET=pathlib.Path("/run/atendimento-piloto-ai.sock")

def demand(ok,reason):
    if not ok:raise RuntimeError(reason)
def digest(data):return hashlib.sha256(data).hexdigest()
def put(path,data,mode):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        f.write(data);f.flush();os.fsync(f.fileno());tmp=pathlib.Path(f.name)
    tmp.chmod(mode);os.replace(tmp,path)
def client(path,request):return json.loads(checked(str(path),stdin=json.dumps(request)))
def snapshot(db,backup):
    with sqlite3.connect(f"file:{db}?mode=ro",uri=True) as src,sqlite3.connect(backup) as dst:
        src.backup(dst);demand(dst.execute("PRAGMA quick_check").fetchone()[0]=="ok","Invalid SQLite backup")
def audit():
    with sqlite3.connect(f"file:{STATE}?mode=ro",uri=True) as db:
        rows=db.execute("SELECT req,state,incoming,outgoing,receipt FROM events ORDER BY rowid").fetchall()
    demand(all(r[1]=="done" for r in rows),"Reconcile unfinished events before updating")
    with sqlite3.connect(f"file:{LEDGER}?mode=ro",uri=True) as db:
        demand(not db.execute("SELECT 1 FROM turns WHERE status='pending' LIMIT 1").fetchone(),"Active model request")
    return {"events":len(rows),"receipt_hash":digest(json.dumps(rows).encode())}
@contextlib.contextmanager
def drain_input(path):
    mode=path.stat().st_mode & 0o777;demand(mode in (0o600,0o700),"Unexpected socket permissions")
    path.chmod(0)
    try:yield
    finally:
        if path.is_socket():path.chmod(mode)

def update(a):
    os.umask(0o077)
    demand(os.geteuid()==0 and re.fullmatch("[0-9a-f]{40}",a.source_commit) and git("rev-parse","HEAD")==a.source_commit and not git("status","--porcelain"),"Expected clean source at exact commit")
    if a.ci:demand(os.getenv("GITHUB_ACTIONS")=="true","Expected CI")
    else:demand(checked("hostname")=="agentes-lab" and checked("systemd-detect-virt")=="microsoft" and pathlib.Path("/etc/machine-id").read_text().strip()==a.machine_id,"Unexpected guest")
    pointers=[R/"current.json",ROOT/"private/assistant-install/installed-current.json"]
    prior=json.loads((pointers[0] if pointers[0].exists() else R/"ready.json").read_bytes())
    ready=json.loads((R/"ready.json").read_bytes())
    demand(prior["source"]==a.from_commit and re.fullmatch("[0-9a-f]{40}",a.from_commit),"Unexpected installed revision")
    if pointers[1].exists():demand(json.loads(pointers[1].read_bytes())["source"]==a.from_commit,"Gateway revision differs")
    target=R/("update-"+a.source_commit[:12]);demand(not target.exists(),"Reconcile previous attempt")
    ai_record=pointers[1].parent/target.name
    demand(not ai_record.exists(),"Existing gateway release receipt")
    for path in R.glob("update-*"):demand((path/"ready.json").is_file(),"Unfinished prior update")
    old=private(ETC/"assets.json",80000)
    demand(old==private(INBOX/"assets.json",80000) and digest(old)==a.old_manifest==prior["manifest"],"Manifest drift")
    catalog=json.loads(old)
    demand(catalog["price_source_policy"]=="pending_user_resolution" and catalog["service_art_prices"],"Unexpected previous policy")
    assets=catalog["assets"]
    demand(len({x["id"] for x in assets})==6 and sorted(x["group"] for x in assets)==["clube-jp"]*3+["servicos"]*3,"Expected six images")
    for item in assets:
        name=item["filename"];demand(pathlib.Path(name).name==name and item["approved"] is True,"Unexpected image")
        for path in (ETC/"assets"/name,INBOX/"assets"/name):
            demand(not path.is_symlink() and path.stat().st_size==item["bytes"] and digest(path.read_bytes())==item["sha256"],"Image drift")
    catalog["price_source_policy"]="artwork";new=(json.dumps(catalog,ensure_ascii=False,indent=2)+"\n").encode()
    demand(digest(new)==a.new_manifest,"Target manifest mismatch")
    mapping={"integration/"+n:BIN/n for n in ("client.py","policy.py","provider.py","service.py")}
    mapping.update({"integration/cockpit/"+n:WEB/n for n in ("index.html","manifest.json","style.css","app.js")})
    mapping["assistant_panel/gateway.py"]=AI_BIN/"gateway.py"
    changes={}
    for name,path in mapping.items():
        before=subprocess.check_output(["git","-c","safe.directory="+str(ROOT),"show",a.from_commit+":"+name],cwd=ROOT)
        demand(not path.is_symlink() and path.read_bytes()==before,"Installed code drift: "+name)
        after=(ROOT/name).read_bytes()
        if after!=before:changes[path]=(before,after,path.stat().st_mode&0o777)
    for path in (INBOX/"assets.json",ETC/"assets.json"):changes[path]=(old,new,0o600)
    pre=audit();info=client(AI_BIN/"client.py",{"op":"info"})
    demand(info.get("status")=="ok" and client(BIN/"client.py",{"op":"history","actor":"A"}).get("status")=="ok","Consumer preflight failed")
    if not a.apply:return {**pre,"plan":"update integration and gateway","files":len(changes)}
    with open(STATE,"rb") as guard,drain_input(PANEL_SOCKET):
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        time.sleep(1)
        demand(audit()==pre,"Activity during admission drain")
        with drain_input(AI_SOCKET):
            demand(client(AI_BIN/"client.py",{"op":"info"})==info and audit()==pre,"Gateway activity")
            target.mkdir(mode=0o700)
            metadata={str(path):{"before":digest(b),"after":digest(n),"backup":str(i)} for i,(path,(b,n,_)) in enumerate(changes.items())}
            newfile(target/"intent.json",json.dumps({"source":a.source_commit,"previous":a.from_commit,"files":metadata,**pre}).encode())
            snapshot(STATE,target/"events.sqlite3");snapshot(LEDGER,target/"trial.sqlite3")
            for i,(before,_,_) in enumerate(changes.values()):newfile(target/str(i),before)
            checked("systemctl","stop",UNIT)
            try:
                demand(audit()==pre,"Activity before cutover")
                for path,(_,after,mode) in changes.items():put(path,after,mode)
                checked("systemctl","restart",AI);checked("systemctl","start",UNIT)
                for _ in range(30):
                    if client(BIN/"client.py",{"op":"history","actor":"A"}).get("status")=="ok":break
                    time.sleep(.2)
                else:raise RuntimeError("Updated bridge did not start")
                demand(info==client(AI_BIN/"client.py",{"op":"info"}) and audit()==pre,"State verification failed")
                demand(all(path.read_bytes()==n for path,(_,n,_) in changes.items()),"Installed byte mismatch")
            except BaseException:
                checked("systemctl","stop",UNIT)
                for path,(before,_,mode) in changes.items():put(path,before,mode)
                checked("systemctl","restart",AI);checked("systemctl","start",UNIT)
                raise
            result={**ready,**pre,"source":a.source_commit,"previous":a.from_commit,"manifest":digest(new),"previous_manifest":digest(old),"backup_dir":str(target)}
            newfile(target/"ready.json",json.dumps(result).encode())
            ai_record.mkdir(mode=0o700);newfile(ai_record/"ready.json",json.dumps({"source":a.source_commit,"previous":a.from_commit,"integration_receipt":str(target/"ready.json")}).encode())
            for pointer in pointers:put(pointer,json.dumps(result).encode(),0o600)
            return result

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    for name in ("source-commit","from-commit","old-manifest","new-manifest"):parser.add_argument("--"+name,required=True)
    parser.add_argument("--machine-id");parser.add_argument("--apply",action="store_true");parser.add_argument("--ci",action="store_true")
    print(json.dumps(update(parser.parse_args())))
