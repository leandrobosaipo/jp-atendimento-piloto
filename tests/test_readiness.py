import contextlib
import io
import http.server
import json
import threading
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

import diagnose_readiness
import install
import verify


class RestartTests(unittest.TestCase):
    def test_refresh_preserves_socket_units_and_only_targets_requested_service(self):
        with patch.object(install.os, "geteuid", return_value=0), patch.object(install, "run") as run:
            install.refresh_forwarders(("chat",))
        run.assert_called_once_with("systemctl", "stop", "atendimento-piloto@13300.service")

    def test_non_root_uses_existing_sudo_path(self):
        with patch.object(install.os, "geteuid", return_value=1000), patch.object(install, "run") as run:
            install.refresh_forwarders(("flow", "clock"))
        run.assert_called_once_with("sudo", "-n", "systemctl", "stop", "atendimento-piloto@17860.service", "atendimento-piloto@15678.service")

    def test_invalid_service_is_rejected_before_mutation(self):
        with patch.object(install, "run") as run:
            with self.assertRaises(KeyError):
                install.refresh_forwarders(("chat", "unknown"))
        run.assert_not_called()

    def test_refresh_failure_is_not_hidden(self):
        with patch.object(install, "run", side_effect=RuntimeError("stop failed")):
            with self.assertRaisesRegex(RuntimeError, "stop failed"):
                install.refresh_forwarders(("chat",))

    def test_restart_then_refresh_then_probe(self):
        calls = []
        with patch.object(verify, "dc", side_effect=lambda *args: calls.append(args)), patch.object(verify, "refresh_forwarders", side_effect=lambda services: calls.append(("refresh", *services))), patch.object(verify, "wait_http", side_effect=lambda service: calls.append(("probe", service))):
            verify.restart_and_wait()
        self.assertEqual(calls, [("restart", "chat", "worker", "clock", "flow"), ("refresh", "chat", "flow", "clock"), ("probe", "chat"), ("probe", "flow"), ("probe", "clock")])

    def test_failed_restart_never_refreshes_or_probes(self):
        with patch.object(verify, "dc", side_effect=RuntimeError("restart failed")), patch.object(verify, "refresh_forwarders") as refresh, patch.object(verify, "wait_http") as wait:
            with self.assertRaises(RuntimeError):
                verify.restart_and_wait()
        refresh.assert_not_called()
        wait.assert_not_called()


class ReadinessTests(unittest.TestCase):
    def test_success_returns_without_mutation(self):
        response = Mock()
        response.__enter__ = Mock(return_value=SimpleNamespace(status=200))
        response.__exit__ = Mock(return_value=False)
        with patch.object(install.urllib.request, "urlopen", return_value=response), patch.object(install, "run") as run:
            install.wait_http("chat")
        run.assert_not_called()

    def test_timeout_reports_type_not_sensitive_message(self):
        error = urllib.error.URLError(ConnectionRefusedError("secret-value"))
        with patch.object(install.time, "monotonic", side_effect=[0, 0, 2]), patch.object(install.time, "sleep"), patch.object(install.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "chat readiness timeout after 1 probes; last result: ConnectionRefusedError") as caught:
                install.wait_http("chat", timeout=1)
        self.assertNotIn("secret-value", str(caught.exception))

    def test_http_failure_reports_only_status(self):
        error = urllib.error.HTTPError("http://user:secret@127.0.0.1/health", 503, "secret-body", {}, None)
        with patch.object(install.time, "monotonic", side_effect=[0, 0, 2]), patch.object(install.time, "sleep"), patch.object(install.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503") as caught:
                install.wait_http("chat", timeout=1)
        self.assertNotIn("secret", str(caught.exception))


class DiagnosticsTests(unittest.TestCase):
    def test_probe_reports_redirect_without_following_it(self):
        followed = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    self.send_response(302)
                    self.send_header("Location", "/unexpected")
                else:
                    followed.append(self.path)
                    self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(diagnose_readiness.probe("127.0.0.1", server.server_port, "/health"), "HTTP 302")
            self.assertEqual(followed, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_readonly_summary_distinguishes_direct_health_and_filters_network_details(self):
        state = {"service": "chat", "status": "running", "oom_killed": False, "exit_code": 0, "restarts": 0, "networks": {"atendimento-piloto_pilot": {"IPAddress": "172.18.0.3", "Aliases": ["do-not-print"]}}}
        output = io.StringIO()
        with patch.object(diagnose_readiness, "command", side_effect=["container-id", json.dumps(state), "ActiveState=active\nSubState=running"]) as command, patch.object(diagnose_readiness, "probe", side_effect=["ConnectionRefusedError", "HTTP 200"]), contextlib.redirect_stdout(output):
            diagnose_readiness.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["loopback_probe"], "ConnectionRefusedError")
        self.assertEqual(result["container_probe"], "HTTP 200")
        self.assertNotIn("do-not-print", output.getvalue())
        self.assertNotIn("networks", result)
        self.assertEqual([call.args[1] for call in command.call_args_list], ["ps", "inspect", "show"])

    def test_docker_unavailable_does_not_mutate_state(self):
        output = io.StringIO()
        with patch.object(diagnose_readiness, "command", return_value=None) as command, contextlib.redirect_stdout(output):
            diagnose_readiness.main()
        self.assertEqual(command.call_count, 1)
        self.assertEqual(json.loads(output.getvalue()), {"diagnostics": "Docker state unavailable"})

    def test_probe_does_not_return_raw_error(self):
        error = urllib.error.URLError(ConnectionRefusedError("credential"))
        with patch.object(diagnose_readiness.OPENER, "open", side_effect=error):
            self.assertEqual(diagnose_readiness.probe("127.0.0.1", 13300, "/health"), "ConnectionRefusedError")


if __name__ == "__main__":
    unittest.main()
