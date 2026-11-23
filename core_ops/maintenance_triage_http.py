"""Socket-activated triage on the Dell's existing n8n monitoring workflow."""
import hmac
import http.server
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import tempfile

from maintenance_triage import ROOT,run
from return_review import review_one

WORKFLOW="C4jene5zY5Z2jJcC"
CLOCK="7Ur9XdO2FiFEnMhY"
GRANTS=Path('/var/lib/jp-migracao-revisao/core-operacional/ops-config/read-grants/fixed-return')

def record_ticket(value):
    fd,tmp=tempfile.mkstemp(dir=GRANTS)
    try:
        with os.fdopen(fd,'w') as f:
            if os.fstat(f.fileno()).st_gid!=GRANTS.stat().st_gid:
                raise PermissionError('private_reader_group_not_inherited')
            os.fchmod(f.fileno(),0o440)
            json.dump(value,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,GRANTS/'latest.json')
    finally:
        Path(tmp).unlink(missing_ok=True)

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_POST(self):
        token=(Path(os.environ["CREDENTIALS_DIRECTORY"])/"token").read_text().strip()
        execution=self.headers.get("X-JP-Execution","")
        workflow=self.headers.get("X-JP-Workflow")
        clock_review=self.path=="/triagem/convite" and workflow==CLOCK
        valid=((clock_review or self.path in ("/triagem","/triagem/convite") and workflow==WORKFLOW) and
               execution.isascii() and execution.isdecimal() and len(execution)<=20 and
               hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token))
        status,body=403,{"ok":False}
        if valid:
            try:
                if self.path=="/triagem":
                    body=run(execution)
                else:
                    body=review_one(ROOT,execution,self.headers.get("X-JP-Approval-Key",""),private_ticket=clock_review)
                    ticket=body.pop('ticket',None)
                    if clock_review:
                        if ticket:
                            from fixed_return_inbox import live_inbox_status
                            roster=json.loads((GRANTS.parents[1]/'fixed-return/identities.json').read_bytes())
                            body['status']=live_inbox_status(ticket['number'],roster['pairs'],roster['since'])
                            if body['status']!='matched':ticket=None
                        record_ticket(ticket or dict(status=body['status'],key=body['approval_key'],execution=execution))
                status=200
            except Exception as error:
                print("maintenance triage held",type(error).__name__,flush=True)
                status,body=503,{"ok":False,"status":"triage_held"}
        raw=json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

if __name__=="__main__":
    if os.environ.get("LISTEN_PID")!=str(os.getpid()) or os.environ.get("LISTEN_FDS")!="1":
        raise ValueError("socket_activation_required")
    server=http.server.HTTPServer.__new__(http.server.HTTPServer)
    socketserver.BaseServer.__init__(server,("172.18.0.1",18963),Handler)
    server.socket=socket.socket(fileno=3)
    while select.select([server.socket],[],[],8)[0]:
        server.handle_request()
