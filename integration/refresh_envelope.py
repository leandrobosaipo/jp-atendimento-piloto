"""Controlled credential-only refresh for the existing identified read-only pilot.

No network calls, credential discovery, provider writes, code update or AI restart.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assistant_panel.maintenance import database_audit

UNIT = "atendimento-piloto-integracao.service"
ETC = Path("/etc/atendimento-piloto-integracao")
BIN = Path("/usr/local/libexec/atendimento-piloto-integracao")
STATE = Path("/var/lib/atendimento-piloto-integracao/events.sqlite3")
ORIGIN = "https://sistema.appbarber.com.br"


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private(path):
    path = Path(path)
    need(not any(p.is_symlink() for p in (path, *path.parents)), "unsafe_private_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
             stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size <= 32000,
             "invalid_private_envelope")
        return stream.read(32001)


def build(old, candidate):
    before, fresh = json.loads(old), json.loads(candidate)
    need(isinstance(before, dict) and isinstance(fresh, dict), "invalid_envelope")
    metadata = {"source_generation", "source_verified_at", "captured_at"}
    need(set(fresh) == {"origin", "cookieHeader", *metadata}, "candidate_must_contain_session_provenance")
    need(before.get("origin") == fresh.get("origin") == ORIGIN, "unexpected_origin")
    cookie = fresh["cookieHeader"]
    need(isinstance(cookie, str) and 0 < len(cookie) <= 16000 and
         all(32 <= ord(c) < 127 for c in cookie) and "=" in cookie, "invalid_cookie")
    need(before.get("services") and before.get("professionals"), "missing_approved_catalog")
    need(before.get("cookieHeader") != cookie, "unchanged_session")
    need(type(before.get("source_generation")) is int and
         type(fresh["source_generation"]) is int and
         fresh["source_generation"] > before["source_generation"], "session_generation_not_newer")
    for name in ("source_verified_at", "captured_at"):
        def timestamp(value):
            need(isinstance(value, str), "missing_session_timestamp")
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            need(parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0), "utc_session_timestamp_required")
            return parsed
        need(timestamp(fresh[name]) >= timestamp(before.get(name)), "session_timestamp_regression")
        need(timestamp(fresh[name]) <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60), "future_session_timestamp")
    # Preserve all approved mappings/policies and every other existing field.
    after = dict(before, cookieHeader=cookie, **{name: fresh[name] for name in metadata})
    return (json.dumps(after, ensure_ascii=False, sort_keys=True) + "\n").encode()


def put(path, raw):
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if os.path.exists(name): os.unlink(name)


def transition(path, candidate, backup, *, stop, start, check, invariant):
    """Callbacks verified by the CLI; an exclusive caller lock spans all actions."""
    old = private(path)
    identity = lambda: (path.lstat().st_dev, path.lstat().st_ino)
    expected_identity = identity()
    expected_bytes = old
    new = build(old, candidate)
    need(not backup.exists(), "reconcile_prior_attempt")
    with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(old); stream.flush(); os.fsync(stream.fileno())
    descriptor = os.open(backup.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
    try:
        stop()
        invariant()
        need(private(path) == old, "envelope_drift")
        need(identity() == expected_identity, "envelope_identity_drift")
        put(path, new)
        expected_identity, expected_bytes = identity(), new
        start()
        check(sha(new))
        invariant()
        need(private(path) == new, "post_refresh_drift")
    except BaseException:
        # Do not restore databases or retry provider activity. Preserve backup.
        need(identity() == expected_identity and private(path) == expected_bytes,
             "rollback_withheld_third_party_envelope_drift")
        stop()
        need(identity() == expected_identity and private(path) == expected_bytes,
             "rollback_withheld_third_party_envelope_drift")
        put(path, old)
        start()
        check(sha(old))
        invariant()
        raise RuntimeError("refresh_failed_rollback_verified") from None
    return {"status": "local_credential_reloaded", "before_sha256": sha(old), "after_sha256": sha(new)}


def run(*args, input=None):
    if args[0] == "git":
        args = ("git", "-c", "safe.directory=" + str(ROOT), *args[1:])
    result = subprocess.run(args, input=input, capture_output=True, timeout=30)
    need(result.returncode == 0, "local_command_failed")
    return result.stdout


def execute(args):
    os.umask(0o077)
    need(os.geteuid() == 0, "root_required")
    need(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in
             (args.integration_commit, args.panel_commit)), "exact_installed_revisions_required")
    need(re.fullmatch(r"[0-9a-f]{40}", args.source_commit) and
         run("git", "-C", str(ROOT), "rev-parse", "HEAD").decode().strip() == args.source_commit and
         not run("git", "-C", str(ROOT), "status", "--porcelain").strip(), "exact_clean_source_required")
    need(run("hostname").decode().strip() == "agentes-lab" and
         run("systemd-detect-virt").decode().strip() == "microsoft" and
         Path("/etc/machine-id").read_text().strip() == args.machine_id, "guest_identity_mismatch")
    need(run("systemctl", "is-active", UNIT).strip() == b"active", "integration_must_be_active")
    unit = Path("/etc/systemd/system") / UNIT
    need(not unit.is_symlink() and unit.read_bytes() == run("git", "-C", str(ROOT), "show", args.integration_commit + ":integration/" + UNIT), "integration_unit_drift")
    need(not (unit.parent / (UNIT + ".d")).exists(), "integration_dropins_require_review")
    # Match installed code independently of the newer panel source revision.
    for name in ("service.py", "provider.py", "policy.py", "client.py"):
        path = BIN / name
        need(not path.is_symlink() and path.read_bytes() == run("git", "-C", str(ROOT), "show", args.integration_commit + ":integration/" + name), "installed_integration_drift")
    ai = Path("/usr/local/libexec/atendimento-piloto-ai/gateway.py")
    need(not ai.is_symlink() and ai.read_bytes() == run("git", "-C", str(ROOT), "show", args.panel_commit + ":assistant_panel/gateway.py"), "installed_panel_drift")
    candidate = private(args.candidate)
    path = ETC / "appbarber-readonly.json"
    old = private(path)
    need(sha(old) == args.expected_old_sha256, "old_envelope_drift")
    new = build(old, candidate)
    need(sha(new) == args.expected_new_sha256, "new_envelope_mismatch")
    # Hashes stay private; output never contains the session or source response.
    protected = {p: p.read_bytes() for p in (ETC / "assets.json", ETC / "runtime.json", ai, unit)}
    states = {u: run("systemctl", "is-active", u) for u in ("atendimento-piloto-ai.socket", "atendimento-piloto-ai.service")}
    pre = database_audit(STATE, allow_human_holds=True)
    def invariant():
        need(database_audit(STATE, allow_human_holds=True) == pre, "integration_state_changed")
        need(all(p.read_bytes() == value for p, value in protected.items()), "protected_files_changed")
        need(all(run("systemctl", "is-active", u) == value for u, value in states.items()), "panel_state_changed")
    def check(expected):
        for _ in range(30):
            credential = Path("/run/credentials") / UNIT / "appbarber"
            if credential.is_file() and sha(credential.read_bytes()) == expected:
                result = json.loads(run(str(BIN / "client.py"), input=b'{"op":"history","actor":"A"}'))
                if result.get("status") == "ok": return
            time.sleep(.2)
        need(False, "loaded_credential_or_consumer_unverified")
    if not args.apply:
        return {"status": "validated_only", "after_sha256": sha(new)}
    backup = Path(args.backup)
    need(backup.parent.is_dir() and not any(p.is_symlink() for p in (backup, *backup.parents)) and
         backup.parent.stat().st_uid == 0 and stat.S_IMODE(backup.parent.stat().st_mode) == 0o700,
         "private_backup_directory_required")
    with STATE.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        invariant()
        return transition(path, candidate, backup,
                          stop=lambda: run("systemctl", "stop", UNIT),
                          start=lambda: run("systemctl", "start", UNIT),
                          check=check, invariant=invariant)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("source-commit", "integration-commit", "panel-commit", "machine-id", "candidate", "backup", "expected-old-sha256", "expected-new-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args)))
    except BaseException:
        # Do not leak JSON parsing snippets, subprocess output or credential values.
        print('{"status":"held","reason":"refresh_or_rollback_requires_private_review"}', file=sys.stderr)
        sys.exit(1)
