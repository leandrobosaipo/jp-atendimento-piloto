#!/usr/bin/python3
import json,socket,sys
try:
    raw=sys.stdin.buffer.read(4097)
    if not 1<=len(raw)<=4096:raise ValueError
    obj=json.loads(raw)
    payload=json.dumps(obj,ensure_ascii=False,separators=(",",":")).encode()+b"\n"
    if len(payload)>4096:raise ValueError
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(65);s.connect("/run/atendimento-piloto-ai.sock");s.sendall(payload)
        with s.makefile("rb") as f:response=f.readline(16385)
    if not response.endswith(b"\n") or len(response)>16384:raise ValueError
    print(json.dumps(json.loads(response),ensure_ascii=False))
except Exception:
    print('{"status":"error","code":"unavailable"}')
