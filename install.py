"""First install only. Reconcile partial state before another operation."""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from prepare import main as prepare

ROOT = Path(__file__).resolve().parent
SECRET = ROOT / "private"
PROJECT = "atendimento-piloto"
PORTS = {"chat": (13300, "/health"), "flow": (17860, "/health_check"), "clock": (15678, "/healthz/readiness")}
UNITS = {
    "forward.py": "/usr/local/libexec/atendimento-piloto-forward",
    "atendimento-piloto@.socket": "/etc/systemd/system/atendimento-piloto@.socket",
    "atendimento-piloto@.service": "/etc/systemd/system/atendimento-piloto@.service",
}

def run(*args, timeout=900, quiet=False):
    result = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        # Never print raw errors containing application configuration or SQL.
        if SECRET.is_dir():
            path = SECRET / ("error-" + str(time.time_ns()) + ".log")
            path.write_bytes(result.stdout)
            path.chmod(0o600)
        raise RuntimeError(f"{args[0]} exited with status {result.returncode}, see private diagnostic")
    return "" if quiet else result.stdout.decode().strip()

def dc(*args, **kwargs):
    return run("docker", "compose", *args, **kwargs)

def wait_http(service, timeout=480):
    port, path = PORTS[service]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",timeout=3) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(3)
    raise RuntimeError(service + " readiness timeout")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--source-commit",required=True)
    parser.add_argument("--machine-id")
    parser.add_argument("--ci",action="store_true")
    args=parser.parse_args()
    os.umask(0o077)
    if not re.fullmatch("[0-9a-f]{40}",args.source_commit) or run("git","rev-parse","HEAD") != args.source_commit or run("git","status","--porcelain"):
        raise SystemExit("The exact clean, published release is required.")
    if args.ci:
        if os.getenv("GITHUB_ACTIONS") != "true":
            raise SystemExit("Expected GitHub Actions")
    elif not (os.geteuid()==0 and socket.gethostname()=="agentes-lab" and args.machine_id and Path("/etc/machine-id").read_text().strip()==args.machine_id and run("systemd-detect-virt")=="microsoft"):
        raise SystemExit("Expected identified Dell guest as root")
    if not args.ci:
        mem=int(Path("/proc/meminfo").read_text().split("MemTotal:")[1].split()[0])
        if mem < 5_850_000 or len(os.sched_getaffinity(0)) < 4:
            raise SystemExit("Insufficient guest RAM or CPUs")
    if shutil.disk_usage("/").free < 11*2**30:
        raise SystemExit("At least 11 GiB free are required")
    for resource in ("container","network","volume"):
        if run("docker",resource,"ls",*(["-a"] if resource=="container" else []),"-q","--filter","label=com.docker.compose.project="+PROJECT):
            raise SystemExit("Pilot resources already exist. Reconcile the existing release.")
    if SECRET.exists():
        raise SystemExit("Private directory already exists. Reconcile first.")
    if any(os.path.lexists(path) for path in UNITS.values()) or not Path("/usr/lib/systemd/systemd-socket-proxyd").is_file():
        raise SystemExit("Unexpected systemd unit state")
    for port,_ in PORTS.values():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",port))
    if not args.apply:
        print("Fresh install preflight passed.")
        return
    prepare()
    (SECRET/"intent.json").write_text(json.dumps({"commit":args.source_commit}))
    model=json.loads(dc("config","--format","json"))
    if model["name"]!=PROJECT or not model["networks"]["pilot"]["internal"] or not all("@sha256:" in s["image"] for s in model["services"].values()):
        raise RuntimeError("Unsafe Compose model")
    print("Pulling pinned images",flush=True)
    dc("pull",quiet=True,timeout=1800)
    if shutil.disk_usage("/").free < 3*2**30:
        raise RuntimeError("Insufficient space after pull, stopped before creating services")
    privileged = [] if os.geteuid()==0 else ["sudo", "-n"]
    for name, path in UNITS.items():
        run(*privileged,"install","-D","-m","755" if name=="forward.py" else "644",str(ROOT/"units"/name),path)
    run(*privileged,"systemctl","daemon-reload")
    for port,_ in PORTS.values():
        run(*privileged,"systemctl","enable","--now",f"atendimento-piloto@{port}.socket")
    dc("up","-d","--wait","postgres","redis")
    print("Preparing Chatwoot schema",flush=True)
    dc("run","--name",PROJECT+"-schema","--rm","--no-deps","chat","bundle","exec","rails","db:chatwoot_prepare")
    for service in ("chat","worker","clock","flow"):
        print("Starting "+service,flush=True)
        dc("up","-d","--no-deps",service)
        if service in PORTS:
            wait_http(service)
    if int(dc("exec","-T","redis","redis-cli","SCARD","processes")) < 1:
        raise RuntimeError("No Sidekiq heartbeat in Redis")
    (SECRET/"ready.json").write_text(json.dumps({"commit":args.source_commit,"services":list(model["services"])}))
    print("Six services ready.")
if __name__=="__main__":
    main()
