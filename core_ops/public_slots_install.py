"""Add only a root-peer public availability read to the installed integration socket."""
from hashlib import sha256

def patch(source):
    if sha256(source.encode()).hexdigest()!='40ac225bdac6787abea61ac5143d91b680fb132a9327f63f64d94073c8155fc5':
        raise ValueError('integration_version_changed')
    old='elif msg.get("op")=="personal-ingress":'
    if source.count(old)!=1:raise ValueError('socket_handler_changed')
    return source.replace(old,'''elif msg.get("op")=="public-slots":
                peer=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize("3i"))
                need(struct.unpack("3i",peer)[1]==0 and set(msg)=={"op","params"},"denied")
                result=provider.query("availability",msg["params"],self.server.app.prov)
            '''+old)
