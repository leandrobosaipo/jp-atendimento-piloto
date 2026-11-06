"""Consumer proof with only hard-coded synthetic fixtures aimed at this guest's loopback."""
import concurrent.futures
import json
import os
from configure_flows import PRIVATE, Local, dump, n8n_login
from install import dc, wait_http

def main():
    os.umask(0o077)
    cfg=json.loads((PRIVATE/"resources.json").read_text())
    release=json.loads((PRIVATE/"ready.json").read_text())
    if (PRIVATE/"test-intent.json").exists():
        raise SystemExit("Existing fixture attempt. Reconcile by stable ID before doing anything else.")
    dump("test-intent.json",{"workflowId":release["workflowId"]})
    path="/webhook/pilot-synthetic-v1"
    header={"X-Pilot-Fixture":cfg["hookSecret"]}
    def event(label,actor="fixture-001",kind="text",text="Qual é o horário do piloto?",fault="none"):
        return {"source":"fixture","event_id":"demo-"+label,"actor":actor,"kind":kind,"text":text,"fault":fault}
    def post(body):
        result=Local(15678).req("POST",path,body,header)
        if isinstance(result,list) and len(result)==1: result=result[0]
        if not isinstance(result,dict) or result.get("event_id")!=body["event_id"]:
            raise RuntimeError("Webhook did not return a reconciled event: "+body["event_id"])
        return result
    cases=[
        (event("faq"),"simulated","faq"),
        (event("complaint",text="Tenho uma reclamação."),"handoff","complaint"),
        (event("identity",actor="ambiguous"),"handoff","identity"),
        (event("media",kind="media",text="Preciso de ajuda."),"handoff","media"),
        (event("tool",fault="tool"),"handoff","tool_error"),
        (event("uncertain",fault="uncertain"),"uncertain","faq"),
    ]
    proof={}
    for body,state,route in cases:
        r=post(body)
        if r.get("status")!=state or r.get("route")!=route or not r.get("message_id") or bool(r.get("reply"))!=(state=="simulated"):
            raise RuntimeError(f"Unexpected {body['event_id']} state={r.get('status')} route={r.get('route')}")
        proof[body["event_id"]]={"status":state,"route":route,"messageId":r["message_id"]}
    # Concurrent deliveries share one database primary key. The second may see a reserved row.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(post,[event("parallel"),event("parallel")]))
    assert sum(r.get("status")=="simulated" for r in results)==1 and any(r.get("status") in ("duplicate","held") for r in results)
    proof["demo-parallel"]={"status":"simulated","route":"faq","messageId":next(r["message_id"] for r in results if r["status"]=="simulated")}
    # Restart only the dedicated clock and reconcile old events through a new authenticated HTTP request.
    dc("restart","clock");wait_http("clock")
    for body,status in [(event("faq"),"duplicate"),(event("uncertain",fault="uncertain"),"held"),(event("faq",actor="unknown"),"conflict")]:
        result=post(body)
        assert result["status"]==status and result["simulated"] is False
    observed={}
    chat=Local(13300)
    for conv in [cfg["knownConversation"],cfg["reviewConversation"]]:
        records=chat.req("GET",f"/api/v1/accounts/{cfg['accountId']}/conversations/{conv}/messages",headers={"api_access_token":cfg["chatToken"]})["payload"]
        for m in records:
            for event_id,expected in proof.items():
                if f"[fixture {event_id}]" not in (m.get("content") or ""): continue
                assert m.get("private") is True and m["id"]==expected["messageId"] and event_id not in observed
                assert conv==(cfg["reviewConversation"] if event_id=="demo-identity" else cfg["knownConversation"])
                observed[event_id]=m["id"]
    assert len(observed)==len(proof)
    sql="SELECT event_id,state,route,attempts,message_id FROM pilot_events ORDER BY event_id"
    rows=dc("exec","-T","postgres","psql","-U","postgres","-d","synthetic_intake","-At","-F","|","-c",sql).splitlines()
    assert len(rows)==len(proof)
    for line in rows:
        k,state,route,note_attempts,msg=line.split("|")
        assert proof[k]["status"]==state and proof[k]["route"]==route and proof[k]["messageId"]==int(msg)
        proof[k]["requests"]=int(note_attempts)
    read=n8n_login().req("GET","/rest/workflows/"+release["workflowId"])["data"]
    assert read["active"] and read["settings"]["saveDataErrorExecution"]=="none" and read["settings"]["saveDataSuccessExecution"]=="none"
    dump("verified.json",{**release,"cases":proof,"privateNotes":len(observed),"duplicatesSuppressed":True,"ambiguousResendHeld":True,"survivedClockRestart":True,"activeWorkflow":True})
    print("Seven synthetic cases registered once. Duplicate, conflict and uncertainty remained closed after restart.")
if __name__=="__main__":
    main()
