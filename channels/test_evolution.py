"""End-to-end adapter rehearsal through the fixture boundary, never over a real channel."""
import base64
import concurrent.futures
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from bridge import Event, Held, Outbox
from evolution import Contract, Fence

GROUP="100000000@g.us"
MEMBER="100000001@lid"
INSTANCE="fixture"
CASE="JP-A0B0C0D0"
NOW=1800000100
MEDIA=base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBMS0dARkVQWnNiUFVtVkVGZIhlbXd7gYKBTmCNl4x9lnN+gXz/2wBDARUXFx4aHjshITt8U0ZTfHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAARCAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/ALMAH//Z")

def command():
    return {"instance":INSTANCE,"event":"MESSAGES_UPSERT","data":{
        "key":{"id":"command01","fromMe":False,"remoteJid":GROUP,"participant":MEMBER},
        "messageTimestamp":NOW,"message":{"extendedTextMessage":{"text":"Assumo "+CASE,"contextInfo":{"stanzaId":"alert001"}}}}}

def callback(native,status):
    return {"instance":INSTANCE,"event":"messages.update","data":{"key":{
        "id":native,"remoteJid":GROUP,"fromMe":True,"participant":MEMBER},"status":status}}

class TestContract(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name).resolve()/"fixture.sqlite";self.box=Outbox(self.path)
        self.f=Fence(self.box);self.owner=self.f.rotate((None,0),"fixture-writer")
        self.posts=[];self.native={};self.lose=False
        def exchange(route,wire):
            self.posts.append((route,wire));native="outbound%02d"%len(self.posts)
            saved={"id":native,"destination":wire["number"],"instance":INSTANCE}
            if "text" in wire:saved["text"]=wire["text"]
            else:saved.update(bytes=base64.b64decode(wire["media"],validate=True),mime=wire["mimetype"],caption=wire["caption"],filename=wire["fileName"])
            self.native[native]=saved
            if self.lose:raise TimeoutError
            return 201,{"key":{"remoteJid":GROUP,"fromMe":True,"id":native},"status":"PENDING"}
        self.flags={"enabled":True,"group_notifications_enabled":True,"quietByOperator":False,"group_jid":GROUP,"members_authorized":True,"verified_at":NOW}
        self.adapter=Contract(INSTANCE,GROUP,exchange=exchange,asset_bytes=lambda _:MEDIA,read_native=self.native.get,settings=lambda:self.flags,clock=lambda:NOW)
        self.context=dict(instance=INSTANCE,group=GROUP,allowed={MEMBER},verified_members={MEMBER},
                          verified_at=NOW-30,now=NOW,authenticated=True,enabled=True,
                          alerts=[{"instance":INSTANCE,"group":GROUP,"case":CASE,"case_id":"case-1","native_id":"alert001","opened_at":NOW-60}])
    def plan(self,n="event001",art=True):
        event=self.box.receive(Event("whatsapp",INSTANCE,GROUP,n,"fictitious"))
        assets=[dict(id=str(i),group="servicos",mime="image/jpeg",filename=f"table{i}.jpg",sha256=hashlib.sha256(MEDIA).hexdigest()) for i in range(3)]
        self.box.prepare(event,["First part","Second part"],image_group="servicos" if art else None,assets=assets)
        return event
    def advance(self,event):return self.f.advance(self.owner,event,self.adapter)
    def confirm(self,event,pos,status):
        return self.f.confirm(event,pos,self.adapter,callback("outbound%02d"%pos,status),observer=MEMBER,authenticated=True)
    def test_group_command_bound_actor_case_quote_replay_and_conflict(self):
        req=command()
        self.assertEqual(self.f.record(req,**self.context)["command"]["action"],"claim")
        self.assertTrue(Fence(Outbox(self.path)).record(req,**self.context)["duplicate"])
        req["data"]["message"]["extendedTextMessage"]["text"]="Resolvido "+CASE
        with self.assertRaisesRegex(Held,"command_conflict"):self.f.record(req,**self.context)
    def test_authorization_rejects_invalid_group_actor_case_time_and_echo(self):
        for field,value in (("instance","other"),("enabled",False),("authenticated",False),("allowed",set()),("verified_members",set()),("verified_at",NOW-90000),("alerts",[]),("group","200000000@g.us")):
            with self.subTest(field=field),self.assertRaises(Held):self.f.record(command(),**{**self.context,field:value})
        for path,value in [(("key","fromMe"),True),(("key","participant"),"99999999@lid"),
                           (("message","extendedTextMessage","contextInfo","stanzaId"),"alert002"),
                           (("message","extendedTextMessage","text"),"OK JP-99999999"),
                           (("messageTimestamp",),NOW-86400)]:
            req=command();node=req["data"]
            for key in path[:-1]:node=node[key]
            node[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(Held):self.f.record(req,**self.context)
        alerts=self.context["alerts"]
        with self.assertRaisesRegex(Held,"ambiguous_case"):
            self.f.record(command(),**{**self.context,"alerts":[*alerts,{**alerts[0],"native_id":"alert002","case_id":"case-2"}]})
    def test_sequence_text_and_jpeg_after_destination_bound_delivery(self):
        event=self.plan()
        self.assertEqual(self.advance(event)["status"],"accepted_not_delivered")
        with self.assertRaises(Held):self.advance(event)
        for status in ("PENDING","SERVER_ACK",2):
            with self.assertRaises(Held):self.confirm(event,1,status)
        self.assertEqual(self.confirm(event,1,"DELIVERY_ACK"),"delivered")
        self.assertEqual(self.confirm(event,1,"READ"),"read")
        self.assertEqual(self.confirm(event,1,3),"read")
        self.advance(event);self.confirm(event,2,4);self.advance(event)
        self.assertEqual([r for r,_ in self.posts],["/message/sendText/fixture","/message/sendText/fixture","/message/sendMedia/fixture"])
        self.assertEqual(self.posts[-1][1]["mediatype"],"image")
        self.native["outbound03"]["bytes"]=b"wrong"
        with self.assertRaisesRegex(Held,"native_asset_mismatch"):self.confirm(event,3,"READ")
        self.native["outbound03"]["bytes"]=MEDIA;self.confirm(event,3,"DELIVERY_ACK")
        self.assertEqual(self.advance(event)["status"],"done");self.assertEqual(self.advance(event)["status"],"done")
        self.assertEqual(len(self.posts),3)
    def test_unbound_compact_receipt_and_absent_webhook_never_unlock(self):
        event=self.plan();self.advance(event)
        for field,value in (("instance","other"),("group","200000000@g.us"),("observer","100000002@lid"),("id","different01"),("compact",True),("echo",False)):
            req=callback("outbound01","READ");data=req["data"]
            if field=="compact":req["data"]={"keyId":"outbound01","status":"READ"}
            elif field=="instance":req["instance"]=value
            else:data["key"][dict(group="remoteJid",observer="participant",id="id",echo="fromMe")[field]]=value
            with self.assertRaises(Held):self.f.confirm(event,1,self.adapter,req,observer=MEMBER,authenticated=True)
        with self.assertRaises(Held):self.f.confirm(event,1,self.adapter,callback("outbound01","READ"),observer=MEMBER,authenticated=False)
        self.assertEqual(self.box.parts(event)[0]["state"],"accepted")
        with self.assertRaises(Held):self.advance(event)
        self.assertEqual(len(self.posts),1)
    def test_middle_ack_loss_freezes_outbox_and_transfer_after_restart(self):
        event=self.plan();self.advance(event);self.confirm(event,1,"READ");self.lose=True
        with self.assertRaisesRegex(Held,"send_outcome_unknown"):self.advance(event)
        self.f=Fence(Outbox(self.path))
        with self.assertRaisesRegex(Held,"no_resend"):self.advance(event)
        with self.assertRaisesRegex(Held,"prior_event_unresolved"):self.advance(self.plan("event002"))
        self.assertEqual([x["state"] for x in self.box.parts(event)],["delivered","sending","pending"])
        inactive=self.f.rotate(self.owner,None)
        with self.assertRaisesRegex(Held,"unreconciled_parts"):self.f.rotate(inactive,"other")
        self.assertEqual(len(self.posts),2)
    def test_disabled_or_muted_group_reserves_no_send(self):
        event=self.plan()
        for flags in ({},{"enabled":False,"group_notifications_enabled":False,"quietByOperator":True},
                      {"enabled":True,"group_notifications_enabled":True,"quietByOperator":True},
                      {"enabled":True,"group_notifications_enabled":False,"quietByOperator":False}):
            self.flags=flags
            with self.assertRaisesRegex(Held,"group_quiet"):self.advance(event)
        self.assertFalse(self.posts);self.assertTrue(all(p["state"]=="pending" for p in self.box.parts(event)))
        self.flags={"enabled":True,"group_notifications_enabled":True,"quietByOperator":False}
        with self.assertRaisesRegex(Held,"group_unverified"):self.advance(event)
        self.assertFalse(self.posts)
    def test_fence_serializes_freeze_with_inflight_exchange(self):
        event=self.plan();entered=threading.Event();finish=threading.Event()
        real=self.adapter.exchange
        def slow(*a):
            entered.set();self.assertTrue(finish.wait(5));return real(*a)
        self.adapter.exchange=slow
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            sending=pool.submit(self.advance,event);self.assertTrue(entered.wait(5))
            gate=pool.submit(self.f.rotate,self.owner,None)
            with self.assertRaises(concurrent.futures.TimeoutError):gate.result(.1)
            finish.set();sending.result();off=gate.result()
        with self.assertRaisesRegex(Held,"stale_owner"):self.advance(event)
        self.confirm(event,1,"READ")
        new=self.f.rotate(off,"new-owner")
        self.assertEqual(self.f.advance(new,event,self.adapter)["position"],2)
        self.assertEqual(len(self.posts),2)

if __name__=="__main__":
    if os.getenv("REQUIRE_NETWORK_ISOLATION")=="1":
        import errno,socket
        assert {name for _,name in socket.if_nameindex()}=={"lo"}
        routes=[line.split() for line in Path("/proc/net/route").read_text().splitlines() if line.strip()]
        assert all(row[0] in ("Iface","lo") for row in routes)
        # UDP connect performs only a route lookup; it sends no datagram.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            assert probe.connect_ex(("192.0.2.1",9)) in (errno.ENETUNREACH,errno.EHOSTUNREACH)
    unittest.main(verbosity=2)
