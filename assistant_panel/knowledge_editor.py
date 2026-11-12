#!/usr/bin/python3
"""Root-only Cockpit backend for deliberate private knowledge revisions.

It never talks to a provider, integration, n8n or a customer channel.  The
operator submits a complete draft over stdin; this program validates, backs up
the prior private files, atomically replaces both settings and restarts only
the private AI gateway.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from gateway import validate_kb

ETC = Path("/etc/atendimento-piloto-ai")
KB_PATH = ETC / "kb.json"
INSTRUCTIONS_PATH = ETC / "operator-instructions.json"
ROOT = Path("/opt/atendimento-piloto/source")
RECEIPTS = ROOT / "private/assistant-install"
UNIT = "atendimento-piloto-ai.service"
MAX_REQUEST = 100_000


class Refused(Exception):
    pass


def sha(value):
    return hashlib.sha256(value).hexdigest()


def parse_json(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise Refused("invalid_input")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refused("invalid_json") from error


def read_private(path, limit=131_072):
    try:
        status = path.lstat()
        if path.is_symlink() or not path.is_file() or status.st_size > limit or status.st_mode & 0o077:
            raise Refused("unsafe_private_file")
        return path.read_bytes()
    except OSError as error:
        raise Refused("private_file_unavailable") from error


def validate_instructions(value):
    if not isinstance(value, dict) or set(value) != {"version", "text"}:
        raise Refused("invalid_instructions")
    version, text = value.get("version"), value.get("text")
    if not isinstance(version, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", version):
        raise Refused("invalid_instruction_version")
    if not isinstance(text, str) or not text.strip() or len(text) > 2400 or len(text.encode()) > 6000:
        raise Refused("invalid_instruction_text")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise Refused("invalid_instruction_text")
    return {"version": version, "text": text.strip()}


def validate_document(value):
    if not isinstance(value, dict) or set(value) != {"knowledge", "instructions"}:
        raise Refused("invalid_document")
    try:
        knowledge = validate_kb(value["knowledge"])
    except (TypeError, ValueError, KeyError) as error:
        raise Refused("invalid_knowledge") from error
    return {"knowledge": knowledge, "instructions": validate_instructions(value["instructions"])}


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def current():
    kb_raw = read_private(KB_PATH)
    instructions_raw = read_private(INSTRUCTIONS_PATH, 16_384)
    document = validate_document({"knowledge": parse_json(kb_raw, 131_072),
                                 "instructions": parse_json(instructions_raw, 16_384)})
    return document, kb_raw, instructions_raw


def view():
    document, kb_raw, instructions_raw = current()
    return {"status": "ok", "document": document, "knowledge_sha256": sha(kb_raw),
            "instructions_sha256": sha(instructions_raw), "active": True}


def identity():
    if os.geteuid() != 0 or os.uname().nodename != "agentes-lab":
        raise Refused("unexpected_host")


def atomic_replace(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        staged = Path(handle.name)
    staged.chmod(0o600)
    os.replace(staged, path)


def gateway_info():
    result = subprocess.run(["runuser", "-u", "delladmin", "--", "/usr/local/libexec/atendimento-piloto-ai/client.py"],
                            input='{"op":"info"}', capture_output=True, text=True, timeout=30)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise Refused("gateway_unavailable") from error
    if result.returncode or value.get("status") != "ok":
        raise Refused("gateway_unavailable")
    return value


def publish(request):
    identity()
    document = validate_document(request.get("document"))
    expected_kb = request.get("expected_knowledge_sha256")
    expected_instructions = request.get("expected_instructions_sha256")
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for v in (expected_kb, expected_instructions)):
        raise Refused("missing_revision_guard")
    before, old_kb, old_instructions = current()
    if sha(old_kb) != expected_kb or sha(old_instructions) != expected_instructions:
        raise Refused("revision_changed_reload")
    new_kb, new_instructions = serialized(document["knowledge"]), serialized(document["instructions"])
    if new_kb == old_kb and new_instructions == old_instructions:
        raise Refused("no_changes")
    if not RECEIPTS.is_dir() or RECEIPTS.is_symlink():
        raise Refused("receipt_store_unavailable")
    target = RECEIPTS / ("editor-" + str(time.time_ns()) + "-" + uuid.uuid4().hex[:8])
    target.mkdir(mode=0o700)
    try:
        (target / "intent.json").write_bytes(serialized({
            "old_knowledge_sha256": sha(old_kb), "new_knowledge_sha256": sha(new_kb),
            "old_instructions_sha256": sha(old_instructions), "new_instructions_sha256": sha(new_instructions),
            "from_kb_version": before["knowledge"]["version"], "to_kb_version": document["knowledge"]["version"],
            "from_instructions_version": before["instructions"]["version"],
            "to_instructions_version": document["instructions"]["version"],
        }))
        (target / "previous-kb.json").write_bytes(old_kb)
        (target / "previous-instructions.json").write_bytes(old_instructions)
        for path in (target / "intent.json", target / "previous-kb.json", target / "previous-instructions.json"):
            path.chmod(0o600)
        atomic_replace(KB_PATH, new_kb)
        atomic_replace(INSTRUCTIONS_PATH, new_instructions)
        restarted = subprocess.run(["systemctl", "restart", UNIT], capture_output=True, text=True, timeout=40)
        if restarted.returncode:
            raise Refused("gateway_restart_failed")
        after = gateway_info()
        if after.get("kb_version") != document["knowledge"]["version"]:
            raise Refused("gateway_readback_failed")
        result = {"status": "ok", "knowledge_version": after["kb_version"],
                  "instructions_version": document["instructions"]["version"],
                  "knowledge_sha256": sha(new_kb), "instructions_sha256": sha(new_instructions),
                  "backup_recorded": True, "test_required": True}
        (target / "ready.json").write_bytes(serialized(result))
        (target / "ready.json").chmod(0o600)
        return result
    except BaseException:
        # Only return files changed by this request.  The persistent AI ledger is never restored.
        try:
            atomic_replace(KB_PATH, old_kb)
            atomic_replace(INSTRUCTIONS_PATH, old_instructions)
            subprocess.run(["systemctl", "restart", UNIT], capture_output=True, text=True, timeout=40)
            (target / "rollback.json").write_bytes(serialized({"status": "restored_code_and_content_only"}))
            (target / "rollback.json").chmod(0o600)
        except BaseException:
            pass
        raise


def handle(request):
    if not isinstance(request, dict):
        raise Refused("invalid_input")
    if request.get("op") == "view":
        return view()
    if request.get("op") == "validate":
        document = validate_document(request.get("document"))
        return {"status": "ok", "knowledge_version": document["knowledge"]["version"],
                "instructions_version": document["instructions"]["version"], "entry_count": len(document["knowledge"]["entries"]),
                "publish_requires_confirmation": True}
    if request.get("op") == "publish":
        return publish(request)
    raise Refused("invalid_operation")


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise Refused("request_too_large")
        print(json.dumps(handle(parse_json(raw, MAX_REQUEST)), ensure_ascii=False))
    except Refused as error:
        print(json.dumps({"status": "error", "code": str(error)}))
    except Exception:
        print('{"status":"error","code":"editor_unavailable"}')


if __name__ == "__main__":
    main()
