"""One authenticated n8n request per tick; the worker has no outbound sockets."""
import hmac
import http.server
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import sqlite3
import sys
import time

import reply_capture as capture
import capture_link

sys.path.insert(0, "/opt/jp-dispatch/current")
from dispatch_service import read_record

WORKFLOW = "Bfk4YqHeYvQWpqiD"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.connection.settimeout(8)
        expected = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "token").read_text().strip()
        execution = self.headers.get("X-JP-Execution", "")
        if (self.path != "/tick" or not execution.isascii() or not execution.isdecimal()
                or len(execution) > 20 or self.headers.get("X-JP-Workflow") != WORKFLOW
                or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + expected)):
            return self.respond(403, {"status": "denied"})
        try:
            result = self.consume(execution)
        except Exception as error:
            print("capture held", type(error).__name__, file=sys.stderr, flush=True)
            return self.respond(503, {"status": "capture_held"})
        self.respond(200, result)

    def consume(self, execution):
        source = capture.ROOT / "data/index/receive.sqlite3"
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as queue, capture.connect() as db:
            result = capture.tick(queue, db, lambda rid: read_record(capture.ROOT, rid, 65534))
            if capture.DB == Path("/var/lib/jp-reply-capture/replies.sqlite3"):
                result["linked"] = capture_link.deliver(queue, db, lambda rid: read_record(capture.ROOT, rid, 65534), execution)
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS ticks(execution TEXT PRIMARY KEY, at INTEGER, summary TEXT)")
                db.execute("INSERT OR IGNORE INTO ticks VALUES(?,?,?)", (execution, int(time.time()), json.dumps(result)))
                db.execute("DELETE FROM ticks WHERE rowid < (SELECT max(rowid)-1440 FROM ticks)")
        return result


if __name__ == "__main__":
    assert os.environ.get("LISTEN_PID") == str(os.getpid()) and os.environ.get("LISTEN_FDS") == "1"
    server = http.server.HTTPServer.__new__(http.server.HTTPServer)
    socketserver.BaseServer.__init__(server, ("172.18.0.1", 18942), Handler)
    server.socket = socket.socket(fileno=3)
    while select.select([server.socket], [], [], 8)[0]:
        server.handle_request()
