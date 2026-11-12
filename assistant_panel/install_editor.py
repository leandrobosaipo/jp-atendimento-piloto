#!/usr/bin/python3
"""Install the authenticated Cockpit rule editor on the known Dell pilot only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from install import BIN, ETC, ROOT, UNIT, run

WEB = Path("/usr/local/share/cockpit/atendimentoteste")
RECEIPTS = ROOT / "private/assistant-install"
SYSTEM_UNIT = UNIT / "atendimento-piloto-ai.service"
CONFIG = ETC / "operator-instructions.json"
SOURCE = Path(__file__).resolve().parent
FILES = {
    "gateway.py": BIN / "gateway.py",
    "knowledge_editor.py": BIN / "knowledge_editor.py",
    "cockpit/editor.html": WEB / "editor.html",
    "cockpit/editor.js": WEB / "editor.js",
    "cockpit/manifest.json": WEB / "manifest.json",
    "atendimento-piloto-ai.service": SYSTEM_UNIT,
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def demand(condition, message):
    if not condition:
        raise SystemExit(message)


def safe(path, missing=False):
    if missing and not path.exists():
        return None
    demand(path.exists() and not path.is_symlink() and path.is_file(), "Unsafe installed file: " + str(path))
    return path.read_bytes()


def put(path, value, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        staged = Path(handle.name)
    staged.chmod(mode)
    os.replace(staged, path)


def info():
    result = subprocess.run(["runuser", "-u", "delladmin", "--", str(BIN / "client.py")], input='{"op":"info"}',
                            capture_output=True, text=True, timeout=30)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit("Gateway readback unavailable") from None
    demand(result.returncode == 0 and value.get("status") == "ok", "Gateway readback unavailable")
    return value


def install(args):
    demand(os.geteuid() == 0 and re.fullmatch(r"[0-9a-f]{40}", args.source_commit) and re.fullmatch(r"[0-9a-f]{40}", args.from_commit),
           "Expected exact revisions")
    demand(run("git", "rev-parse", "HEAD") == args.source_commit and not run("git", "status", "--porcelain"),
           "Expected clean source at exact revision")
    demand(run("hostname") == "agentes-lab" and run("systemd-detect-virt") == "microsoft" and
           Path("/etc/machine-id").read_text().strip() == args.machine_id, "Guest identity mismatch")
    receipts = Path(args.receipt_dir) if args.receipt_dir else RECEIPTS
    demand(receipts == Path("/opt/atendimento-piloto/source/private/assistant-install"), "Unexpected receipt destination")
    installed = json.loads((receipts / "installed-current.json").read_text())
    demand(installed.get("source") == args.from_commit, "Unexpected deployed source")
    before = info()
    accepted = {}
    for item in args.accept_drift:
        name, separator, value = item.partition(":")
        demand(separator and name in FILES and re.fullmatch(r"[0-9a-f]{64}", value) and name not in accepted,
               "Invalid effective installed hash")
        accepted[name] = value
    old, new = {}, {}
    for name, path in FILES.items():
        candidate = (SOURCE / name).read_bytes()
        new[name] = candidate
        prior = subprocess.check_output(["git", "-c", "safe.directory=" + str(ROOT), "show", args.from_commit + ":assistant_panel/" + name], cwd=ROOT) \
            if name not in ("knowledge_editor.py", "cockpit/editor.html", "cockpit/editor.js") else None
        actual = safe(path, missing=prior is None)
        demand(actual == prior or (actual is not None and accepted.get(name) == digest(actual)), "Installed release drift: " + name)
        old[name] = actual
    default = (SOURCE / "default_operator_instructions.json").read_bytes()
    config_before = safe(CONFIG, missing=True)
    if config_before is not None:
        from gateway import validate_operator_instructions
        validate_operator_instructions(json.loads(config_before))
    target = receipts / ("editor-install-" + args.source_commit[:12] + "-" + str(time.time_ns()))
    demand(not target.exists(), "Existing editor install receipt")
    if not args.apply:
        print(json.dumps({"plan": "install authenticated rule editor", "source": args.source_commit,
                          "changed": [name for name in FILES if old[name] != new[name]], "gateway": before["kb_version"]}))
        return
    target.mkdir(mode=0o700)
    (target / "intent.json").write_text(json.dumps({"source": args.source_commit, "previous": args.from_commit,
        "before": before, "files": {name: {"old": digest(old[name]) if old[name] is not None else None, "new": digest(new[name])}
                                  for name in FILES}}))
    for name, value in old.items():
        if value is not None:
            (target / ("previous-" + name.replace("/", "_"))).write_bytes(value)
    try:
        for name, path in FILES.items():
            if old[name] != new[name]:
                put(path, new[name], 0o755 if name in ("gateway.py", "knowledge_editor.py") else 0o644)
        if config_before is None:
            put(CONFIG, default, 0o600)
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "atendimento-piloto-ai.service")
        after = info()
        demand(after["kb_version"] == before["kb_version"] and after["total_reserved_micro_usd"] >= before["total_reserved_micro_usd"],
               "Gateway readback changed")
        result = {"source": args.source_commit, "previous": args.from_commit, "changed": [name for name in FILES if old[name] != new[name]],
                  "gateway": after, "editor_config_sha256": digest(default)}
        (target / "ready.json").write_text(json.dumps(result))
        for path in target.iterdir(): path.chmod(0o600)
        put(receipts / "installed-current.json", json.dumps({"source": args.source_commit}).encode(), 0o600)
        print(json.dumps(result))
    except BaseException:
        for name, path in FILES.items():
            if old[name] is None:
                path.unlink(missing_ok=True)
            else:
                put(path, old[name], 0o755 if name in ("gateway.py", "knowledge_editor.py") else 0o644)
        if config_before is None:
            CONFIG.unlink(missing_ok=True)
        else:
            put(CONFIG, config_before, 0o600)
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "atendimento-piloto-ai.service")
        (target / "rollback.json").write_text(json.dumps({"status": "restored"}))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--from-commit", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--receipt-dir")
    parser.add_argument("--accept-drift", action="append", default=[], help="name:sha256 from the authenticated preflight")
    parser.add_argument("--apply", action="store_true")
    install(parser.parse_args())
