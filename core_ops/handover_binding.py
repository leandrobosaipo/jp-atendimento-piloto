"""Render one JP ownership epoch for the receiver, customer service and dispatcher."""
from copy import deepcopy
from uuid import UUID

INBOUND = "/api/n8n/inbound"
OPERATIONS = {
    ("clock","send-initial"): ("sendText",True),
    ("clock","send-reminders"): ("sendText",False),
    ("clock","reactivation"): ("sendText",True),
    ("reply","reply-execute"): ("sendText",False),
    ("batch","run"): ("sendText",True),
    ("manual","manual"): ("sendText",True),
    ("brand","brand-send"): ("sendMedia",True),
}


def render(route, side, manifest, business_policy, source_marker, catalog, epoch, not_before, paths):
    if str(UUID(epoch)) != epoch or UUID(epoch).version != 4 or type(not_before) is not int or not_before <= 0:
        raise ValueError("invalid_cutover_watermark")
    route, side = deepcopy(route), deepcopy(side)
    flow = route["workflowId"]
    if (flow != manifest["inbound"]["workflow"] or flow != side["channel_workflow"] or
            route["businessCore"]["url"] != "http://jp-core-operacional:8791"+INBOUND or
            manifest["inbound"]["nodes"].get(route["businessCore"]["node"]) != INBOUND or
            not route.get("nativeBusinessControls") or not route["customer"].get("claimedDirect") or
            not side.get("native_pair_reader") or not route["receiptObserver"].get("resume") or
            route["receiptObserver"].get("fixtureOnly") is not False):
        raise ValueError("unbound_inbound")
    if not business_policy.get("enabled") or set(business_policy["caps"]) != {"minute","hour","day"}:
        raise ValueError("missing_budget_policy")
    for period, cap in business_policy["caps"].items():
        reserve = business_policy["priority_reserve"][period]
        if type(cap) is not int or type(reserve) is not int or cap <= 0 or not 0 <= reserve <= cap:
            raise ValueError("invalid_budget_policy")
    roles = []
    matched = set()
    for role, spec in manifest.items():
        for node, endpoint in spec["nodes"].items():
            key = role, endpoint.rsplit("/",1)[-1]
            if key in OPERATIONS:
                if key in matched:
                    raise ValueError("ambiguous_send_stage")
                matched.add(key)
                op, commercial = OPERATIONS[key]
                roles.append(dict(workflow=spec["workflow"],node=node,role=role,operation=op,commercial=commercial))
    if matched != set(OPERATIONS):
        raise ValueError("incomplete_send_stages")
    roles.extend([dict(workflow=flow,node="public-short-text",role="public_first_reply",operation="sendText",commercial=True),
                  dict(workflow=flow,node="public-art",role="public_art_continuation",operation="sendMedia",commercial=True)])
    hashes = sorted({row["sha256"] for row in catalog["assets"] if row["approved"] is True})
    if not hashes or any(len(s)!=64 or any(c not in "0123456789abcdef" for c in s) for s in [*hashes,source_marker]):
        raise ValueError("missing_catalog_or_budget_checkpoint")
    route["customer"]["epoch"] = route["receiptObserver"]["epoch"] = side["channel_epoch"] = epoch
    side["business_not_before"] = side["service_cases"]["not_before"] = not_before
    first=dict(workflow=flow,node="public-short-text",epoch=epoch,not_before=not_before,instance_hash=side["native_instance_id_hash"],
               account=side["account"],inbox=side["inbox"],queue=side["claimed_queue_db"],intake_root=paths["intake_root"],
               intakes=side["db_file"],reservations=side["reservations_db_file"],plans=side["plans_db_file"],reader_uid=0,
               chat_socket=paths["chat_socket"],manifest=side["asset_manifest_file"],manifest_sha256=side["asset_manifest_sha256"],assets=side["asset_dir"])
    resume={**first,"node":"public-art","text_role":[flow,"public-short-text","sendText"],"ledger":paths["ledger"]}
    policy=dict(mode="owned",epoch=epoch,first_intake_uid=0,receipt_receiver_uid=65534,roles=roles,limits=business_policy["caps"],
                priority_reserve=business_policy["priority_reserve"],approved_asset_hashes=hashes,budget_marker=source_marker)
    return dict(route=route,side=side,first=first,resume=resume,policy=policy,
                chat_reader=dict(account=side["account"],inbox=side["inbox"],token_file=side["chatwoot_token_file"],intakes=side["db_file"]),
                identity_epoch=epoch,ownership_roles=sorted(set(manifest)|{"public_first_reply","public_art_continuation"}))
