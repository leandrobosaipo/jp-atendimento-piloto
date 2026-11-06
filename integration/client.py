#!/usr/bin/python3
import json,socket,sys
try:
    message=json.loads(sys.stdin.buffer.read(4097))
    raw=json.dumps(message,separators=(",",":"),ensure_ascii=False).encode()+b"\n"
    if len(raw)>4096:raise ValueError
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(125);sock.connect("/run/atendimento-piloto-integracao/panel.sock");sock.sendall(raw)
        with sock.makefile("rb") as f:result=json.loads(f.readline(2_100_001))
    print(json.dumps(result,separators=(",",":"),ensure_ascii=False))
except Exception:
    print('{"status":"error","code":"unavailable"}')
