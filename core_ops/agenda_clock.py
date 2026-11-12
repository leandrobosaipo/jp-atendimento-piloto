"""Build the agenda-only profile of an already bound, unpublished JP clock."""
from copy import deepcopy

from graphs import BASE, PRE


AGENDA = ("plan-initial", "reconcile-responses", "plan-reminders", "before-dispatch",
          "send-initial", "send-reminders")
RETURN_CAMPAIGN = ("reactivation-review", "reactivation-plan", "reactivation-due",
                   "before-dispatch", "reactivation")


def agenda_profile(graph, manifest):
    if manifest["role"] != "clock" or graph.get("active") or graph.get("activeVersionId"):
        raise ValueError("expected_unpublished_clock")
    nodes = {node["name"]: node for node in graph["nodes"]}
    timers = [node for node in nodes.values() if node["type"] == "n8n-nodes-base.scheduleTrigger"]
    if (len(timers) != 1 or timers[0].get("disabled") or len(nodes) != len(graph["nodes"])
            or len(nodes) != 1 + len(AGENDA) + len(RETURN_CAMPAIGN)):
        raise ValueError("unexpected_clock_shape")
    chain = [timers[0]]
    connections = graph["connections"]
    for stage in AGENDA + RETURN_CAMPAIGN:
        edge = connections[chain[-1]["name"]]
        if set(edge) != {"main"} or len(edge["main"]) != 1 or len(edge["main"][0]) != 1:
            raise ValueError("unexpected_branch")
        target = edge["main"][0][0]
        node = nodes[target["node"]]
        path = "/api/n8n/stages/" + stage
        if (target.get("type") != "main" or target.get("index") != 0
                or node["type"] != "n8n-nodes-base.httpRequest"
                or node["parameters"]["url"] != BASE + path
                or node["parameters"].get("method") != "POST"
                or manifest["nodes"].get(node["id"]) != path or node.get("disabled")
                or node.get("retryOnFail") or node.get("continueOnFail")
                or node.get("onError", "stopWorkflow") != "stopWorkflow"):
            raise ValueError("unexpected_clock_stage")
        chain.append(node)
    if len({n["name"] for n in chain}) != len(nodes) or connections.get(chain[-1]["name"]):
        raise ValueError("unexpected_clock_tail")
    keep = chain[:1 + len(AGENDA)]
    keep_ids = {n["id"] for n in keep[1:]}
    before = next(n for n in keep if manifest["nodes"].get(n["id"]) == PRE)
    if before["id"] in manifest["forced"]:
        raise ValueError("wrong_preflight")
    profile = {k: deepcopy(graph[k]) for k in ("name", "settings")}
    profile.update(nodes=deepcopy(keep),
                   connections={n["name"]: deepcopy(connections[n["name"]]) for n in keep[:-1]})
    role = deepcopy(manifest)
    role.update(nodes={k: v for k, v in role["nodes"].items() if k in keep_ids}, forced=[])
    return profile, role


def planning_profile(source, manifest):
    """Select both native planning stages from the audited clock and retain its timer."""
    graph, role = agenda_profile(source, manifest)
    timer = graph["nodes"][0]
    by_route = {role["nodes"][node["id"]]: node for node in graph["nodes"][1:]}
    initial, reminders = [by_route["/api/n8n/stages/" + name] for name in ("plan-initial", "plan-reminders")]
    chain = [timer, initial, reminders]
    graph["nodes"] = chain
    graph["connections"] = {a["name"]: {"main": [[{"node": b["name"], "type": "main", "index": 0}]]}
                            for a, b in zip(chain, chain[1:])}
    role["nodes"] = {node["id"]: role["nodes"][node["id"]] for node in chain[1:]}
    return graph, role
