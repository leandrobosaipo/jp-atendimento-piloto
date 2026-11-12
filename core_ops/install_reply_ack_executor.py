"""Patch the already-scoped Dell core with the bounded confirmation ACK stage."""
import hashlib
from pathlib import Path
import sys


HELPERS = """serveCapture({load,save,instance,target:inboundTarget,window:responseWithinVisitWindow,blocked:contactBlocked,
  feedback:visitFeedback,support:supportCaseFor,snapshot:visitSnapshot,read:async c=>{
    const cfg=agendaConfig();return readOfficialConfirmation(c,{...cfg,expires_at:cfg?.expires_at||cfg?.session?.expires_at},officialFetchFor(c),true);
  }});"""
HELPERS_REPLACEMENT = """const capturedVisitHelpers={load,save,instance,target:inboundTarget,window:responseWithinVisitWindow,blocked:contactBlocked,
  feedback:visitFeedback,support:supportCaseFor,snapshot:visitSnapshot,read:async c=>{
    const cfg=agendaConfig();return readOfficialConfirmation(c,{...cfg,expires_at:cfg?.expires_at||cfg?.session?.expires_at},officialFetchFor(c),true);
  }};
serveCapture(capturedVisitHelpers);"""
IMPORT = "import {runCapturedConfirmation} from \"/ops-app/captured_confirmation.mjs\";\nimport {executeAcknowledgements} from \"/ops-app/reply_ack_executor.mjs\";\n"
STAGE = """      if(stage==='reply-execute'){
        const dispatchReady=dispatchSyncProof(load(),identity.executionId,true);
        const confirmation=await runCapturedConfirmation({...capturedVisitHelpers,dispatchReady:execution=>execution===identity.executionId&&dispatchReady});
        let acknowledgement={status:'idle'};
        try{if(dispatchReady){
          const policy=JSON.parse(fs.readFileSync('/ops-config/automatic-confirmations.json','utf8'));
          if(policy.enabled===true&&typeof policy.afterUtc==='string') acknowledgement=await executeAcknowledgements(capturedVisitHelpers,policy.afterUtc);
        }}catch{acknowledgement={status:'held'};}
        if(!dispatchReady) acknowledgement={status:'preflight_held'};
        return n8nStageResponse(res,identity,stage,200,{ok:true,status:acknowledgement.status,confirmation:confirmation.status,attempted:acknowledgement.status==='provider_accepted'?1:0});
      }
"""


def patched(data):
    if IMPORT.encode() in data:
        raise ValueError("reply_ack_executor_already_patched")
    if data.count(HELPERS.encode()) != 1:
        raise ValueError("captured_visit_helper_anchor_missing")
    anchor = b"      const stage=req.url.slice('/api/n8n/stages/'.length),sendStages=new Set(['send-initial','send-reminders','support']);"
    if data.count(anchor) != 1:
        raise ValueError("reply_stage_anchor_missing")
    data = data.replace(HELPERS.encode(), HELPERS_REPLACEMENT.encode())
    data = IMPORT.encode() + data.replace(anchor, STAGE.encode() + anchor)
    return data


if __name__ == '__main__':
    source, destination = map(Path, sys.argv[1:3])
    destination.write_bytes(patched(source.read_bytes()))
