"""Transport contract rehearsal. No network client, listener, secret or session."""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import time
from bridge import canonical, digest, need

GROUP = re.compile(r"[0-9]{5,30}(?:-[0-9]+)?@g\.us")
MEMBER = re.compile(r"[0-9]{5,20}(?::[0-9]+)?@(?:s\.whatsapp\.net|lid)")
ID = re.compile(r"[\w-]{6,120}", re.ASCII)
COMMAND = re.compile(r"(ok|assumo|atendido|resolvido)\s+(jp-[a-f0-9]{8})[.!?\s]*", re.I)

def envelope(body, instance, event, authenticated):
    need(authenticated is True and isinstance(body, dict), "unauthenticated")
    need(body.get("instance") == instance and str(body.get("event")).lower().replace("_",".") == event, "wrong_envelope")
    data=body.get("data")
    if isinstance(data,list):
        need(len(data)==1,"batch_requires_individual_validation")
        data=data[0]
    need(isinstance(data,dict) and isinstance(data.get("key"),dict),"missing_native_key")
    return data,data["key"]

def group_command(body, *, instance, group, allowed, verified_members, verified_at, now, alerts, authenticated, enabled):
    """Return an eligible command only. Caller's verified group snapshot and case
    notifications are distinct from fields supplied by the original participant."""
    need(enabled is True,"group_disabled")
    data,key=envelope(body,instance,"messages.upsert",authenticated)
    need(GROUP.fullmatch(group) and key.get("remoteJid")==group and key.get("fromMe") is False,"wrong_group_or_direction")
    actor=key.get("participant") or data.get("participant")
    need(isinstance(actor,str) and MEMBER.fullmatch(actor) and data.get("participant",actor)==actor,"invalid_participant")
    need(0<=now-verified_at<=86400 and actor in allowed and actor in verified_members,"membership_unverified")
    native=key.get("id")
    need(isinstance(native,str) and ID.fullmatch(native),"invalid_message_id")
    message=data.get("message") or {}
    extended=message.get("extendedTextMessage") or {}
    match=COMMAND.fullmatch(str(extended.get("text","")).strip())
    need(match,"unsupported_command")
    quote=(extended.get("contextInfo") or {}).get("stanzaId")
    code=match[2].upper()
    need(isinstance(quote,str) and ID.fullmatch(quote),"quote_required")
    same_code=[a for a in alerts if (a["instance"],a["group"],a["case"])==(instance,group,code)]
    need(len({a["case_id"] for a in same_code})==1,"ambiguous_case")
    case=[a for a in same_code if a["native_id"]==quote]
    need(len(case)==1,"quote_case_mismatch")
    stamp=data.get("messageTimestamp")
    need(type(stamp) in (float,int),"invalid_time")
    if stamp>1e12:stamp/=1000
    need(case[0]["opened_at"]<=stamp<=now+60,"invalid_time")
    return {"instance":instance,"group":group,"actor":actor,"id":native,"case":code,"quote":quote,"timestamp":stamp,
            "text_digest":hashlib.sha256(extended["text"].encode()).hexdigest(),
            "action":{"ok":"claim","assumo":"claim","atendido":"answered","resolvido":"resolve"}[match[1].lower()]}

class Fence:
    """All competing senders in this rehearsal share one ledger and lock inode.
    This does not fence independent production processes on a second machine."""
    def __init__(self,outbox):
        self.box=outbox
        self.directory=os.path.dirname(self.box.path)
        info=os.stat(self.directory);self.dir_identity=(info.st_dev,info.st_ino)
        with outbox.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS owner_epoch (id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT,epoch INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO owner_epoch VALUES(1,NULL,0)")
            db.execute("CREATE TABLE IF NOT EXISTS group_commands (instance TEXT,native TEXT,body TEXT NOT NULL,PRIMARY KEY(instance,native))")
            db.execute("CREATE TABLE IF NOT EXISTS observer_receipts (event_key TEXT,position INTEGER,observer TEXT,status TEXT NOT NULL,PRIMARY KEY(event_key,position,observer))")
    @contextmanager
    def lock(self):
        fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd);need((info.st_dev,info.st_ino)==self.dir_identity,"ledger_directory_replaced")
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:os.close(fd)
    def rotate(self,previous,owner):
        with self.lock(),self.box.db() as db:
            current=tuple(db.execute("SELECT owner,epoch FROM owner_epoch").fetchone())
            need(current==previous,"stale_owner")
            if owner is not None:
                need(current[0] is None and isinstance(owner,str) and owner,"freeze_before_transfer")
                need(not db.execute("SELECT 1 FROM channel_parts WHERE state IN ('sending','accepted') LIMIT 1").fetchone(),"unreconciled_parts")
            epoch=current[1]+1
            db.execute("UPDATE owner_epoch SET owner=?,epoch=?",(owner,epoch))
            return owner,epoch
    def advance(self,credential,event_key,transport):
        with self.lock():
            with self.box.db() as db:
                current=tuple(db.execute("SELECT owner,epoch FROM owner_epoch").fetchone())
            need(current[0] and current==credential,"stale_owner")
            transport.require_dispatch()
            return self.box.advance(event_key,transport)
    def record(self,body,**context):
        command=group_command(body,**context)
        with self.box.db() as db:
            key=(command["instance"],command["id"]);data=canonical(command)
            inserted=db.execute("INSERT OR IGNORE INTO group_commands VALUES(?,?,?)",(*key,data)).rowcount
            stored=db.execute("SELECT body FROM group_commands WHERE instance=? AND native=?",key).fetchone()
            need(stored[0]==data,"command_conflict")
        return {"command":command,"duplicate":not bool(inserted)}
    def confirm(self,event_key,position,transport,callback,*,observer,authenticated):
        with self.box.db() as db:
            entry=db.execute("SELECT event FROM channel_events WHERE event_key=?",(event_key,)).fetchone()
            part=db.execute("SELECT * FROM channel_parts WHERE event_key=? AND position=?",(event_key,position)).fetchone()
        need(entry and part and part["state"] in ("accepted","delivered"),"accepted_native_id_required")
        native=json.loads(part["native_id"])[2]
        proof=transport.proof(json.loads(entry[0]),native,json.loads(part["payload"]),callback,authenticated=authenticated,observer=observer)
        self.box.confirm(event_key,position,lambda *_:proof)
        with self.box.db() as db:
            db.execute("""INSERT INTO observer_receipts VALUES(?,?,?,?) ON CONFLICT(event_key,position,observer)
            DO UPDATE SET status=excluded.status WHERE observer_receipts.status!='read'""",(event_key,position,observer,proof["status"]))
            return db.execute("SELECT status FROM observer_receipts WHERE event_key=? AND position=? AND observer=?",(event_key,position,observer)).fetchone()[0]

