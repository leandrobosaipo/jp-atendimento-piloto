"""Project one authenticated Chatwoot conversation to the local receipt dispatcher."""
import json
import os
from pathlib import Path
import socket
import struct
import urllib.request
from personal import NoRedirect
from bridge import human_pause


def serve(listener, cfg):
    account,inbox=cfg["account"],cfg["inbox"]
    assert all(type(i) is int and i>0 for i in (account,inbox))
    token=Path(cfg["token_file"]).read_text().strip()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def get(path):
        url="http://127.0.0.1:13300/api/v1/accounts/"+str(account)+path
        with opener.open(urllib.request.Request(url,headers={"api_access_token":token}),timeout=3) as r:
            data=r.read(500001)
            if len(data)>500000:raise ValueError()
            return json.loads(data)
    while True:
        conn,_=listener.accept()
        with conn:
            conn.settimeout(5)
            try:
                if struct.unpack("3i",conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=0:raise ValueError()
                with conn.makefile("rb") as stream:line=stream.readline(129)
                req=json.loads(line);cid=req["conversation"]
                if len(line)>128 or not line.endswith(b"\n") or set(req)!={"conversation"} or type(cid) is not int or cid<1:raise ValueError()
                row=get("/conversations/"+str(cid))
                if (row["account_id"],row["inbox_id"],row["id"])!=(account,inbox,cid):raise ValueError()
                meta=row["meta"];sender=meta["sender"]
                result={k:row[k] for k in ("account_id","inbox_id","id","status","labels")}
                result["meta"]={"sender":{"id":sender["id"],"identifier":sender["identifier"]},
                                "assignee":True if meta.get("assignee") else None,
                                "team":True if meta.get("team") else None}
                if cfg.get("intakes"):
                    scoped=(account,inbox,meta["sender"]["id"],cid)
                    assert result["account_id"]==account and result["inbox_id"]==inbox and result["id"]==cid
                    observed=bool(meta.get("assignee") or meta.get("team") or "jp-atendimento-humano" in result["labels"])
                    if human_pause(cfg["intakes"],scoped,observed):
                        result["labels"]=list(set(result["labels"])|{"jp-atendimento-humano"})
            except Exception:
                result={"error":"conversation_unavailable"}
            try:conn.sendall((json.dumps(result)+"\n").encode())
            except OSError:pass


if __name__=="__main__":
    cfg=json.loads(Path("/etc/jp-resume-source/config.json").read_bytes())
    assert int(os.environ["LISTEN_PID"])==os.getpid() and int(os.environ["LISTEN_FDS"])==1
    serve(socket.socket(fileno=3),cfg)
