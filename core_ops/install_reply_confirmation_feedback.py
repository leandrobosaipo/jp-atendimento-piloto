"""Install the reply-confirmation feedback path without reviving legacy reply work.

The existing reply clock remains its sole timer.  This graph first retains new
inbound records, then uses the native preflight and the single reply-execute
stage.  It has no Evolution node: the operational core owns the native
dispatcher and can bind an attempt to an outbox item before it reaches the
socket.
"""
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path


WID = "Bfk4YqHeYvQWpqiD"
BASE = "http://jp-core-operacional:8791"
PRE_ID = "d9c7458a-e942-4fd8-8d46-92571eccb1b3"
EXEC_ID = "c5015efa-cb69-4393-87de-9a7b71d7cd94"
CAPTURE_ID = "2b6b164e-2a9a-4402-8cc1-9863395512c7"
PENDING_ID = "b1abdc0d-97d6-4c92-9e74-293a011ce697"
BACKUP = Path("/etc/jp-reply-capture/reply-feedback-before.json")
RECEIPT = Path("/etc/jp-reply-capture/reply-feedback-prepared.json")
CORE = Path("/var/lib/jp-migracao-revisao/core-operacional")


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())


def shape(workflow):
    return {key: workflow[key] for key in ("name", "nodes", "connections", "settings")}


def get_api():
    sys.path.insert(0, "/opt/atendimento-piloto/source")
    from configure_flows import n8n_login
    return n8n_login()


def headers(node_id):
    return {"parameters": [
        {"name": "X-JP-n8n-workflow-id", "value": "={{ $workflow.id }}"},
        {"name": "X-JP-n8n-execution-id", "value": "={{ $execution.id }}"},
        {"name": "X-JP-Node-ID", "value": node_id},
    ]}


def core_node(node_id, name, path, credential, body):
    parameters = {
        "method": "POST", "url": BASE + path,
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "sendHeaders": True, "headerParameters": headers(node_id),
        "sendBody": True, "specifyBody": "json", "jsonBody": body,
        "options": {"timeout": 600000},
    }
    return {"id": node_id, "name": name, "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2, "position": [600 if node_id == PRE_ID else 900, 0],
            "parameters": parameters,
            "credentials": {"httpHeaderAuth": credential}}


