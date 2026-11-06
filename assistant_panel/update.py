"""Update installed panel code while retaining the provider credential, base and usage ledger."""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from install import ROOT,SRC,BIN,WEB,run

def info():
    p=subprocess.run(["runuser","-u","delladmin","--",str(BIN/"client.py")],input='{"op":"info"}',capture_output=True,text=True,timeout=45)
    r=json.loads(p.stdout)
    if r.get("status")!="ok":raise SystemExit("Socket consumer unavailable")
    return r

def update(args):
    os.umask(0o077)
    if os.geteuid()!=0 or run("git","rev-parse","HEAD")!=args.source_commit or run("git","status","--porcelain"):raise SystemExit("Release guard failed")
    if args.ci:
        if os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("CI required")
    elif not (run("systemd-detect-virt")=="microsoft" and run("hostname")=="agentes-lab" and pathlib.Path("/etc/machine-id").read_text().strip()==args.machine_id):raise SystemExit("Guest identity mismatch")
    receipts=ROOT/"private/assistant-install"
    if json.loads((receipts/"ready.json").read_text())["source"]!=args.from_commit:raise SystemExit("Unexpected prior installation")
    target=receipts/("update-"+args.source_commit[:12])
    if target.exists():raise SystemExit("Reconcile prior update")
    before=info();files={"gateway.py":BIN/"gateway.py","cockpit/panel.js":WEB/"panel.js"}
    for name,path in files.items():
        old=subprocess.check_output(["git","-c","safe.directory="+str(ROOT),"show",args.from_commit+":assistant_panel/"+name],cwd=ROOT)
        if path.is_symlink() or path.read_bytes()!=old:raise SystemExit("Installed file differs from the known release")
    if not args.apply:
        print("Same panel, quota and content verified");return
    target.mkdir(mode=0o700)
    (target/"intent.json").write_text(json.dumps(dict(before=before,source=args.source_commit)))
    for name,path in files.items():
        shutil.copyfile(path,target/path.name)
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
            tmp=pathlib.Path(f.name);f.write((SRC/name).read_bytes())
        tmp.chmod(path.stat().st_mode&0o777);os.replace(tmp,path)
    run("systemctl","restart","atendimento-piloto-ai.service")
    after=info()
    if after["kb_version"]!=before["kb_version"] or after["total_reserved_micro_usd"]<before["total_reserved_micro_usd"]:raise RuntimeError("Usage or knowledge verification failed")
    (target/"ready.json").write_text(json.dumps(dict(after=after,source=args.source_commit)))
    print("Panel updated with retained quota and private configuration")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for n in ["source-commit","from-commit"]:p.add_argument("--"+n,required=True)
    p.add_argument("--machine-id");p.add_argument("--ci",action="store_true");p.add_argument("--apply",action="store_true")
    update(p.parse_args())