class Contract:
    """Injected provider exchange. No base URL, auth header, remote asset fetch
    or default HTTP implementation. read_native supplies independent native content."""
    def __init__(self,instance,destination,*,exchange,asset_bytes,read_native,settings,clock=time.time):
        need(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",instance) and GROUP.fullmatch(destination),"invalid_route")
        self.instance=instance;self.destination=destination
        self.exchange=exchange;self.assets=asset_bytes;self.read_native=read_native
        self.settings=settings;self.clock=clock
    def require_dispatch(self):
        flags=self.settings()
        need(flags.get("enabled") is True and flags.get("group_notifications_enabled") is True and flags.get("quietByOperator") is False,"group_quiet")
        stamp=flags.get("verified_at")
        need(flags.get("group_jid")==self.destination and flags.get("members_authorized") is True and type(stamp) in (float,int) and 0<=self.clock()-stamp<=86400,"group_unverified")
    def scope(self,event):
        need((event["channel"],event["account"],event["sender"])==("whatsapp",self.instance,self.destination),"wrong_route")
    def wire(self,payload):
        if payload["kind"]=="text":return "sendText",{"number":self.destination,"text":payload["text"],"linkPreview":False}
        need(payload["kind"]=="image","unsupported_kind")
        asset=payload["asset"];raw=self.assets(asset["id"])
        need(isinstance(raw,bytes) and 4<=len(raw)<=1_500_000 and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9") and hashlib.sha256(raw).hexdigest()==asset["sha256"],"asset_mismatch")
        return "sendMedia",{"number":self.destination,"mediatype":"image","mimetype":"image/jpeg","caption":"Tabela","fileName":asset["filename"],"media":base64.b64encode(raw).decode(),"linkPreview":False}
    def send(self,event,payload):
        self.require_dispatch()
        self.scope(event)
        method,body=self.wire(payload);status,result=self.exchange("/message/"+method+"/"+self.instance,body)
        key=result.get("key",{})
        need(200<=status<300 and key.get("remoteJid")==self.destination and key.get("fromMe") is True and ID.fullmatch(str(key.get("id",""))),"acceptance_unproved")
        return {"id":key["id"],"account":self.instance,"destination":self.destination}
    def proof(self,event,native,payload,callback,*,authenticated,observer):
        self.scope(event)
        data,key=envelope(callback,self.instance,"messages.update",authenticated)
        need(key.get("id")==native and key.get("fromMe") is True and key.get("remoteJid")==self.destination,"receipt_not_bound")
        need(MEMBER.fullmatch(observer) and key.get("participant")==observer,"recipient_unproved")
        need(all(data.get(k,v)==v for k,v in {"keyId":native,"id":native,"remoteJid":self.destination,"participant":observer,"fromMe":True}.items()),"conflicting_receipt")
        for node in (key,data):
            need(node.get("remoteJidAlt",self.destination)==self.destination and node.get("participantAlt",observer)==observer,"ambiguous_identity")
        value=str(data.get("status",(data.get("update") or {}).get("status",""))).upper()
        mapping={"DELIVERY_ACK":"delivered","DELIVERED":"delivered","3":"delivered","READ":"read","READ_ACK":"read","4":"read"}
        need(value in mapping,"delivery_unproved")
        saved=self.read_native(native)
        need(saved and saved["instance"]==self.instance and saved["destination"]==self.destination and saved["id"]==native,"native_identity_mismatch")
        if payload["kind"]=="text":need(saved.get("text")==payload["text"],"native_text_mismatch")
        else:
            need(saved.get("mime")=="image/jpeg" and saved.get("caption")=="Tabela" and saved.get("filename")==payload["asset"]["filename"],"native_media_metadata_mismatch")
            need(hashlib.sha256(saved["bytes"]).hexdigest()==payload["asset"]["sha256"],"native_asset_mismatch")
        return {"id":native,"account":self.instance,"destination":self.destination,"status":mapping[value],"observer":observer,"payload_sha256":digest(payload)}
