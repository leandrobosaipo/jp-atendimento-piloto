"""Wire contract for the JP Unix socket. Validation alone never grants or performs a send."""
import re
from bridge import need

INSTANCE = "jp-protese-atendimento"
FIELDS = {"operation", "instance", "workflow", "execution", "node", "source_event_id", "attempt_id", "native_path", "payload"}


def validate_request(body, *, roles):
    """roles comes from the authenticated caller's private workflow/node permissions."""
    need(isinstance(body, dict) and set(body) == FIELDS, "invalid_dispatch_contract")
    op = body["operation"]
    need(op in ("sendText", "sendMedia") and body["instance"] == INSTANCE, "wrong_operation_or_instance")
    need(body["native_path"] == "/message/" + op + "/" + INSTANCE, "wrong_native_path")
    for name in ("workflow", "node", "source_event_id", "attempt_id"):
        value = body[name]
        need(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}", value), "invalid_domain_id")
    need(isinstance(body["execution"], str) and re.fullmatch(r"[0-9]{1,20}", body["execution"]), "invalid_execution")
    need((body["workflow"], body["node"], op) in roles, "unauthorized_dispatch_role")
    payload = body["payload"]
    need(isinstance(payload, dict) and isinstance(payload.get("number"), str) and
         re.fullmatch(r"[1-9][0-9]{7,14}", payload["number"]), "direct_number_required")
    allowed = {"number", "delay", "linkPreview"}
    if "delay" in payload:
        need(type(payload["delay"]) is int and 0 <= payload["delay"] <= 60000, "invalid_delay")
    if "linkPreview" in payload:
        need(payload["linkPreview"] is False, "link_preview_not_allowed")
    if op == "sendText":
        need(not (set(payload) - allowed - {"text"}) and isinstance(payload.get("text"), str) and
             0 < len(payload["text"].strip()) <= 4096, "invalid_text")
    else:
        allowed |= {"mediatype", "mimetype", "caption", "fileName", "media"}
        pair = (payload.get("mediatype"), payload.get("mimetype"))
        need(not (set(payload) - allowed) and pair in (("image", "image/jpeg"), ("video", "video/mp4")), "invalid_media_type")
        need(isinstance(payload.get("caption", ""), str) and len(payload.get("caption", "")) <= 1024, "invalid_caption")
        need(isinstance(payload.get("fileName"), str) and re.fullmatch(r"[\w .-]{1,180}", payload["fileName"])
             and payload["fileName"] not in (".", ".."), "invalid_filename")
        media = payload.get("media")
        need(isinstance(media, str) and 0 < len(media) <= 24*1024*1024 and len(media)%4==0 and
             re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", media), "invalid_media_encoding")
    return body


def recipient_binding(submitted, verified_recipient):
    """Called only with a destination returned for the submitted number by an independent native lookup."""
    if verified_recipient == submitted + "@s.whatsapp.net":
        return None
    canonical = verified_recipient.removesuffix("@s.whatsapp.net")
    need(verified_recipient == canonical+"@s.whatsapp.net","unverified_destination")
    long,short=sorted((submitted,canonical),key=len,reverse=True)
    need(re.fullmatch(r"55[1-9][0-9]9[6-9][0-9]{7}",long) and short==long[:4]+long[5:], "unverified_destination")
    return {"submitted":submitted,"canonical_jid":verified_recipient,"basis":"native_lookup"}


def accepted_response(native, verified_recipient, submitted=None):
    """Use only after checking the HTTP response and independently binding the destination."""
    need(isinstance(verified_recipient, str) and re.fullmatch(r"[1-9][0-9]{7,14}@s\.whatsapp\.net", verified_recipient),
         "unverified_destination")
    key = native.get("key") if isinstance(native, dict) else None
    need(isinstance(key,dict) and key.get("fromMe") is True and key.get("remoteJid")==verified_recipient and
         isinstance(key.get("id"),str) and re.fullmatch(r"[A-Za-z0-9_-]{6,160}",key["id"]), "unverified_provider_result")
    result={"status":"provider_accepted","key":{"id":key["id"],"fromMe":True,"remoteJid":verified_recipient}}
    binding=recipient_binding(submitted,verified_recipient) if submitted else None
    if binding:
        result["recipient_binding"]=binding
    return result
