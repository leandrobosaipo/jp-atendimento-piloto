#!/usr/bin/python3
"""Cockpit bridge. Authorization is the operator's Unix socket access."""
import http.client, json, socket, sys
class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(6)
        self.sock.connect("/run/agenda-renovacao/operator.sock")
try:
    b=sys.stdin.buffer.read(4097)
    if not 0<len(b)<=4096: raise ValueError()
    x=json.loads(b)
    if x.get("op") not in ("status","start","action"): raise ValueError()
    conn=UnixHTTP("localhost",timeout=6)
    conn.request("POST","/operate",b,{"Content-Type":"application/json"})
    response=conn.getresponse()
    data=response.read(1750001)
    if len(data)>1750000:raise ValueError()
    print(json.dumps(json.loads(data)))
except Exception:
    print('{"state":"unavailable"}')
