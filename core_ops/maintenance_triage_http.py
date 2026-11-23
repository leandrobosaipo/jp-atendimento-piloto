"""Socket-activated triage on the Dell's existing n8n monitoring workflow."""
import hmac
import http.server
import json
import os
from pathlib import Path
import select
import socket
import socketserver

from maintenance_triage import ROOT,run
from return_review import review_one

WORKFLOW="C4jene5zY5Z2jJcC"

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_POST(self):
        token=(Path(os.environ["CREDENTIALS_DIRECTORY"])/"token").read_text().strip()
        execution=self.headers.get("X-JP-Execution","")
        valid=(self.path in ("/triagem","/triagem/convite") and self.headers.get("X-JP-Workflow")==WORKFLOW and
               execution.isascii() and execution.isdecimal() and len(execution)<=20 and
               hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token))
        status,body=403,{"ok":False}
        if valid:
            try:
                if self.path=="/triagem":
                    body=run(execution)
                else:
                    body=review_one(ROOT,execution,self.headers.get("X-JP-Approval-Key",""))
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
