"""Resolve a new claimed service request against its original Chatwoot message."""
import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path

from bridge import human_pause,intake_held,need
from policy import classify,norm


def decide(question, request, occasion, source, query, assistant):
    options=dict(intent="auto",service="",professional="",date="")
    intent, service, professional, day=classify(question,options,source)
    handoff={"action":"human","reason":"team_review"}
    if intent=="sensitive" or re.search(r"\b(cancel\w*|remarc\w*|reagend\w*|meu horario|minha agenda|meu saldo|meus creditos|pag\w*|financeir\w*|cadastr\w*|confirm\w*|pix|contrat\w*|quero marcar|quero agendar)\b",norm(question)):
        return handoff
    if intent=="availability":
        if re.search(r"\bjp\b",norm(question)):return handoff
        if not service:return dict(action="answer",reply="Para qual serviço você quer ver os horários?",evidence="service_required")
        if not professional:return dict(action="answer",reply="Você prefere consultar com Alysson ou Pedro?",evidence="professional_required")
        if not day:return dict(action="answer",reply="Para qual dia você quer consultar?",evidence="day_required")
        if professional not in ("alysson","pedro"):return handoff
        result=query("availability",dict(service_key=service,professional_key=professional,date=day),source)
        if (result.get("status")!="ok" or result.get("source")!="appbarber" or result.get("reservation_performed") is not False
                or (result.get("date"),result.get("professional_key"),result.get("service_key"))!=(day,professional,service)):return handoff
        # Published JP policy uses the start time in Cuiaba. The provider owns availability.
        date=dt.date.fromisoformat(day)
        def allowed(slot):
            if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]",slot):return False
            if date.weekday()==5:return "08:00"<=slot<="12:00"
            return 1<=date.weekday()<=4 and ("09:00"<=slot<="11:00" or "13:00"<=slot<="18:00")
        slots=[s for s in result["slots"] if allowed(s)][:3]
        if not slots:return dict(action="human",reason="availability_requires_team")
        person=source["professionals"][professional]["name"].split()[0]
        return dict(action="answer",reply=f"Para {date:%d/%m}, encontrei com {person} às "+", ".join(slots)+". Algum desses horários serve para você?",
                    evidence="official_availability",observed_at=result["observed_at"],slots=slots,booking_performed=False)
    if intent!="chat" or re.search(r"\b(ok|sim|nao|aceito|assumo)\b",norm(question).strip()) and len(question.split())<=2:return handoff
    answer=assistant(dict(id=request,session=occasion,text=question,context={"category":"chat"}))
    if answer.get("decision")!="answer" or not answer.get("kb_version"):return handoff
    wording=answer.get("reply")
    if not isinstance(wording,str) or not 0<len(wording.strip())<=600:return handoff
    return dict(action="answer",reply=wording.strip(),evidence="jp_knowledge",kb_version=answer["kb_version"],mode=answer.get("mode"))


def installed(cfg):
    from claimed_customer import _private_config
    from service import ai_socket
    from provider import query
    from customer_ingress import Chatwoot
    # Reuse the same confined official reader and signed JP knowledge gateway.
    def resolve(question,request,occasion):
        source=_private_config("/etc/atendimento-piloto-integracao/appbarber-readonly.json")
        key=json.loads(Path("/run/credentials/atendimento-piloto-integracao.service/runtime").read_bytes())["hmac"].encode()
        return decide(question,request,occasion,source,query,lambda payload:ai_socket(payload,key))
    def team_note(row,question,reason):
        conf=_private_config(cfg["team_configuration"])
        api=Chatwoot(conf["account"],conf["inbox"],conf["channel"],conf["token"])
        conv=conf["conversation"];target=f"/api/v1/accounts/{api.account}/conversations/{conv}"
        def verify():
            c=api.request("GET",target)
            need((c["id"],c["account_id"],c["inbox_id"])==(conv,api.account,api.inbox) and
                 (c["meta"]["sender"]["identifier"]==conf["contact_identifier"]),"wrong_internal_conversation")
            return c
        verify()
        title="Ensaio com cliente fictício" if cfg.get("fixture") is True else "Atendimento JP"
        message=(f"{title} · revisão humana\nConversa {row['conversation']}, mensagem {row['message']}.\n"
                 f"Motivo: {reason}.\nMensagem recebida: {question}\nID de entrada: {row['record']}")
        m=api.request("POST",target+"/messages",{"message_type":"outgoing","private":True,"content":message})
        need(m.get("private") is True and m.get("conversation_id")==conv and m["content"]==message,"private_note_uncertain")
        read=verify()
        need(any(x.get("id")==m["id"] and x.get("private") is True and x.get("content")==message for x in read.get("messages",[])),"private_note_unconfirmed")
        return m["id"]
    return resolve,team_note


