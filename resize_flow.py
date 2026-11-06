"""Converge only the existing synthetic Langflow service to its measured memory budget."""
import argparse
import json
import os
import re
import socket
from pathlib import Path
from install import ROOT, SECRET, dc, run, wait_http

def fingerprint(cid):
    value = json.loads(run("docker","inspect","--format","{{json .Mounts}}",cid))
    return sorted((m["Name"],m["Destination"]) for m in value if m["Type"]=="volume")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-commit",required=True)
    parser.add_argument("--from-commit",required=True)
    parser.add_argument("--machine-id")
    parser.add_argument("--ci",action="store_true")
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    os.umask(0o077)
    if not all(re.fullmatch("[0-9a-f]{40}",s) for s in [args.source_commit,args.from_commit]):
        raise SystemExit("Expected full commits")
    if run("git","rev-parse","HEAD")!=args.source_commit or run("git","status","--porcelain"):
        raise SystemExit("Expected clean published release")
    if args.ci:
        if os.getenv("GITHUB_ACTIONS")!="true":
            raise SystemExit("Expected an ephemeral runner")
    elif not (os.geteuid()==0 and socket.gethostname()=="agentes-lab" and args.machine_id==Path("/etc/machine-id").read_text().strip() and run("systemd-detect-virt")=="microsoft"):
        raise SystemExit("Identified Dell guest required")
    if json.loads((SECRET/"ready.json").read_text())["commit"]!=args.from_commit or not (SECRET/"fixture/result.json").is_file() or (SECRET/"flow-memory-intent.json").exists():
        raise SystemExit("Reconcile existing receipts")
    model=json.loads(dc("config","--format","json"))
    new_limit=1536*1024**2
    if model["name"]!="atendimento-piloto" or int(model["services"]["flow"]["mem_limit"])!=new_limit:
        raise SystemExit("Unexpected Compose release")
    ids={name:dc("ps","-q",name) for name in model["services"]}
    if not all(ids.values()):
        raise SystemExit("Expected all pilot services running")
    old=ids["flow"]
    def attr(cid,expr):
        return run("docker","inspect","--format",expr,cid)
    if int(attr(old,"{{.HostConfig.Memory}}"))!=1280*1024**2 or attr(old,"{{.Config.Image}}")!=model["services"]["flow"]["image"]:
        raise SystemExit("Unexpected Langflow baseline")
    mounts=fingerprint(old)
    if mounts!=[("atendimento-piloto_flow-files","/app/langflow")]:
        raise SystemExit("Unexpected application volume")
    if not args.apply:
        print("Scoped Langflow plan passed.")
        return
    intent={"from":args.from_commit,"source":args.source_commit,"name":"flow","previousLimitMiB":1280,"limitMiB":1536}
    with (SECRET/"flow-memory-intent.json").open("x") as f:
        json.dump(intent,f)
    dc("up","-d","--no-deps","--force-recreate","flow")
    wait_http("flow")
    new=dc("ps","-q","flow")
    if old==new or int(attr(new,"{{.HostConfig.Memory}}"))!=new_limit or fingerprint(new)!=mounts:
        raise RuntimeError("Updated service readback mismatch")
    if any(dc("ps","-q",name)!=cid for name,cid in ids.items() if name!="flow"):
        raise RuntimeError("Sibling service identity changed")
    with (SECRET/"flow-memory-ready.json").open("x") as f:
        json.dump(dict(intent,siblingIdsPreserved=True,volumePreserved=True),f)
    print("Only Langflow changed. Its original volume and all five sibling containers were preserved.")
if __name__=="__main__":
    main()
