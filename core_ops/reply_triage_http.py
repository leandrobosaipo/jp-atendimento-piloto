"""Root-only Unix-socket endpoint for private Chatwoot review cases."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import sqlite3
import struct
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import reply_triage


DB = Path("/var/lib/jp-reply-capture/replies.sqlite3")
TEAM = Path("/etc/atendimento-piloto-customer-ingress/team-service.json")


def _context(case: reply_triage.Case) -> dict:
    """Read only a bound reply for the restricted team inbox."""
    program = r'''import fs from 'node:fs';import crypto from 'node:crypto';import {decodeStateEnvelope} from '/legacy/scripts/jp-state-envelope.mjs';
const q=JSON.parse(fs.readFileSync(0));
const cfg=JSON.parse(fs.readFileSync('/config/runtime.json'));
const state=JSON.parse(decodeStateEnvelope(JSON.parse(fs.readFileSync('/state/state.json')),Buffer.from(cfg.stateKey,'hex')));
const key=crypto.createHash('sha256').update(JSON.stringify([q.mid,q.fp])).digest('hex');
const link=state.dell_reply_links?.[key];
const result={visits:[],received:''};
if(link?.key===key && link.mid===q.mid){
 const document=JSON.parse(fs.readFileSync(process.env.JP_APPROVAL_CANDIDATES_FILE||'/state/candidates.json'));
 const candidates=document.candidates||[];
 for(const id of link.appointments||[]){
   const matches=candidates.filter(c=>String(c.appointment_id)===String(id));
   const v=matches.length===1 && matches[0];
   if(v) result.visits.push({name:v.name||v.first_name,date:v.appointment_date,time:v.appointment_time,service:v.service});
 }
 const messages=(state.messages||[]).filter(m=>m.instance===cfg.instance && m.source==='dell_capture' &&
   m.message_id===q.mid && m.direction==='inbound' && link.appointments?.includes(m.appointment_id));
 if(messages.length===1) result.received=String(messages[0].text||'').slice(0,500);
}
console.log(JSON.stringify(result));'''
    value = subprocess.check_output(
        ["docker", "exec", "-i", "jp-core-operacional", "node", "--input-type=module", "-e", program],
        input=json.dumps({"mid": case.message_id, "fp": case.fingerprint}), text=True, timeout=20,
    )
    data = json.loads(value)
    return data


def _chatwoot(case: reply_triage.Case) -> tuple[int, int]:
    """Create/re-read only an internal API-channel note; never a public message."""
    config = json.loads(TEAM.read_bytes())
    if config.get("account") != 2 or config.get("inbox") != 3:
        raise RuntimeError("unexpected_team_inbox")
    context = _context(case)
    when = datetime.fromisoformat(case.received_at.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Cuiaba"))
    note = reply_triage.render_note(case.status, case.kind, case.disposition, case.marker, context["visits"],
                                    received=context["received"], when=when.strftime("%d/%m às %H:%M"))
    payload = {
        "account": 2,
        "inbox": 3,
        "marker": case.marker,
        "origin": case.origin_hash,
        "note": note,
        "source": "jp-reply-triage/" + str(case.capture),
    }
    ruby = r'''
require 'json'
p=JSON.parse(STDIN.read)
r=ActiveRecord::Base.transaction do
 ActiveRecord::Base.connection.execute('SELECT pg_advisory_xact_lock(984731283)')
 a=Account.find(p.fetch('account')); i=a.inboxes.find(p.fetch('inbox'))
 raise 'wrong_inbox' unless i.name=='Revisão privada da manutenção' && i.channel.is_a?(Channel::Api) && i.channel.webhook_url.blank? && a.webhooks.empty?
 ident='jp-triage-origin-'+p.fetch('origin')
 c=a.contacts.find_or_create_by!(identifier:ident){|x|x.name='Retorno WhatsApp em revisão'}
 raise 'delivery_address' unless c.email.blank? && c.phone_number.blank?
 ci=ContactInbox.find_or_create_by!(contact:c,inbox:i){|x|x.source_id=p.fetch('source')}
 v=a.conversations.find_by(inbox:i,contact:c) || Conversation.create!(account:a,inbox:i,contact:c,contact_inbox:ci).reload
 ref='jp-reply-triage/'+p.fetch('marker')
 m=v.messages.find_by(source_id:ref)
 if m
  raise 'note_mismatch' unless m.private? && m.content.include?(p.fetch('marker'))
 else
  m=v.messages.create!(account:a,inbox:i,private:true,message_type: :outgoing,content:p.fetch('note'),source_id:ref)
 end
 raise 'public_message' if v.messages.where(private:false).exists?
 {conversation:v.display_id,note:m.id}
end
puts 'RECEIPT='+JSON.generate(r)
'''
    container = subprocess.check_output(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=atendimento-piloto", "--filter", "label=com.docker.compose.service=chat"],
        text=True,
    ).strip()
    if not container or "\n" in container:
        raise RuntimeError("chatwoot_container_unavailable")
    result = subprocess.run(
        ["docker", "exec", "-i", container, "bundle", "exec", "rails", "runner", ruby],
        input=json.dumps(payload), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    if result.returncode:
        raise RuntimeError("chatwoot_private_case_failed")
    receipt = json.loads(next(line.split("=", 1)[1] for line in result.stdout.splitlines() if line.startswith("RECEIPT=")))
    return int(receipt["conversation"]), int(receipt["note"])


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.path != "/tick" or self.headers.get("Content-Length", "0") not in {"0", ""}:
            self.send_error(403)
            return
        _pid, uid, _gid = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            self.send_error(403)
            return
        try:
            with sqlite3.connect(DB, timeout=8) as db:
                db.row_factory = sqlite3.Row
                result = reply_triage.run_tick(db, _chatwoot, limit=3)
            body = json.dumps({"processed": result["created"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            print("triage held", type(error).__name__, file=sys.stderr, flush=True)
            self.send_error(503)


if __name__ == "__main__":
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise SystemExit("requires socket activation")
    server = http.server.HTTPServer.__new__(http.server.HTTPServer)
    socketserver.BaseServer.__init__(server, ("localhost", 0), Handler)
    server.socket = socket.socket(fileno=3)
    while select.select([server.socket], [], [], 8)[0]:
        server.handle_request()