def handle(app,planner,intake,reservation,claim,*,resolve,team_note):
    """A stopped/unknown attempt remains visible. Reentry cannot consume it again."""
    record=intake["record"];rid=intake["request"];scope=(app.chat.account,app.chat.inbox,intake["contact"],intake["conversation"])
    retained=dict(status="held",service_record=record)
    claim()
    with planner.db() as db:
        case=db.execute("SELECT * FROM service_requests WHERE record=? AND reservation=?",(record,reservation["reservation"])).fetchone()
        need(case and tuple(case[k] for k in ("account","inbox","contact","conversation"))==scope and case["message"]==intake["message"],"wrong_service_case")
    if intake_held(app.db_path,scope,rid):return dict(retained,service_state="human_ownership")
    conv=app._check_conversation(scope[3],scope[2]);meta=conv["meta"]
    need(isinstance(conv.get("labels"),list) and conv.get("status")=="open","conversation_not_open")
    observed=bool(meta.get("assignee") or meta.get("team") or "jp-atendimento-humano" in conv["labels"])
    if human_pause(app.db_path,scope,observed,rid):return dict(retained,service_state="human_ownership")
    original=[m for m in app.chat.messages(scope[3]) if m["id"]==case["message"] and m["message_type"]==0
              and not m.get("private") and (m.get("sender") or {}).get("id")==scope[2]
              and hashlib.sha256(m["content"].encode()).hexdigest()==case["text_hash"]]
    need(len(original)==1,"service_message_missing")
    with planner.db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS service_decisions(record TEXT PRIMARY KEY,
            request TEXT NOT NULL UNIQUE,decision TEXT NOT NULL,proof TEXT NOT NULL,part TEXT UNIQUE,note INTEGER)""")
        if db.execute("UPDATE service_requests SET state='planning' WHERE record=? AND state='pending'",(record,)).rowcount!=1:
            return dict(retained,service_state=db.execute("SELECT state FROM service_requests WHERE record=?",(record,)).fetchone()[0])
    try:
        claim()
        occasion=str(uuid.uuid5(uuid.NAMESPACE_URL,"jp-service/"+"/".join(map(str,scope))))
        decision=resolve(original[0]["content"],rid,occasion)
        claim()
        if decision["action"]=="human":
            human_pause(app.db_path,scope,True,rid)
            note=team_note(case,original[0]["content"],decision["reason"])
            need(type(note) is int and note>0,"team_readback_missing")
            state="team_received"; part=None
        else:
            need(decision["action"]=="answer" and decision["evidence"] in ("service_required","professional_required","day_required","official_availability","jp_knowledge"),"invalid_service_plan")
            part=str(uuid.uuid5(uuid.NAMESPACE_URL,"jp-service-reply/"+record));note=None;state="answer_prepared"
        with planner.db() as db:
            if part:
                content=decision["reply"]
                need(isinstance(content,str) and 0<len(content.strip())<=600,"invalid_service_reply")
                db.execute("""INSERT INTO held_parts(reservation,position,part_id,account,inbox,contact,conversation,message,
                    content,content_sha256,state) VALUES(?,1,?,?,?,?,?,?,?,?, 'held_before_transport')""",
                    (reservation["reservation"],part,*scope,case["message"],content,hashlib.sha256(content.encode()).hexdigest()))
            db.execute("INSERT INTO service_decisions VALUES(?,?,?,?,?,?)",
                       (record,rid,decision["action"],json.dumps(decision,ensure_ascii=False,separators=(",",":")),part,note))
            need(db.execute("UPDATE service_requests SET state=? WHERE record=? AND state='planning'",(state,record)).rowcount==1,"service_owner_changed")
        return dict(status="prepared_held" if part else "held",service_record=record,service_state=state,parts=[{"part_id":part}] if part else [])
    except Exception:
        with planner.db() as db:db.execute("UPDATE service_requests SET state='requires_reconciliation' WHERE record=? AND state='planning'",(record,))
        raise
