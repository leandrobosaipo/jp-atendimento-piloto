"""Apply a narrow change to the verified Dell runtime source; no state migration."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile

BASE = "e3530fbd51f5956d7b9238b343a16efee1b6ff0f9bb8c6fa9cd6a660682a5453"


def patched(source):
    assert hashlib.sha256(source.encode()).hexdigest() == BASE
    updates = {
        "function reminderReadiness(candidate,state){": """function reminderReadiness(candidate,state){
  const first=individualReminderReadiness(candidate,state);
  if(!first.available)return first;
  const record=responseRecord(state,candidate.appointment_id);
  const sources=[...(state.individual_dispatches||[]),...(state.reminders||[])].filter(d=>d.message_id===record.outbound_message_id&&sameDestination(d.destination_e164,candidate.destination_e164));
  const groups=[record.visit_group,...sources.map(d=>d.visit_group)].filter(Boolean);
  if(!groups.length)return first;
  const group=groups[0];
  const held={available:false,reason:'Aguardando a confirmação oficial de todos os serviços da visita.'};
  if(!groups.every(g=>sameVisit(g,group))||!group.appointment_ids.includes(candidate.appointment_id))return held;
  const members=dispatchMembers({visit_group:group});
  if(members.length!==group.appointment_ids.length)return held;
  return members.every(c=>responseRecord(state,c.appointment_id)?.outbound_message_id===record.outbound_message_id&&individualReminderReadiness(c,state).available)?first:held;
}
function individualReminderReadiness(candidate,state){""",
        "if(!dispatches.some(d=>d.message_id===record.outbound_message_id))":
            "if(!sameDestination(record.destination_e164,candidate.destination_e164)||!dispatches.some(d=>d.message_id===record.outbound_message_id&&sameDestination(d.destination_e164,candidate.destination_e164)))",
        "if(record.official_update?.status!=='confirmed'||!proof)return":
            "if(record.official_update?.status!=='confirmed'||proof?.confirmed!==true||proof.status!=='1'||!Number.isFinite(Date.parse(proof.checked_at)))return",
    }
    for before, after in updates.items():
        assert source.count(before) == 1
        source = source.replace(before, after)
    return source


if __name__ == "__main__":
    source, dest = map(Path, sys.argv[1:])
    result = patched(source.read_text())
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as out:
        out.write(result.encode())
        os.fchmod(out.fileno(), source.stat().st_mode & 0o777)
        os.fsync(out.fileno())
    os.replace(out.name, dest)
