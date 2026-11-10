"""JP dispatch entry point. Unix peers and private permits are checked independently."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import struct
import urllib.request
from http.server import BaseHTTPRequestHandler
from uuid import UUID

from bridge import Held, Outbox, canonical, digest, need
from dispatch import Dispatcher
from dispatch_contract import INSTANCE
from dispatch_receipt import delivery_state, evidence, match_event, open_record, verify_echo
from personal import NoRedirect, ORIGIN

CONFIG = Path("/etc/jp-dispatch")
OPS = Path("/var/lib/jp-migracao-revisao/core-operacional/ops-state")
SOCKET = Path("/run/jp-dispatch/dispatch.sock")
LEDGER = Path("/var/lib/jp-dispatch/ledger.sqlite3")
CORE_UID = 64681
MAX_REQUEST = 25 * 1024 * 1024


def private_bytes(path, uid, *, mode=0o600, gid=None, limit=65536):
    """Walk directory FDs without following symlinks, including parent components."""
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.absolute().parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1 and
                 stat.S_IMODE(info.st_mode) == mode and (gid is None or info.st_gid == gid), "unsafe_proof_file")
            need(info.st_size<=limit, "proof_too_large")
            data = stream.read(limit+1)
            need(len(data) == info.st_size, "proof_size_changed")
            return data
    finally:
        os.close(directory)

def read_private(path, uid, **options):
    return json.loads(private_bytes(path,uid,**options))


def read_record(intake, identifier, uid):
    need(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}",identifier), "invalid_record_id")
    key = private_bytes(intake/"access/key",uid,limit=32)
    raw = private_bytes(intake/"data/records"/(identifier+".json"),uid,limit=32*1024*1024)
    return open_record(identifier,json.loads(raw),key)


def receipt_authority(config, attempt, uid):
    scope = read_private(config/"receipt-scope.json",uid)
    need(scope["instance"]==INSTANCE and re.fullmatch(r"[a-f0-9]{64}",scope["instance_hash"]), "receipt_scope")
    pair = config/"receipt-mappings"/(hashlib.sha256(attempt.encode()).hexdigest()+".json")
    try:
        mapping = read_private(pair,uid)
    except FileNotFoundError:
        mapping = None
    return scope["instance_hash"], mapping


def observe_records(dispatcher, body, *, config=CONFIG, intake=Path("/var/lib/jp-migracao-revisao/entrada-operacional"),
                    admin_uid=0, receiver_uid=65534):
    need(isinstance(body,dict) and set(body)=={"native","echo","record"} and isinstance(body["native"],str) and
         re.fullmatch(r"[A-Za-z0-9_-]{6,160}",body["native"]) and all(isinstance(body[k],str) and
         re.fullmatch(r"[a-f0-9]{64}",body[k]) for k in ("echo","record")) and body["record"]!=body["echo"], "invalid_receipt_ids")
    def verify(attempt):
        authority=receipt_authority(config,attempt["attempt"],admin_uid)
        return {**evidence(attempt,attempt["content_hash"],read_record(intake,body["echo"],receiver_uid),
                           read_record(intake,body["record"],receiver_uid),*authority),
                "echo":body["echo"],"record":body["record"]}
    return {"status":dispatcher.observe(body["native"],verify)}


def observe_event(dispatcher, body, *, config=CONFIG, intake=Path("/var/lib/jp-migracao-revisao/entrada-operacional"),
                  admin_uid=0, receiver_uid=65534):
    need(isinstance(body,dict) and set(body)=={"record"}, "invalid_event_request")
    identifier = body["record"]
    record = read_record(intake,identifier,receiver_uid)
    envelope = record[1]
    if isinstance(envelope,list):
        need(len(envelope)==1, "ambiguous_envelope")
        envelope = envelope[0]
    kind = envelope["event"].lower().replace("_",".")
    need(kind in ("messages.upsert","messages.update") and envelope["instance"]==INSTANCE, "unexpected_native_event")
    rows = envelope["data"]
    rows = rows if isinstance(rows,list) else [rows]
    need(0<len(rows)<=256 and all(isinstance(r,dict) for r in rows), "invalid_event_batch")
    result = {"matched":0,"waiting":0,"confirmed":0}
    for item in rows:
        native = item.get("keyId") or item.get("key",{}).get("id")
        if not isinstance(native,str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,160}",native):
            continue
        with dispatcher.box.db() as db:
            db.execute("BEGIN IMMEDIATE")
            attempt = db.execute("""SELECT a.*, c.content_hash FROM dispatch_attempts a
                JOIN dispatch_content c USING(attempt) WHERE native=? AND state IN ('accepted','delivered','read')""",(native,)).fetchone()
            if not attempt:
                continue
            authority = receipt_authority(config,attempt["attempt"],admin_uid)
            row = match_event(record,attempt,kind,*authority)
            if kind == "messages.upsert":
                verify_echo(row,attempt["content_hash"])
                observation = "echo"
            else:
                if (row.get("status") or row.get("update",{}).get("status")) in ("PENDING","SERVER_ACK"):
                    result["matched"] += 1
                    result["waiting"] += 1
                    continue
                observation = delivery_state(row)
            result["matched"] += 1
            db.execute("INSERT OR IGNORE INTO dispatch_observations VALUES(?,?,?)",(attempt["attempt"],observation,identifier))
            seen = dict(db.execute("SELECT kind,record FROM dispatch_observations WHERE attempt=?",(attempt["attempt"],)))
        ack = seen.get("read") or seen.get("delivered")
        if "echo" in seen and ack:
            observe_records(dispatcher,{"native":native,"echo":seen["echo"],"record":ack},
                            config=config,intake=intake,admin_uid=admin_uid,receiver_uid=receiver_uid)
            result["confirmed"] += 1
        else:
            result["waiting"] += 1
    return result

class Policy:
    def __init__(self, config=CONFIG, ops=OPS, admin_uid=0, core_uid=CORE_UID, core_gid=CORE_UID):
        self.config = config
        self.ops = ops
        self.admin_uid = admin_uid
        self.core_uid = core_uid
        self.core_gid = core_gid

    def policy(self):
        return read_private(self.config / "policy.json", self.admin_uid)

    def roles(self, uid):
        policy = self.policy()
        need(uid == self.core_uid and policy["mode"] == "owned", "peer_or_sender_disabled")
        return {(p["workflow"], p["node"], p["operation"]) for p in policy["roles"]}

    def authorize(self, body, epoch, now):
        policy = self.policy()
        ownership = read_private(self.ops / "ownership.json", self.admin_uid, mode=0o440, gid=self.core_gid)
        expiry = dt.datetime.fromisoformat(ownership["until"])
        need(ownership["mode"] == policy["mode"] == "owned" and ownership["owner"] == "central-dell" and
             str(UUID(epoch)) == epoch == ownership["epoch"] == policy["epoch"] and
             expiry.utcoffset() == dt.timedelta(0), "sender_fenced")
        matches = [r for r in policy["roles"] if all(r[k] == body[k] for k in ("workflow","node","operation"))]
        need(len(matches) == 1 and matches[0]["role"] in ownership["roles"], "unbound_role")
        name = hashlib.sha256(body["attempt_id"].encode()).hexdigest() + ".json"
        proof = read_private(self.ops / "dispatch-proofs" / name, self.core_uid)
        need(all(proof[k] == body[k] for k in ("workflow","node","instance","operation","source_event_id","attempt_id","execution"))
             and proof["epoch"] == epoch and type(proof["until"]) is int and now < proof["until"] <= int(expiry.timestamp())
             and proof["payload_sha256"] == digest(body["payload"]), "stale_or_changed_dispatch_proof")
        caps = {}
        for period in ("minute","hour","day"):
            cap, reserve = policy["limits"][period], policy["priority_reserve"][period]
            need(type(cap) is int and type(reserve) is int and 0 <= reserve <= cap and cap > 0, "invalid_budget")
            need(type(matches[0]["commercial"]) is bool, "invalid_budget_role")
            caps[period] = cap - (reserve if matches[0]["commercial"] else 0)
        return {**proof, "limits":caps, "approved_asset_hashes":policy["approved_asset_hashes"],
                "budget_marker":policy["budget_marker"]}


class Evolution:
    def __init__(self, config=CONFIG):
        self.config = config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def post(self, path, payload):
        access = read_private(self.config / "evolution.json", 0)
        need(access["origin"] == ORIGIN and access["instance"] == INSTANCE, "wrong_provider")
        request = urllib.request.Request(ORIGIN + path, method="POST", data=canonical(payload).encode(),
                                         headers={"Content-Type":"application/json", "apikey":access["token"],
                                                  "CF-Access-Client-Id":access["client_id"],
                                                  "CF-Access-Client-Secret":access["client_secret"]})
        with self.opener.open(request, timeout=20) as result:
            data = result.read(524289)
            need(len(data) <= 524288, "provider_reply_too_large")
            return json.loads(data)

    def verify(self, number, jid):
        need(jid==number+"@s.whatsapp.net","unverified_native_identity")
        rows = self.post("/chat/whatsappNumbers/" + INSTANCE, {"numbers":[number]})
        need(isinstance(rows, list) and len(rows) == 1 and rows[0].get("exists") is True and
             str(rows[0].get("number")) == number and isinstance(rows[0].get("jid"),str), "unverified_native_identity")
        return rows[0]["jid"]

    def __call__(self, path, payload):
        need(path in ("/message/sendText/" + INSTANCE, "/message/sendMedia/" + INSTANCE), "invalid_native_path")
        return self.post(path, payload)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            need(self.path in ("/deliver","/receipt","/receipt-event"), "unknown_path")
            peer = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            roles = self.server.policy.roles(struct.unpack("3i", peer)[1])
            lengths = self.headers.get_all("Content-Length", [])
            need(len(lengths) == 1 and re.fullmatch(r"[1-9][0-9]{0,7}", lengths[0]) and
                 not self.headers.get_all("Transfer-Encoding") and self.headers.get_content_type() == "application/json",
                 "invalid_request")
            length = int(lengths[0])
            need(length <= (MAX_REQUEST if self.path=="/deliver" else 1024), "request_too_large")
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            need(len(raw) == length, "truncated_request")
            if self.path=="/receipt-event":
                status,result=200,observe_event(self.server.dispatcher,json.loads(raw))
            elif self.path=="/receipt":
                status,result=200,observe_records(self.server.dispatcher,json.loads(raw))
            else:
                status, result = self.server.dispatcher.deliver(json.loads(raw), roles)
        except (Held, ValueError, KeyError, OSError, TypeError, AttributeError):
            status, result = 409, {"status":"held"}
        self.send_response(status)
        data = canonical(result).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(socketserver.UnixStreamServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


def clear_stale_socket(path, uid, gid):
    # The directory is also a bind mount in the core. Retain its inode on restart.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    need(stat.S_ISSOCK(info.st_mode) and info.st_uid==uid and info.st_gid==gid and
         stat.S_IMODE(info.st_mode)==0o660, "unexpected_socket")
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        try:
            probe.connect(str(path))
        except ConnectionRefusedError:
            current=path.lstat()
            need((current.st_dev,current.st_ino)==(info.st_dev,info.st_ino),"socket_changed")
            path.unlink()
            return
        raise RuntimeError("dispatch_socket_busy")


def main():
    os.umask(0o077)
    native, policy = Evolution(), Policy()
    clear_stale_socket(SOCKET, 0, CORE_UID)
    with Server(str(SOCKET), Handler) as server:
        os.chown(SOCKET, 0, CORE_UID)
        os.chmod(SOCKET, 0o660)
        server.policy = policy
        server.dispatcher = Dispatcher(Outbox(LEDGER), policy.authorize, native, verify_number=native.verify)
        server.serve_forever()


if __name__ == "__main__":
    main()
