"""Update installed panel code while retaining the provider credential, base and usage ledger."""
import argparse
import hashlib
import json
import os
import re
import pathlib
import shutil
import subprocess
import tempfile
from install import ROOT,SRC,BIN,WEB,run

def installed_source(receipts):
    original=json.loads((receipts/"ready.json").read_text())["source"]
    updates=sorted(p for p in receipts.glob("update-*") if p.is_dir())
    resolved=[]
    for p in updates:
        if (p/"rollback.json").is_file():
            rollback=json.loads((p/"rollback.json").read_text())
            intent=json.loads((p/"intent.json").read_text())
            if rollback.get("status")!="resolved" or rollback.get("source")!=intent.get("previous") or rollback.get("failed_source")!=intent.get("source"):
                raise SystemExit("Invalid rollback receipt")
            resolved.append(p)
    updates=[p for p in updates if p not in resolved]
    if any(not (p/"ready.json").is_file() for p in updates):
        raise SystemExit("Incomplete update requires reconciliation")
    pointer=receipts/"installed-current.json"
    if pointer.exists():
        active=json.loads(pointer.read_text())["source"]
        if active!=original and not any(json.loads((p/"ready.json").read_text())["source"]==active for p in updates):
            raise SystemExit("Pointer does not identify a completed update")
        return active
    # The first deployed updater wrote one full receipt before pointers existed.
    if len(updates)==1:return json.loads((updates[0]/"ready.json").read_text())["source"]
    if updates:raise SystemExit("Ambiguous legacy update sequence")
    return original

def info():
    p=subprocess.run(["runuser","-u","delladmin","--",str(BIN/"client.py")],input='{"op":"info"}',capture_output=True,text=True,timeout=45)
    r=json.loads(p.stdout)
    if r.get("status")!="ok":raise SystemExit("Socket consumer unavailable")
    return r

def update(args):
    os.umask(0o077)
    if os.geteuid()!=0 or not re.fullmatch(r"[0-9a-f]{40}",args.source_commit) or not re.fullmatch(r"[0-9a-f]{40}",args.from_commit) or run("git","rev-parse","HEAD")!=args.source_commit or run("git","status","--porcelain"):raise SystemExit("Release guard failed")
    if args.ci:
        if os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("CI required")
    elif not (run("systemd-detect-virt")=="microsoft" and run("hostname")=="agentes-lab" and pathlib.Path("/etc/machine-id").read_text().strip()==args.machine_id):raise SystemExit("Guest identity mismatch")
    receipts=ROOT/"private/assistant-install"
    if installed_source(receipts)!=args.from_commit:raise SystemExit("Unexpected prior installation")
    files={"gateway.py":BIN/"gateway.py","client.py":BIN/"client.py",**{"cockpit/"+n:WEB/n for n in ("panel.js","panel.css","index.html","manifest.json")}}
    if getattr(args,"maintenance",False):
        from maintenance import cutover
        print(json.dumps(cutover(args,receipts,files,run,info,ROOT,SRC)))
        return
    target=receipts/("update-"+args.source_commit[:12])
    if target.exists():raise SystemExit("Reconcile prior update")
    before=info();files={"gateway.py":BIN/"gateway.py","client.py":BIN/"client.py",**{"cockpit/"+n:WEB/n for n in ("panel.js","panel.css","index.html","manifest.json")}}
    for name,path in files.items():
        old=subprocess.check_output(["git","-c","safe.directory="+str(ROOT),"show",args.from_commit+":assistant_panel/"+name],cwd=ROOT)
        if path.is_symlink() or path.read_bytes()!=old:raise SystemExit("Installed file differs from the known release")
    if not args.apply:
        print("Same panel, quota and content verified");return
    target.mkdir(mode=0o700)
    (target/"intent.json").write_text(json.dumps(dict(before=before,source=args.source_commit,previous=args.from_commit)))
    changed=[]
    for name,path in files.items():
        if path.read_bytes()==(SRC/name).read_bytes():continue
        changed.append(name)
        shutil.copyfile(path,target/path.name)
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
            tmp=pathlib.Path(f.name);f.write((SRC/name).read_bytes())
        tmp.chmod(path.stat().st_mode&0o777);os.replace(tmp,path)
    if "gateway.py" in changed:run("systemctl","restart","atendimento-piloto-ai.service")
    after=info()
    if after["kb_version"]!=before["kb_version"] or after["total_reserved_micro_usd"]<before["total_reserved_micro_usd"]:raise RuntimeError("Usage or knowledge verification failed")
    state=dict(after=after,source=args.source_commit,previous=args.from_commit,changed=changed)
    (target/"ready.json").write_text(json.dumps(state))
    with tempfile.NamedTemporaryFile(mode="w",dir=receipts,delete=False) as f:
        json.dump({"source":args.source_commit},f);f.flush();os.fsync(f.fileno());tmp=pathlib.Path(f.name)
    os.replace(tmp,receipts/"installed-current.json")
    print("Panel updated with retained quota and private configuration")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for n in ["source-commit","from-commit"]:p.add_argument("--"+n,required=True)
    p.add_argument("--machine-id");p.add_argument("--ci",action="store_true");p.add_argument("--apply",action="store_true")
    p.add_argument("--maintenance",action="store_true",help="Controlled panel-only cutover with SQLite snapshots and code-only rollback")
    update(p.parse_args())
