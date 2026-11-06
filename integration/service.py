#!/usr/bin/python3
"""Only the pilot bridge can call these fixed n8n steps. The Cockpit login uses a Unix socket."""
import base64, datetime as dt, hashlib, hmac, http.server, io, json, os, pathlib, re, secrets, socket, socketserver, sqlite3, threading, unicodedata, urllib.error, urllib.parse, urllib.request, uuid
from zoneinfo import ZoneInfo
import provider

HERE=pathlib.Path(__file__).resolve().parent
ZONE=ZoneInfo("America/Cuiaba")
class Stop(Exception):pass
def need(condition,reason):
    if not condition:raise Stop(reason)
def uid(v):
    need(isinstance(v,str) and str(uuid.UUID(v))==v,"invalid_id");return v
def normals(s):
    return "".join(c for c in unicodedata.normalize("NFD",s.lower()) if not unicodedata.combining(c))
def sha(b):return hashlib.sha256(b).hexdigest()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None
def http(port,path,data=None,headers=None,ctype="application/json",timeout=60,limit=2_000_000):
    need(port in (13300,15678) and path.startswith("/") and not path.startswith("//"),"invalid_local_destination")
    body=data if isinstance(data,bytes) else (json.dumps(data).encode() if data is not None else None)
    req=urllib.request.Request("http://127.0.0.1:"+str(port)+path,data=body,headers={"Content-Type":ctype,**(headers or {})})
    op=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
    with op.open(req,timeout=timeout) as r:
        raw=r.read(limit+1);need(len(raw)<=limit,"oversize");return json.loads(raw)
