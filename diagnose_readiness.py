"""Read-only, allowlisted failure diagnostics. Never print env, logs or bodies."""
import ipaddress
import json
import subprocess
import urllib.error
import urllib.request
from install import PORTS, ROOT

INTERNAL_PORTS = {"chat": 3000, "flow": 7860, "clock": 5678}
SERVICES = {"postgres", "redis", "chat", "worker", "flow", "clock"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def command(*args):
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def probe(host, port, path):
    try:
        with OPENER.open(f"http://{host}:{port}{path}", timeout=3) as response:
            return f"HTTP {response.status}"
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code}"
    except OSError as error:
        return type(getattr(error, "reason", error)).__name__


def main():
    ids = command("docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=atendimento-piloto")
    if ids is None:
        print(json.dumps({"diagnostics": "Docker state unavailable"}))
        return
    # Query only the listed fields, never Config.Env or full inspect output.
    template = '{"service":{{json (index .Config.Labels "com.docker.compose.service")}},"status":{{json .State.Status}},"oom_killed":{{json .State.OOMKilled}},"exit_code":{{json .State.ExitCode}},"restarts":{{json .RestartCount}},"networks":{{json .NetworkSettings.Networks}}}'
    for container_id in ids.splitlines():
        raw = command("docker", "inspect", "--format", template, container_id)
        try:
            state = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            print(json.dumps({"diagnostics": "Container state unavailable"}))
            continue
        service = state.get("service")
        if service not in SERVICES:
            continue
        report = {key: state[key] for key in ("service", "status", "oom_killed", "exit_code", "restarts")}
        if service in PORTS:
            external_port, path = PORTS[service]
            report["loopback_probe"] = probe("127.0.0.1", external_port, path)
            address = state.get("networks", {}).get("atendimento-piloto_pilot", {}).get("IPAddress", "")
            try:
                ip = ipaddress.IPv4Address(address)
                if ip.is_private and not ip.is_loopback and not ip.is_unspecified:
                    report["container_probe"] = probe(str(ip), INTERNAL_PORTS[service], path)
                else:
                    report["container_probe"] = "No valid private endpoint"
            except ipaddress.AddressValueError:
                report["container_probe"] = "No valid private endpoint"
            unit = f"atendimento-piloto@{external_port}.service"
            properties = command("systemctl", "show", unit, "--property=ActiveState,SubState,Result,ExecMainStatus")
            if properties is not None:
                report["forwarder"] = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
