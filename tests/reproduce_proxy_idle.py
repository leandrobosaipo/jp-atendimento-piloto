"""Loopback-only reproduction; no Docker, systemd manager, root or pilot data."""
import http.server
import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"healthy")

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def probe(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status
    except (OSError, urllib.error.URLError):
        return None


def launch(front, backend):
    # systemd-socket-activate only passes our own loopback listener to the proxy.
    return subprocess.Popen([
        "/usr/bin/systemd-socket-activate", "-l", f"127.0.0.1:{front}",
        "/usr/lib/systemd/systemd-socket-proxyd", "--exit-idle-time=10s",
        f"127.0.0.1:{backend}",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    old = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    old_port = old.server_port
    threading.Thread(target=old.serve_forever, daemon=True).start()
    front = free_port()
    proc = launch(front, old_port)
    replacement = None
    result = {}
    try:
        time.sleep(0.1)
        assert probe(front) == 200
        result["initial_forwarded_health"] = 200
        replacement = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=replacement.serve_forever, daemon=True).start()
        assert replacement.server_port != old_port
        old.shutdown()
        old.server_close()
        assert probe(replacement.server_port) == 200
        result["replacement_direct_health"] = 200
        errors = 0
        for _ in range(5):
            assert probe(front) is None
            errors += 1
            time.sleep(3)
        assert proc.poll() is None
        result["failed_probes_every_3_seconds"] = errors
        result["stale_proxy_still_running_after_15_seconds"] = True
        proc.wait(timeout=15)
        result["proxy_exited_only_after_probes_stopped"] = proc.returncode == 0
        proc = launch(front, replacement.server_port)
        time.sleep(0.1)
        assert probe(front) == 200
        result["forwarded_health_after_reresolving_endpoint"] = 200
        print(json.dumps(result, indent=2))
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        if replacement:
            replacement.shutdown()
            replacement.server_close()


if __name__ == "__main__":
    main()