def pending_node():
    return {"id": PENDING_ID, "name": "Há confirmação oficial ou retorno pendente?",
            "type": "n8n-nodes-base.if", "typeVersion": 2.3, "position": [400, 0],
            "parameters": {"conditions": {"combinator": "and", "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                          "conditions": [{"id": "a2c7ceda-3dfa-4f0e-b1c1-9f9ca1fc90a1", "leftValue": "={{ $json.actionPending }}", "rightValue": True,
                                          "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}}


def expected_capture_only(workflow):
    ids = {node.get("id") for node in workflow["nodes"]}
    return ids == {"a5831f01-beb7-40f4-b970-d993925303e1", CAPTURE_ID}


def definition(current):
    if not expected_capture_only(current):
        raise ValueError("reply_workflow_not_capture_only")
    capture = next(node for node in current["nodes"] if node["id"] == CAPTURE_ID)
    if capture.get("credentials", {}).get("httpHeaderAuth") is None:
        raise ValueError("capture_credential_missing")
    original = json.loads(Path("/etc/jp-reply-capture/workflow-original.json").read_text())
    role_node = next(node for node in original["nodes"] if node.get("id") == EXEC_ID)
    credential = role_node.get("credentials", {}).get("httpHeaderAuth")
    if not credential or not credential.get("id"):
        raise ValueError("reply_role_credential_missing")
    timer = next(node for node in current["nodes"] if node["type"].endswith("scheduleTrigger"))
    pre = core_node(PRE_ID, "Pré-sincronizar antes da confirmação", "/api/n8n/stages/before-dispatch", credential, "={{ { force: true } }}")
    execute = core_node(EXEC_ID, "Confirmar e responder confirmação oficial", "/api/n8n/stages/reply-execute", credential, "={{ {} }}")
    pending = pending_node()
    graph = {"name": current["name"], "nodes": [deepcopy(timer), deepcopy(capture), pending, pre, execute],
             "connections": {timer["name"]: {"main": [[{"node": capture["name"], "type": "main", "index": 0}]]},
                             capture["name"]: {"main": [[{"node": pending["name"], "type": "main", "index": 0}]]},
                             pending["name"]: {"main": [[{"node": pre["name"], "type": "main", "index": 0}]]},
                             pre["name"]: {"main": [[{"node": execute["name"], "type": "main", "index": 0}]]}},
             "settings": deepcopy(current["settings"])}
    return graph


def gates_ready():
    owner = json.loads((CORE / "ops-state/ownership.json").read_text())
    policy = json.loads(Path("/etc/jp-dispatch/policy.json").read_text())
    if not (CORE / "ops-app/captured_confirmation.mjs").is_file():
        raise ValueError("captured_confirmation_module_missing")
    if owner.get("mode") != "owned" or "reply" not in owner.get("roles", []) or time.time() >= __import__("datetime").datetime.fromisoformat(owner["until"]).timestamp():
        raise ValueError("reply_owner_not_ready")
    if policy.get("mode") != "owned" or "reply" not in policy.get("roles", []):
        raise ValueError("reply_dispatch_policy_not_ready")


def apply():
    api = get_api()
    current = api.req("GET", "/rest/workflows/" + WID)["data"]
    if not current.get("active") or not expected_capture_only(current):
        raise ValueError("live_reply_capture_precondition_failed")
    if BACKUP.exists() or RECEIPT.exists():
        raise ValueError("reply_feedback_receipt_already_exists")
    gates_ready()
    graph = definition(current)
    write_new(BACKUP, {"version": current["versionId"], "shape": shape(current), "at": time.time()})
    try:
        api.req("POST", "/rest/workflows/" + WID + "/deactivate", {})
        stopped = api.req("GET", "/rest/workflows/" + WID)["data"]
        if stopped.get("active") or shape(stopped) != shape(current):
            raise RuntimeError("reply_feedback_deactivation_readback_failed")
        api.req("PATCH", "/rest/workflows/" + WID, {**graph, "versionId": stopped["versionId"]})
        staged = api.req("GET", "/rest/workflows/" + WID)["data"]
        if staged.get("active") or shape(staged) != graph:
            raise RuntimeError("reply_feedback_prepare_readback_failed")
        api.req("POST", "/rest/workflows/" + WID + "/activate", {"versionId": staged["versionId"]})
        readback = api.req("GET", "/rest/workflows/" + WID)["data"]
        if not readback.get("active") or readback.get("activeVersionId") != staged["versionId"]:
            raise RuntimeError("reply_feedback_activation_readback_failed")
    except Exception:
        now = api.req("GET", "/rest/workflows/" + WID)["data"]
        if now.get("active"):
            api.req("POST", "/rest/workflows/" + WID + "/deactivate", {})
            now = api.req("GET", "/rest/workflows/" + WID)["data"]
        api.req("PATCH", "/rest/workflows/" + WID, {**shape(current), "versionId": now["versionId"]})
        reverted = api.req("GET", "/rest/workflows/" + WID)["data"]
        api.req("POST", "/rest/workflows/" + WID + "/activate", {"versionId": reverted["versionId"]})
        raise
    write_new(RECEIPT, {"version": readback["versionId"], "backup": str(BACKUP), "active": True,
                        "nodes": [CAPTURE_ID, PENDING_ID, PRE_ID, EXEC_ID], "at": time.time()})
    print(json.dumps({"active": True, "version": readback["versionId"]}))


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        apply()
    else:
        raise SystemExit("usage: install_reply_confirmation_feedback.py apply")
