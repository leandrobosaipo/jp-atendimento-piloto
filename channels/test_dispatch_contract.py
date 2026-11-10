import unittest
from bridge import Held
from dispatch_contract import INSTANCE, accepted_response, validate_request


class DispatchContract(unittest.TestCase):
    def test_bound_role_operation_address_and_receipt(self):
        number = "10000000000"
        body = dict(instance=INSTANCE, operation="sendText", native_path="/message/sendText/"+INSTANCE,
                    workflow="fixture", node="node-1", execution="91", source_event_id="visit/001",
                    attempt_id="first:001", payload={"number":number,"text":"Teste","linkPreview":False})
        roles = {("fixture","node-1","sendText")}
        self.assertEqual(validate_request(body,roles=roles),body)
        for changes in ({"node":"other"}, {"execution":"untrusted"}, {"attempt_id":None},
                        {"native_path":"https://example.invalid"}, {"payload":{**body["payload"],"number":number+"@g.us"}},
                        {"payload":{**body["payload"],"url":"https://example.invalid"}}):
            with self.assertRaises(Held):validate_request({**body,**changes},roles=roles)
        jid=number+"@s.whatsapp.net"
        key={"id":"fixture-001","fromMe":True,"remoteJid":jid}
        self.assertEqual(accepted_response({"key":key},jid),{"status":"provider_accepted","key":key})
        for change in ({"remoteJid":"other@lid"},{"fromMe":False},{"id":""}):
            with self.assertRaises(Held):accepted_response({"key":{**key,**change}},jid)


if __name__=="__main__":unittest.main()
