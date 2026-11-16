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
    if not context["received"] and case.kind in ("text","emoji_text","reaction"):
        note += original_context(case)
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


def original_context(case):
    """Quote the authenticated source in the private case, even without a matched visit."""
    sys.path[:0]=['/opt/jp-reply-capture','/opt/jp-dispatch/current']
    import reply_capture as capture
    from dispatch_service import read_record
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT record_id,envelope_pos,item_pos,message_id,content_sha256,address_hashes FROM captured WHERE rowid=?',(case.capture,)).fetchone()
    if not row or row[3:5]!=(case.message_id,case.fingerprint):return ''
    _,raw=read_record(capture.ROOT,row[0],65534)
    env=(raw if isinstance(raw,list) else [raw])[row[1]]
    batch=env['data'];msg=(batch if isinstance(batch,list) else [batch])[row[2]]
    key=msg.get('key') or {}
    if env.get('instance')!=capture.INSTANCE or key.get('fromMe') is not False or key.get('id')!=row[3]:return ''
    kind,disposition,quoted,text=capture.classify(msg.get('message') or {})
    if capture.digest(json.dumps([kind,disposition,quoted,text,json.loads(row[5])],ensure_ascii=False))!=row[4]:return ''
    if not isinstance(text,str) or not text.strip():return ''
    return '\nMensagem recebida na conversa de origem: «'+text[:500]+'». Esta nota é interna. Confira a conversa original antes de responder.'


def observe_renewal(db,root=Path('/var/lib/jp-migracao-revisao/core-operacional'),now=None):
    """Observe the existing n8n renewal intent, never call AppBarber or submit a login."""
    directory=root/'state/renewal-intents'
    if not directory.is_dir():return
    import time
    now=int(time.time()) if now is None else now
    # Ignore completed history older than the session installed by the official verifier.
    replaced=int((root/'config/session.json').stat().st_mtime)
    db.execute("CREATE TABLE IF NOT EXISTS observed_renewal(job TEXT PRIMARY KEY,started INTEGER,execution TEXT,closed INTEGER DEFAULT 0)")
    for row in db.execute("SELECT job,started,execution FROM observed_renewal WHERE closed=0").fetchall():
        if replaced>row[1]:
            db.execute("INSERT OR IGNORE INTO capture_incidents(start,kind,execution,stamp) VALUES(?,'agenda_reler',?,?)",(row[1],row[2],now))
            db.execute('UPDATE observed_renewal SET closed=1 WHERE job=?',(row[0],))
    for file in directory.glob('*.json'):
        ts=int(file.stat().st_mtime)
        if ts<replaced or now-ts<180 or db.execute('SELECT 1 FROM observed_renewal WHERE job=?',(file.stem,)).fetchone():continue
        item=json.loads(file.read_bytes());execution=str(item.get('execution',''))
        if item.get('job')!=file.stem or not execution.isdigit() or len(execution)>20:continue
        db.execute('INSERT INTO observed_renewal(job,started,execution) VALUES(?,?,?)',(file.stem,ts,execution))
        db.execute("INSERT OR IGNORE INTO capture_incidents(start,kind,execution,stamp) VALUES(?,'agenda_acesso',?,?)",(ts,execution,now))