def multipart(fields,blob):
    boundary="pilot-"+secrets.token_hex(24);chunks=[]
    for key,value in fields.items():
        chunks.append(("--"+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+str(value)+"\r\n").encode())
    chunks.append(("--"+boundary+'\r\nContent-Disposition: form-data; name="attachments[]"; filename="tabela.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()+blob+b"\r\n")
    return b"".join(chunks)+("--"+boundary+"--\r\n").encode(),"multipart/form-data; boundary="+boundary
def stored_image(url):
    op=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
    for _ in range(3):
        parsed=urllib.parse.urlsplit(url)
        need(parsed.hostname in ("127.0.0.1","localhost") and parsed.port in (3000,13300,None) and parsed.path.startswith("/rails/active_storage/") and not parsed.username and not parsed.fragment,"nonlocal_attachment")
        local="http://127.0.0.1:13300"+parsed.path+("?"+parsed.query if parsed.query else "")
        try:
            with op.open(local,timeout=10) as r:
                b=r.read(1_500_001);need(len(b)<=1_500_000 and b.startswith(b"\xff\xd8"),"invalid_image");return b
        except urllib.error.HTTPError as e:
            if e.code not in (301,302,303,307,308):raise
            url=urllib.parse.urljoin(local,e.headers["Location"])
    raise Stop("attachment_redirect")
def ai_socket(signed,key):
    body={"op":"integrated",**signed}
    body["signature"]=hmac.new(key,json.dumps(signed,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode(),hashlib.sha256).hexdigest()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(65);sock.connect("/run/atendimento-piloto-ai.sock")
        sock.sendall(json.dumps(body,ensure_ascii=False).encode()+b"\n")
        with sock.makefile("rb") as f:r=json.loads(f.readline(16385))
    need(r.get("status")=="ok","ai_"+str(r.get("code","unavailable")));return r
class App:
    def __init__(self,settings,assets,prov,db,asset_dir,aisend=None):
        self.cfg=settings;self.catalog=assets;self.prov=prov;self.dbfile=str(db);self.asset_dir=pathlib.Path(asset_dir);self.ai=aisend or ai_socket
        self.actor_locks={x:threading.Lock() for x in settings["actors"]}
        with self.db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS events(req TEXT PRIMARY KEY,actor TEXT NOT NULL,question TEXT NOT NULL,options TEXT NOT NULL,state TEXT NOT NULL,incoming INTEGER UNIQUE,outgoing INTEGER,reserved_asset TEXT,asset TEXT,plan TEXT,receipt TEXT,reason TEXT,stamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    def db(self):
        c=sqlite3.connect(self.dbfile,timeout=6);c.row_factory=sqlite3.Row;return c
    def row(self,rid):
        with self.db() as c:r=c.execute("SELECT * FROM events WHERE req=?",(rid,)).fetchone()
        need(r is not None,"unknown_request");return dict(r)
    def advance(self,rid,old,new,**fields):
        assert set(fields)<=set("incoming outgoing asset plan receipt reason".split())
        with self.db() as c:
            sql="UPDATE events SET state=?"+''.join(','+k+'=?' for k in fields)+" WHERE req=? AND state=?"
            changed=c.execute(sql,[new,*fields.values(),rid,old]).rowcount
        need(changed==1,"held")
    def chatpath(self,actor):
        conv=self.cfg["actors"][actor]["conversation"]
        return f"/api/v1/accounts/{self.cfg['account']}/conversations/{conv}/messages"
    def messages(self,actor):
        return http(13300,self.chatpath(actor),headers={"api_access_token":self.cfg["agent_token"]})["payload"]
    def options(self,msg):
        op=msg.get("intent","auto")
        need(op in ("auto","price","availability","services_art","club_art"),"invalid_intent")
        srv=msg.get("service","");prof=msg.get("professional","");day=msg.get("date","")
        need(isinstance(srv,str) and re.fullmatch(r"[a-z_0-9-]{0,50}",srv) and isinstance(prof,str) and re.fullmatch(r"[a-z_0-9-]{0,50}",prof) and isinstance(day,str) and re.fullmatch(r"(?:\d{4}-\d{2}-\d{2})?",day),"invalid_selection")
        return {"intent":op,"service":srv,"professional":prof,"date":day}
    def submit(self,msg):
        rid=uid(msg.get("request"));actor=msg.get("actor");q=msg.get("text");need(actor in self.cfg["actors"],"invalid_actor")
        need(isinstance(q,str) and 1<=len(q.strip())<=600,"invalid_text")
        sanitized=re.sub(r"\b20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"," ",q)
        need(not re.search(r"@|\b\d[\d .()-]{7,}\d\b",sanitized),"use_fictional_text")
        opts=self.options(msg);q=q.strip();o=json.dumps(opts,sort_keys=True)
        with self.actor_locks[actor]:
            from policy import classify,group_for
            g=group_for(classify(q,opts,self.prov)[0])
            choice=secrets.choice([a["id"] for a in self.catalog["assets"] if a["group"]==g]) if g else None
            with self.db() as c:
                fresh=c.execute("INSERT OR IGNORE INTO events(req,actor,question,options,state,reserved_asset) VALUES(?,?,?,?,'opening',?)",(rid,actor,q,o,choice)).rowcount
            if not fresh:
                row=self.row(rid);need((row["actor"],row["question"],row["options"])==(actor,q,o),"id_conflict")
                return self.status(rid)
            x=self.cfg["actors"][actor]
            path=f"/public/api/v1/inboxes/{self.cfg['channel']}/contacts/{x['source']}/conversations/{x['conversation']}/messages"
            try:
                incoming=http(13300,path,{"content":q,"echo_id":rid})
                mid=incoming["id"];need(type(mid) is int and mid>0,"invalid_incoming")
                self.advance(rid,"opening","incoming",incoming=mid)
                # Only this UI triggers the local n8n webhook. The workflow rereads the actual inbox record.
                http(15678,"/webhook/pilot-integrated-v1",{"request":rid,"incoming":mid},{"X-Pilot-Integrated":self.cfg["hook_token"]},timeout=110)
            except Exception:
                # Another worker may already have finished: only reconcile the same ID.
                pass
            return self.status(rid)
    def status(self,rid):
        row=self.row(uid(rid));return json.loads(row["receipt"]) if row["state"]=="done" else {"status":"held","request":rid,"state":row["state"],"incoming":row["incoming"],"outgoing":row["outgoing"]}
    def step(self,op,body):
        rid=uid(body.get("request"));row=self.row(rid);mid=body.get("incoming");need(type(mid) is int and row["incoming"]==mid,"wrong_incoming")
        key={"request":rid,"incoming":mid}
        if row["state"]=="done":return self.status(rid) if op=="deliver" else key
        if op=="claim":
            self.advance(rid,"incoming","verifying")
            x=self.cfg["actors"][row["actor"]]
            match=[m for m in self.messages(row["actor"]) if m.get("id")==mid and m.get("message_type")==0 and not m.get("private") and m.get("content")==row["question"] and m.get("sender",{}).get("id")==x["contact"]]
            need(len(match)==1,"inbox_readback_mismatch")
            self.advance(rid,"verifying","claimed");return key
        if op=="plan":
            self.advance(rid,"claimed","planning")
            p,asset=self.plan(row)
            self.advance(rid,"planning","prepared",plan=json.dumps(p,ensure_ascii=False),asset=asset);return key
        need(op=="deliver","unknown_step")
        self.advance(rid,"prepared","sending")
        p=json.loads(row["plan"]);art=self.asset(row["asset"]) if row["asset"] else None
        private=p["decision"]=="handoff";need(not (art and private),"private_art_mismatch")
        fields={"content":p["reply"],"message_type":"outgoing","private":"true" if private else "false"}
        if art:
            data,ct=multipart(fields,self.artbytes(art))
        else:
            data={**fields,"private":private};ct="application/json"
        response=http(13300,self.chatpath(row["actor"]),data,{"api_access_token":self.cfg["agent_token"]},ct)
        oid=response["id"];need(type(oid) is int and oid>0,"invalid_outcome")
        self.advance(rid,"sending","checking",outgoing=oid)
        m=next((m for m in self.messages(row["actor"]) if m.get("id")==oid),None)
        need(m and m["private"]==private and m["content"]==p["reply"],"message_readback_mismatch")
        attachment=None
        if art:
            values=m.get("attachments",[]);need(len(values)==1,"attachment_missing")
            f=values[0];need(sha(stored_image(f["data_url"]))==art["sha256"],"attachment_hash_mismatch")
            attachment={"id":f["id"],"asset":art["id"],"group":art["group"],"sha256":art["sha256"],"bytes":art["bytes"]}
        result={"status":"ok","request":rid,"actor":row["actor"],"conversation":self.cfg["actors"][row["actor"]]["conversation"],"incoming":mid,"outgoing":oid,"private":private,"reply":p["reply"],"provider":p["provider"],"ai":p["ai"],"attachment":attachment}
        self.advance(rid,"checking","done",receipt=json.dumps(result,ensure_ascii=False));return result
    def asset(self,aid):
        return next(a for a in self.catalog["assets"] if a["id"]==aid)
    def artbytes(self,a):
        filename=a["filename"];need(pathlib.Path(filename).name==filename,"invalid_asset_path")
        p=self.asset_dir/filename;need(not p.is_symlink(),"invalid_asset_file")
        b=p.read_bytes();need(len(b)==a["bytes"]<=1_500_000 and b.startswith(b"\xff\xd8") and sha(b)==a["sha256"],"asset_integrity");return b
    def plan(self,row):
        from policy import prepare
        context,fixed,decision,group=prepare(row["question"],json.loads(row["options"]),self.prov,self.catalog,provider.query)
        asset=self.asset(row["reserved_asset"]) if group else None
        if asset:need(asset["group"]==group,"reserved_group_mismatch")
        if asset:self.artbytes(asset)
        session=str(uuid.uuid5(uuid.NAMESPACE_URL,"isolated-chatwoot:"+str(self.cfg["inbox"])+":"+row["actor"]))
        ai=self.ai({"id":row["req"],"session":session,"text":row["question"],"context":context},self.cfg["hmac"].encode())
        answer=fixed or ai["reply"];action=decision or ai["decision"]
        return {"reply":answer,"decision":action,"provider":context,"ai":{k:ai[k] for k in ("model","tokens","estimate_micro_usd","mode","kb_version")}},asset["id"] if asset and action=="answer" else None
    def ui(self,msg):
        op=msg.get("op")
        if op=="ask":return self.submit(msg)
        if op=="status":return self.status(uid(msg.get("request")))
        if op=="asset":
            r=self.status(uid(msg.get("request")));need(r.get("status")=="ok" and r.get("attachment"),"no_attachment")
            a=self.asset(r["attachment"]["asset"]);return {"status":"ok","image":base64.b64encode(self.artbytes(a)).decode()}
        actor=msg.get("actor","A");need(op=="history" and actor in self.cfg["actors"],"invalid_request")
        with self.db() as c:
            rows=c.execute("SELECT req,question FROM events WHERE actor=? ORDER BY rowid DESC LIMIT 12",(actor,)).fetchall()
        return {"status":"ok","conversation":self.cfg["actors"][actor]["conversation"],"services":{k:v["name"] for k,v in self.prov["services"].items()},"professionals":{k:v["name"] for k,v in self.prov["professionals"].items()},"events":[{"question":x["question"],**self.status(x["req"])} for x in reversed(rows)]}

class Web(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        code=200
        try:
            need(hmac.compare_digest(self.headers.get("X-Pilot-Adapter",""),self.server.app.cfg["adapter_token"]),"denied")
            op=self.path.removeprefix("/");need(op in ("claim","plan","deliver"),"unknown_step")
            n=int(self.headers.get("Content-Length","0"));need(0<n<1000,"oversize")
            result=self.server.app.step(op,json.loads(self.rfile.read(n)))
        except Exception as e:
            code=409 if isinstance(e,Stop) else 502;result={"status":"held"}
        data=json.dumps(result,ensure_ascii=False).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
class Screen(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(8)
        try:
            raw=self.rfile.readline(4097);need(len(raw)<=4096 and raw.endswith(b"\n"),"oversize")
            result=self.server.app.ui(json.loads(raw))
        except Exception as e:result={"status":"error","code":str(e) if isinstance(e,Stop) else "unavailable"}
        self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b"\n")
class Unix(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
def serve():
    os.umask(0o077);cred=pathlib.Path(os.environ["CREDENTIALS_DIRECTORY"])
    cfg=json.loads((cred/"runtime").read_bytes());manifest=json.loads((cred/"manifest").read_bytes());prov=json.loads((cred/"appbarber").read_bytes())
    app=App(cfg,manifest,prov,pathlib.Path(os.environ["STATE_DIRECTORY"])/"events.sqlite3","/etc/atendimento-piloto-integracao/assets")
    web=http.server.ThreadingHTTPServer((cfg["bridge"],19735),Web);web.app=app
    path=pathlib.Path(os.environ["RUNTIME_DIRECTORY"])/"panel.sock"
    if path.exists():path.unlink() # Private systemd runtime path created only for this service.
    ui=Unix(str(path),Screen);ui.app=app;threading.Thread(target=web.serve_forever,daemon=True).start()
    ui.serve_forever()
if __name__=="__main__":serve()
