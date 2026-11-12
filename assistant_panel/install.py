#!/usr/bin/python3
"""Install a first, private Cockpit package. No provider request during setup."""
import argparse
import hashlib
import json
import os
import pathlib
import pwd
import re
import shutil
import subprocess
from gateway import validate_kb, validate_operator_instructions

SRC=pathlib.Path(__file__).resolve().parent
ROOT=SRC.parent
ETC=pathlib.Path("/etc/atendimento-piloto-ai")
BIN=pathlib.Path("/usr/local/libexec/atendimento-piloto-ai")
WEB=pathlib.Path("/usr/local/share/cockpit/atendimentoteste")
UNIT=pathlib.Path("/etc/systemd/system")
NAMES=("atendimento-piloto-ai.socket","atendimento-piloto-ai.service")

def run(*args):
    if args[0]=="git":args=("git","-c","safe.directory="+str(ROOT),*args[1:])
    return subprocess.check_output(args,cwd=ROOT,text=True,stderr=subprocess.DEVNULL,timeout=50).strip()

def read_private(p,limit):
    if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077 or p.stat().st_uid!=0 or p.stat().st_size>limit:
        raise SystemExit("Invalid private input")
    return p.read_bytes()

def install(args):
    os.umask(0o077)
    if os.geteuid()!=0 or not re.fullmatch(r"[0-9a-f]{40}",args.source_commit) or run("git","rev-parse","HEAD")!=args.source_commit or run("git","status","--porcelain"):
        raise SystemExit("A verified root checkout is required")
    if args.ci:
        if os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("Expected CI")
    elif not (run("hostname")=="agentes-lab" and run("systemd-detect-virt")=="microsoft" and pathlib.Path("/etc/machine-id").read_text().strip()==args.machine_id and (ROOT/"private/flows-v1/verified.json").is_file()):
        raise SystemExit("Expected known pilot guest")
    pwd.getpwnam("delladmin")
    inbox=ROOT/"private/openai-inbox"
    if inbox.is_symlink() or not inbox.is_dir() or inbox.stat().st_mode&0o077 or inbox.stat().st_uid!=0:raise SystemExit("Secure inbox required")
    kb=read_private(inbox/"kb.json",131072);key=read_private(inbox/"openai.key",400)
    if hashlib.sha256(kb).hexdigest()!=args.kb_sha256 or not re.fullmatch(rb"sk-[A-Za-z0-9_-]{20,360}",key.strip()):raise SystemExit("Private configuration mismatch")
    validate_kb(json.loads(kb))
    instructions=(SRC/"default_operator_instructions.json").read_bytes()
    validate_operator_instructions(json.loads(instructions))
    receipt=ROOT/"private/assistant-install"
    for path in [ETC,BIN,WEB,receipt,*[UNIT/n for n in NAMES],pathlib.Path("/var/lib/atendimento-piloto-ai"),pathlib.Path("/run/atendimento-piloto-ai.sock")]:
        if os.path.lexists(path):raise SystemExit("Reconcile existing package state")
    if not args.apply:
        print("Private panel preflight passed")
        return
    receipt.mkdir(mode=0o700)
    (receipt/"intent.json").write_text(json.dumps({"source":args.source_commit,"kb_sha256":args.kb_sha256}))
    for target,mode in [(ETC,0o700),(BIN,0o755),(WEB,0o755)]:
        if not target.parent.exists():
            target.parent.mkdir(parents=True,mode=0o755);target.parent.chmod(0o755)
        target.mkdir();target.chmod(mode)
    for name,data in (("openai.key",key),("kb.json",kb),("operator-instructions.json",instructions)):
        with (ETC/name).open("xb") as f:f.write(data)
    for name in ("gateway.py","client.py"):
        shutil.copyfile(SRC/name,BIN/name);(BIN/name).chmod(0o755)
    for name in ("index.html","panel.css","panel.js","manifest.json"):
        shutil.copyfile(SRC/"cockpit"/name,WEB/name);(WEB/name).chmod(0o644)
    for name in NAMES:
        shutil.copyfile(SRC/name,UNIT/name);(UNIT/name).chmod(0o644)
    run("systemd-analyze","verify",*[str(UNIT/n) for n in NAMES])
    run("systemctl","daemon-reload");run("systemctl","enable","--now",NAMES[0])
    p=subprocess.run(["runuser","-u","delladmin","--",str(BIN/"client.py")],input='{"op":"info"}',capture_output=True,text=True,timeout=15)
    info=json.loads(p.stdout)
    if info["status"]!="ok" or info["kb_version"]!=json.loads(kb)["version"]:raise RuntimeError("Local consumer failed")
    (receipt/"ready.json").write_text(json.dumps({"source":args.source_commit,"info":info}))
    print("Cockpit package and restricted socket verified")

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--source-commit",required=True);parser.add_argument("--kb-sha256",required=True);parser.add_argument("--machine-id");parser.add_argument("--ci",action="store_true");parser.add_argument("--apply",action="store_true")
    install(parser.parse_args())
