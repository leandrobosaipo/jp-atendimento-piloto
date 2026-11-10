import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from uuid import uuid4

from bridge import Held, Outbox, digest
from dispatch import Dispatcher
from dispatch_contract import INSTANCE
from dispatch_service import Handler, Policy, Server, clear_stale_socket


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve()
        self.now = 1800000000
        self.epoch = str(uuid4())
        self.jid = "10000000000@s.whatsapp.net"
        self.fixture = b"synthetic-media"
        self.roles = {("wf","reply","sendText"), ("wf","attachment","sendMedia")}
        self.marker = digest([self.now, []])
        self.calls = []
        self.box = Outbox(self.path / "state.db")
        self.sender = self.service()
        self.sender.import_baseline(self.marker, self.now, [])
        self.sender.fence.rotate((None,0), self.epoch)

    def service(self):
        return Dispatcher(self.box, self.grant, self.exchange,
                          verify_number=lambda n,j:self.assertEqual(n + "@s.whatsapp.net",j), clock=lambda:self.now)

    def body(self, media=False):
        op = "sendMedia" if media else "sendText"
        payload = {"number":"10000000000"}
        if media:
            payload.update(media=base64.b64encode(self.fixture).decode(), mediatype="image",
                           mimetype="image/jpeg", fileName="fixture.jpg")
        else:
            payload["text"] = "Fixture"
        return dict(operation=op, instance=INSTANCE, node="attachment" if media else "reply",
                    workflow="wf", execution="41", native_path="/message/"+op+"/"+INSTANCE,
                    source_event_id="source/1", attempt_id="part/2" if media else "part/1", payload=payload)

    def grant(self, body, epoch, now):
        return dict(epoch=epoch, until=self.now+60, destination_jid=self.jid, payload_sha256=digest(body["payload"]),
                    approved_asset_hashes=[hashlib.sha256(self.fixture).hexdigest()], budget_marker=self.marker,
                    limits={"minute":6,"hour":60,"day":500}, prior_attempt_id="part/1" if body["operation"]=="sendMedia" else None)

    def exchange(self, path, payload):
        self.calls.append(path)
        return {"key":{"id":"native-"+str(len(self.calls)), "fromMe":True, "remoteJid":self.jid}}

    def receipt(self, row, state):
        return dict(native=row["native"], instance=INSTANCE, fromMe=True, destination=self.jid,
                    payload_sha256=row["body_hash"], state=state)

    def test_concurrent_workers_restart_and_ambiguous_transport(self):
        other = self.service()
        barrier = threading.Barrier(2)
        def submit(pair):
            worker, execution = pair
            barrier.wait(5)
            return worker.deliver({**self.body(),"execution":execution}, self.roles)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first, second = pool.map(submit, [(self.sender,"41"),(other,"42")])
        self.assertEqual(first,second)
        self.assertEqual(first[0],201)
        self.assertEqual(len(self.calls),1)
        self.sender.observe("native-1",lambda r:self.receipt(r,"delivered"))
        with self.assertRaises(Held):
            other.deliver({**self.body(), "attempt_id":"renamed"},self.roles)
        def lost_response(*args):
            self.calls.append("lost")
            raise TimeoutError()
        self.sender.transport = lost_response
        self.assertEqual(self.sender.deliver(self.body(True),self.roles), (409,{"status":"delivery_unknown"}))
        self.assertEqual(self.service().deliver(self.body(True),self.roles), (409,{"status":"delivery_unknown"}))
        self.assertEqual(len(self.calls),2)

    def test_only_bound_receipt_releases_approved_media(self):
        with self.assertRaises(Held):
            self.sender.deliver(self.body(True), self.roles)
        self.sender.deliver(self.body(), self.roles)
        for patch in ({"destination":"123456789@lid"}, {"fromMe":False}, {"payload_sha256":"changed"}, {"state":"accepted"}):
            with self.assertRaises(Held):
                self.sender.observe("native-1",lambda r:{**self.receipt(r,"read"),**patch})
        with self.assertRaises(Held):
            self.sender.deliver(self.body(True), self.roles)
        self.assertEqual(self.sender.observe("native-1",lambda r:self.receipt(r,"read")), "read")
        self.assertEqual(self.sender.observe("native-1",lambda r:self.receipt(r,"delivered")), "read")
        media = self.body(True)
        changed = {**media,"payload":{**media["payload"],"media":"eHh4"}}
        with self.assertRaises(Held):
            self.sender.deliver(changed,self.roles)
        self.assertEqual(self.sender.deliver(media,self.roles)[0],201)
        self.assertEqual(len(self.calls),2)

    def test_source_budget_and_fence(self):
        self.sender.fence.rotate((self.epoch,1),None)
        self.now += 61
        with self.assertRaises(Held):
            self.sender.deliver(self.body(),self.roles)
        rows = [["reservation",self.now-55,6]]
        self.marker = digest([self.now,rows])
        self.sender.import_baseline(self.marker,self.now,rows)
        for marker,cut in [(digest([self.now-1,[]]),self.now-1),("0"*64,self.now)]:
            with self.assertRaises(Held):
                self.sender.import_baseline(marker,cut,[])
        self.sender.fence.rotate((None,2),self.epoch)
        with self.assertRaises(Held):
            self.sender.deliver(self.body(),self.roles)
        self.assertEqual(self.calls,[])
        self.now += 6
        self.assertEqual(self.sender.deliver(self.body(),self.roles)[0],201)

    def test_persist_before_call_even_on_process_death(self):
        def killed(*args):
            raise SystemExit()
        self.sender.transport = killed
        with self.assertRaises(SystemExit):
            self.sender.deliver(self.body(),self.roles)
        again = self.service()
        self.assertEqual(again.deliver(self.body(),self.roles), (409,{"status":"delivery_unknown"}))
        self.assertEqual(self.calls,[])

    def test_stable_bind_mount_on_restart(self):
        path=self.path/"dispatch.sock"
        inode=self.path.stat().st_ino
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            path.chmod(0o660)
            listener.listen(1)
            with self.assertRaises(RuntimeError):
                clear_stale_socket(path,os.getuid(),os.getgid())
        clear_stale_socket(path,os.getuid(),os.getgid())
        self.assertFalse(path.exists())
        self.assertEqual(inode,self.path.stat().st_ino)

    def test_peer_proof_role_and_expiry(self):
        uid = os.getuid()
        policy = Policy(self.path, self.path, uid, uid, os.getgid())
        body = self.body()
        def save(name,data):
            path = self.path / name
            path.write_text(json.dumps(data))
            path.chmod(0o600)
        role = dict(workflow="wf", node="reply", operation="sendText", role="reply", commercial=True)
        save("policy.json",dict(mode="owned",epoch=self.epoch,roles=[role],limits={"minute":6,"hour":60,"day":500},
                               priority_reserve={"minute":1,"hour":10,"day":50},approved_asset_hashes=[],budget_marker=self.marker))
        save("ownership.json",dict(mode="owned",epoch=self.epoch,owner="central-dell",roles=["reply"],until="2030-01-01T00:00:00+00:00"))
        (self.path/"dispatch-proofs").mkdir()
        name = "dispatch-proofs/" + hashlib.sha256(body["attempt_id"].encode()).hexdigest() + ".json"
        proof = {k:body[k] for k in ("instance","workflow","node","operation","execution","source_event_id","attempt_id")}
        proof.update(epoch=self.epoch,until=self.now+30,destination_jid=self.jid,payload_sha256=digest(body["payload"]))
        save(name,proof)
        with self.assertRaises(Held):
            policy.authorize(body,self.epoch,self.now)
        (self.path/"ownership.json").chmod(0o440)
        self.assertEqual(policy.authorize(body,self.epoch,self.now)["limits"]["minute"],5)
        with self.assertRaises(Held):
            policy.roles(uid+1)
        for patch in ({"execution":"402"},{"source_event_id":"other"}):
            with self.assertRaises(Held):
                policy.authorize({**body,**patch},self.epoch,self.now)
        with self.assertRaises(Held):
            policy.authorize(body,self.epoch,self.now+30)
        (self.path/name).chmod(0o640)
        with self.assertRaises(Held):
            policy.authorize(body,self.epoch,self.now)
        (self.path/name).chmod(0o600)
        if hasattr(socket,"SO_PEERCRED"):
            self.sender.authorize = policy.authorize
            path = str(self.path/"test.sock")
            with Server(path,Handler) as server:
                server.policy, server.dispatcher = policy,self.sender
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(5)
                    client.connect(path)
                    data = json.dumps(body).encode()
                    client.sendall(b"POST /deliver HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: " +
                                   str(len(data)).encode() + b"\r\n\r\n" + data)
                    response = client.makefile("rb").read()
                thread.join(5)
                self.assertIn(b" 201 ",response.splitlines()[0])
                self.assertIn(b"provider_accepted",response)


if __name__ == "__main__":
    unittest.main(verbosity=2)
