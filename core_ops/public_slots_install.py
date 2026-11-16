"""Add only a root-peer public availability read to the installed integration socket."""
from hashlib import sha256

def patch(source):
    if sha256(source.encode()).hexdigest()!='40ac225bdac6787abea61ac5143d91b680fb132a9327f63f64d94073c8155fc5':
        raise ValueError('integration_version_changed')
    anchor='    ui.serve_forever()'
    if source.count(anchor)!=1:raise ValueError('socket_handler_changed')
    return source.replace(anchor,'''    class PublicSlots(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(8)
            peer=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize("3i"))
            if struct.unpack("3i",peer)[1]!=0:return
            try:
                raw=self.rfile.readline(1025)
                need(len(raw)<=1024 and raw.endswith(b"\\n"),"invalid_request")
                msg=json.loads(raw)
                need(set(msg)=={"op","params"} and msg["op"]=="public-slots","invalid_operation")
                result=provider.query("availability",msg["params"],prov)
            except Exception:result={"status":"unavailable"}
            self.wfile.write(json.dumps(result).encode()+b"\\n")
    public_path=path.with_name("public-slots.sock")
    public_path.unlink(missing_ok=True)
    public_server=Unix(str(public_path),PublicSlots)
    public_path.chmod(0o602)  # private 0700 runtime directory, kernel-verified root peer
    threading.Thread(target=public_server.serve_forever,daemon=True).start()
'''+anchor)
