"""Install one verified private content revision, retaining its predecessor and usage ledger."""
import argparse
import hashlib
import json
import os
import pathlib
import tempfile
from gateway import validate_kb
from install import ROOT,ETC,read_private,run
from update import info,installed_source

def sha(b):return hashlib.sha256(b).hexdigest()

def update(a):
    os.umask(0o077)
    if os.geteuid()!=0 or run("git","rev-parse","HEAD")!=a.source_commit or run("git","status","--porcelain"):raise SystemExit("Release guard failed")
    if a.ci:
        if os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("CI required")
    elif not (run("systemd-detect-virt")=="microsoft" and run("hostname")=="agentes-lab" and pathlib.Path("/etc/machine-id").read_text().strip()==a.machine_id):raise SystemExit("Guest identity mismatch")
    receipts=ROOT/"private/assistant-install"
    if installed_source(receipts)!=a.source_commit:raise SystemExit("Expected deployed source")
    current=ETC/"kb.json";old=read_private(current,131072)
    new=read_private(ROOT/"private/openai-inbox"/("kb-"+a.expected_new+".json"),131072)
    if sha(old)!=a.expected_old or sha(new)!=a.expected_new:raise SystemExit("Private content hash mismatch")
    previous=validate_kb(json.loads(old));revised=validate_kb(json.loads(new))
    before=info()
    if previous["version"]==revised["version"] or before["kb_version"]!=previous["version"]:raise SystemExit("Unexpected content version")
    target=receipts/("knowledge-"+a.expected_new[:16])
    if target.exists():raise SystemExit("Reconcile existing knowledge update")
    if not a.apply:
        print("Private knowledge update preflight passed");return
    target.mkdir(mode=0o700)
    (target/"intent.json").write_text(json.dumps({"source":a.source_commit,"old":a.expected_old,"new":a.expected_new,"before":before}))
    with (target/"previous-kb.json").open("xb") as f:f.write(old)
    with tempfile.NamedTemporaryFile(dir=ETC,delete=False) as f:
        f.write(new);f.flush();os.fsync(f.fileno());tmp=pathlib.Path(f.name)
    if tmp.stat().st_mode&0o077:raise SystemExit("Unsafe staged file mode")
    os.replace(tmp,current)
    run("systemctl","restart","atendimento-piloto-ai.service");after=info()
    if after["kb_version"]!=revised["version"] or after["total_reserved_micro_usd"]<before["total_reserved_micro_usd"]:raise RuntimeError("Content or usage readback failed")
    (target/"ready.json").write_text(json.dumps({"source":a.source_commit,"new":a.expected_new,"after":after}))
    print("Private knowledge revision active; predecessor and budget preserved")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for k in ("source-commit","expected-old","expected-new"):p.add_argument("--"+k,required=True)
    p.add_argument("--ci",action="store_true");p.add_argument("--apply",action="store_true");p.add_argument("--machine-id")
    args=p.parse_args()
    import re
    if not all(re.fullmatch("[a-f0-9]{64}",h) for h in (args.expected_old,args.expected_new)):raise SystemExit("Expected SHA-256")
    update(args)
