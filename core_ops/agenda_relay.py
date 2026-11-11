"""Limit the operator relay to the explicit, single-visit agenda confirmation."""
from copy import deepcopy


def selection_only(graph):
    g = deepcopy(graph)
    nodes = g["nodes"]
    switch, = (n for n in nodes if n["type"] == "n8n-nodes-base.if")
    hook, = (n for n in nodes if n["type"] == "n8n-nodes-base.webhook")
    pre, = (n for n in nodes if n["parameters"].get("url", "").endswith("/api/n8n/stages/before-dispatch"))
    relay, = (n for n in nodes if n["parameters"].get("url", "").endswith("/api/n8n/manual"))
    edges = g["connections"]
    dest = lambda n, i: edges[n["name"]]["main"][i][0]["node"]
    if (len(nodes) != 6 or dest(hook, 0) != switch["name"] or dest(switch, 0) != relay["name"]
            or dest(switch, 1) != pre["name"] or dest(pre, 0) != relay["name"] or dest(pre, 1) != dest(relay, 1)):
        raise ValueError("unexpected_relay")
    condition, = switch["parameters"]["conditions"]["conditions"]
    if condition["operator"] != {"type": "boolean", "operation": "equals"} or condition["rightValue"] is not True:
        raise ValueError("unexpected_predicate")
    condition["leftValue"] = ("={{ $json.body?.method === 'POST' && $json.body?.route === '/api/confirmation-batch/start'"
                              " && $json.body?.body?.authorization === true && Array.isArray($json.body?.body?.items)"
                              " && $json.body.body.items.length === 1 }}")
    edges[switch["name"]]["main"] = [deepcopy(edges[switch["name"]]["main"][1]), deepcopy(edges[pre["name"]]["main"][1])]
    return g