def reflect_health(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_incidents'").fetchone():return
    with db:observe_renewal(db)
    rows=[dict(r) for r in db.execute('SELECT start,kind,execution,stamp FROM capture_incidents WHERE noted=0 ORDER BY rowid LIMIT 4')]
    if not rows:return
    ruby="""require 'json'
a=Account.find(2);i=a.inboxes.find(3)
raise unless i.name=='Revisão privada da manutenção' && i.channel.is_a?(Channel::Api) && i.channel.webhook_url.blank? && a.webhooks.empty?
ActiveRecord::Base.transaction do
 ActiveRecord::Base.connection.execute('SELECT pg_advisory_xact_lock(984731283)')
 c=a.contacts.find_or_create_by!(identifier:'jp-dell-fluxos-internos'){|x|x.name='Saúde do atendimento no Dell'}
 raise unless c.email.blank? && c.phone_number.blank?
 ci=ContactInbox.find_or_create_by!(contact:c,inbox:i){|x|x.source_id='jp-saude-dell'}
 v=a.conversations.find_by(contact:c,inbox:i)||Conversation.create!(account:a,inbox:i,contact:c,contact_inbox:ci).reload
 raise if v.messages.where(private:false).exists?
 JSON.parse(STDIN.read).each do |r|
  key='jp-dell/captura/'+r['start'].to_s+'/'+r['kind']
  m=v.messages.find_by(source_id:key)||v.messages.create!(account:a,inbox:i,private:true,message_type: :outgoing,source_id:key,content:r['text'])
  raise unless m.reload.private? && m.content==r['text']
  v.open! if v.resolved? && ['interrompido','agenda_acesso'].include?(r['kind'])
 end
end"""
    for row in rows:
        when=datetime.fromtimestamp(row['stamp'],ZoneInfo('America/Cuiaba')).strftime('%d/%m %H:%M')
        what={'interrompido':'Passaram ao menos três minutos sem um ciclo de captura e associação saudável. Confira o n8n e o núcleo do Dell. Preserve as entradas e não reenvie avisos.',
              'retomado':'A captura e a associação voltaram a concluir um ciclo. Confira as entradas acumuladas; este registro não comprova resposta nem entrega.',
              'agenda_acesso':'O n8n pediu renovação do AppBarber e o arquivo da sessão não foi atualizado em três minutos. Conferir o job de acesso antes da próxima janela. Não repetir login nem assumir que a agenda está vazia.',
              'agenda_reler':'A sessão da agenda foi atualizada depois do pedido do n8n. Conferir a leitura oficial na próxima execução antes de liberar avisos.'}[row['kind']]
        row['text']=when+' (Cuiabá) · '+what+' Execução n8n '+row['execution']+'.'
    proc=subprocess.run(['docker','exec','-i','atendimento-piloto-chat-1','bundle','exec','rails','runner',ruby],input=json.dumps(rows),text=True,capture_output=True,timeout=45)
    if proc.returncode:raise RuntimeError('private_health_note_failed')
    with db:db.executemany('UPDATE capture_incidents SET noted=1 WHERE start=? AND kind=?',[(r['start'],r['kind']) for r in rows])


def reflect_questions(db):
    path=Path('/var/lib/jp-migracao-revisao/core-operacional/ops-config/read-grants/visit-questions-observed.json')
    if not path.is_file():
        return
    db.execute("create table if not exists question_team_notes(ticket integer,stage text, primary key(ticket,stage))")
    texts={'attempted':'O Dell registrou a tentativa de perguntar se todos os serviços estão confirmados. Aguardando recibo ou resposta.',
           'native':'O WhatsApp registrou a entrega da pergunta ao destino da visita.',
           'responded':'Chegou resposta à pergunta. Confira a agenda e o retorno do Dell antes de encerrar o caso.',
           'completed':'Todos os serviços foram conferidos como confirmados no AppBarber e o WhatsApp registrou a entrega do retorno ao cliente.'}
    stages=[]
    for row in json.loads(path.read_bytes())['rows']:
        for key,text in texts.items():
            if row.get(key) and (row['case'] in (3,4)) and not db.execute('select 1 from question_team_notes where ticket=? and stage=?',(row['case'],key)).fetchone():
                stages.append(dict(ticket=row['case'],stage=key,text=text))
    if not stages:return
    ruby=r"""
require 'json'
a=Account.find(2);i=a.inboxes.find(3)
raise unless i.channel.is_a?(Channel::Api) && i.channel.webhook_url.blank? && a.webhooks.empty?
ActiveRecord::Base.transaction do
 JSON.parse(STDIN.read).each do |p|
  v=a.conversations.lock.find_by!(inbox:i,display_id:p['ticket'])
  raise unless v.messages.where(id: {3=>468,4=>469}.fetch(p['ticket']),private:true).exists?
  ref="jp-question/#{p['ticket']}/#{p['stage']}"
  m=v.messages.find_by(source_id:ref)||v.messages.create!(account:a,inbox:i,private:true,message_type: :outgoing,source_id:ref,content:p['text'])
  raise unless m.reload.private? && m.content==p['text']
  v.update!(status: :resolved) if p['stage']=='completed' && v.open? && !v.messages.where('id > ?', {3=>468,4=>469}.fetch(p['ticket'])).where("coalesce(source_id,'') NOT LIKE 'jp-question/%'").exists?
 end
end
"""
    proc=subprocess.run(['docker','exec','-i','atendimento-piloto-chat-1','bundle','exec','rails','runner',ruby],input=json.dumps(stages),text=True,capture_output=True,timeout=50)
    if proc.returncode:raise RuntimeError('private_question_note_failed')
    db.executemany('insert or ignore into question_team_notes values(?,?)',[(r['ticket'],r['stage']) for r in stages])


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.path.startswith('/ownership/'):
            return self.ownership(self.path[11:])
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
                reflect_health(db)
                result = reply_triage.run_tick(db, _chatwoot, limit=3)
                reflect_questions(db)
            body = json.dumps({"processed": result["created"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            print("triage held", type(error).__name__, file=sys.stderr, flush=True)
            self.send_error(503)

    def ownership(self,seq):
        import re
        peer=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        if not re.fullmatch(r'[0-9]{1,12}',seq) or struct.unpack('3i',peer)[1]!=0:
            return self.send_error(403)
        with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as db:
            entry=db.execute('SELECT t.conversation,c.received_at FROM captured c JOIN triage_cases t ON t.capture=c.rowid WHERE c.seq=?',(int(seq),)).fetchall()
        allowed=False
        if len(entry)==1:
            ticket,at=entry[0]
            stamp=int(datetime.fromisoformat(at.replace('Z','+00:00')).timestamp())
            sql=f"""SELECT count(*) FROM conversations v JOIN inboxes i ON i.id=v.inbox_id
               WHERE v.account_id=2 AND v.inbox_id=3 AND i.channel_type='Channel::Api' AND v.display_id={int(ticket)}
               AND v.status=0 AND v.assignee_id IS NULL AND NOT EXISTS
               (SELECT 1 FROM messages m WHERE m.conversation_id=v.id AND m.created_at>=to_timestamp({stamp})
               AND (m.private=false OR m.sender_type='User'))"""
            proc=subprocess.run(['docker','exec','atendimento-piloto-postgres-1','psql','-XAt','-v','ON_ERROR_STOP=1','-U','postgres','-d','chat','-c',sql],capture_output=True,text=True,timeout=4)
            allowed=proc.returncode==0 and proc.stdout.strip()=='1'
        payload=json.dumps({'available':allowed}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)


if __name__ == "__main__":
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise SystemExit("requires socket activation")
    server = http.server.HTTPServer.__new__(http.server.HTTPServer)
    socketserver.BaseServer.__init__(server, ("localhost", 0), Handler)
    server.socket = socket.socket(fileno=3)
    while select.select([server.socket], [], [], 8)[0]:
        server.handle_request()
