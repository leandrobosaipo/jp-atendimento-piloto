#!/usr/bin/python3
"""Resolve only this project's container, then drop privileges before forwarding."""
import ipaddress
import os
import subprocess
import sys

services = {"13300": ("chat",3000), "17860": ("flow",7860), "15678": ("clock",5678)}
service, port = services[sys.argv[1]]
def docker(*args):
    return subprocess.check_output(["/usr/bin/docker", *args], text=True).strip()
ids = docker("ps", "-q", "--filter", "label=com.docker.compose.project=atendimento-piloto",
             "--filter", "label=com.docker.compose.service="+service).splitlines()
if len(ids) != 1:
    raise SystemExit("One matching pilot container is required")
ip = ipaddress.IPv4Address(docker("inspect","--format",'{{(index .NetworkSettings.Networks "atendimento-piloto_pilot").IPAddress}}',ids[0]))
if not ip.is_private or ip.is_loopback:
    raise SystemExit("Unexpected pilot endpoint")
os.execv("/usr/bin/setpriv", ["setpriv","--reuid=nobody","--regid=nogroup","--clear-groups",
         "--no-new-privs","/usr/lib/systemd/systemd-socket-proxyd","--exit-idle-time=10s",f"{ip}:{port}"])
