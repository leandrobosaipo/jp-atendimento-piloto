import hashlib
from pathlib import Path
import sys

def patched(data):
    assert hashlib.sha256(data.encode()).hexdigest()=='905a816a1827d2a77f19fa198dc9423e249f0b603b78689415d2037d4e5e8334'
    hold='  const first=individualReminderReadiness(candidate,state);'
    assert data.count(hold)==1
    data=data.replace(hold,"  if(Object.values(state.dell_reply_links||{}).some(r=>r.appointments.includes(candidate.appointment_id)&&['human_hold','human_review','awaiting_official'].includes(r.status)))return {available:false,reason:'Resposta em conferência no Dell.'};\n"+hold)
    anchor='scopedServer(async (req, res) => {'
    assert data.count(anchor)==1
    return 'import {serveCapture,officialFetchFor} from "/ops-app/captured_visit.mjs";\n'+data.replace(anchor,"""serveCapture({load,save,instance,target:inboundTarget,window:responseWithinVisitWindow,blocked:contactBlocked,
  feedback:visitFeedback,support:supportCaseFor,snapshot:visitSnapshot,read:async c=>{
    const cfg=agendaConfig();return readOfficialConfirmation(c,{...cfg,expires_at:cfg?.expires_at||cfg?.session?.expires_at},officialFetchFor(c),true);
  }});
"""+anchor)

if __name__=='__main__':
    Path(sys.argv[2]).write_text(patched(Path(sys.argv[1]).read_text()))
