"""Narrow, allowlisted response planning. The model never chooses a provider endpoint or command."""
import datetime as dt
import re
import unicodedata
from zoneinfo import ZoneInfo

def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD",s.lower()) if not unicodedata.combining(c))

def currency(cents):return "R$ "+f"{cents//100},{cents%100:02d}"
def stops_contact(question):
    return bool(re.search(r"\b(parar|pare|sair|descadastrar|stop)\b|nao.*mensag",norm(question)))
def classify(question,options,cfg):
    q=norm(question);intent=options["intent"];service=options["service"];prof=options["professional"];date=options["date"]
    # Prioritize sensitive requests and ambiguous identities over requested sales material.
    if stops_contact(question) or re.search(r"\b(reclam\w*|coceira|ferida|sangr\w*|alerg\w*|cpf|cadastro de clientes|lista de clientes)\b",q):return "sensitive","","",""
    if intent=="auto":
        if re.search(r"\b(clube|assinatura|planos?)\b",q):intent="club_art"
        elif re.search(r"\b(horar\w*|vagas?|disponib\w*|agend\w*|marcar|amanha)\b",q):intent="availability"
        elif re.search(r"\b(tabela|cartaz|arte|cardapio)\b",q):intent="services_art"
        elif re.search(r"\b(prec\w*|valor\w*|custa\w*|custo|quanto)\b",q):intent="price"
        else:intent="chat"
    if not service:
        aliases={"maintenance":"manutencao","haircut":"corte","styling":"penteado","taping":"fitagem","adjustment":"reajuste","hydration":"hidratacao","eyebrows":"sobrancelha","simple_beard":"barba simples","beard":"barba","application":"aplicacao","coloring":"coloracao"}
        found=[k for k,alias in aliases.items() if k in cfg.get("services",{}) and re.search(r"\b"+alias+r"\b",q)]
        if "simple_beard" in found and "beard" in found:found.remove("beard")
        if "haircut_beard" in cfg.get("services",{}) and re.search(r"\bcorte\s*(?:e|\+)\s*barba\b",q):found=["haircut_beard"]
        if len(found)==1:service=found[0]
    if not prof:
        pros=cfg.get("professionals",{})
        first=[norm(v["name"]).split()[0] for v in pros.values()]
        matches=[]
        for k,v in pros.items():
            n=norm(v["name"]);short=n.split()[0]
            names={k,n}|({short} if first.count(short)==1 else set())
            if any(re.search(r"\b"+re.escape(a)+r"\b",q) for a in names):matches.append(k)
        if len(matches)==1:prof=matches[0]
    if not date:
        today=dt.datetime.now(ZoneInfo("America/Cuiaba")).date()
        given=re.search(r"\b\d{4}-\d{2}-\d{2}\b",q)
        date=given.group() if given else str(today+dt.timedelta(days=1)) if "amanha" in q else str(today) if "hoje" in q else ""
    return intent,service,prof,date

def group_for(intent):
    return "clube-jp" if intent=="club_art" else "servicos" if intent in ("price","services_art") else None

def precedence(catalog):
    value=catalog.get("price_source_policy")
    if value in ("appbarber","api"):return "api"
    if value in ("artwork","arte"):return "artwork"
    if value=="human_review":return "human_review"
    return "pending"

def prepare(question,options,cfg,catalog,query):
    intent,service,prof,date=classify(question,options,cfg)
    context={"category":intent};group=group_for(intent)
    if intent=="sensitive":return context,"Revisão humana necessária. Esta solicitação permanece na fila privada do teste.","handoff",None
    if intent=="chat":return context,None,None,None
    if intent=="club_art":
        plans=catalog["club_plans"];context={"category":"club","plans":[{"name":x["name"],"visits_per_month":x["visits_per_month"],"price_cents":x["price_cents"]} for x in plans],"source":"approved_artwork"}
        text="\n".join(x["name"]+" — "+currency(x["price_cents"])+" ("+str(x["visits_per_month"])+" visitas/mês)." for x in plans)
        return context,text,"answer",group
    if intent=="services_art":
        context["source"]="approved_artwork";context["price_source_policy"]=catalog["price_source_policy"]
        return context,"Aqui está a tabela de serviços.","answer",group
    if intent=="price" and precedence(catalog)=="artwork":
        return artwork_quote(question,service,catalog)
    if not service or service not in cfg.get("services",{}):return context,"Para consultar o cadastro correto, escolha um serviço do piloto.","handoff",None
    op="prices" if intent=="price" else "availability"
    if op=="availability" and (not date or not prof):return context,"Para consultar a disponibilidade, escolha um profissional e um dia. Nenhum horário foi reservado.","handoff",None
    facts=query(op,({"service_key":service} if op=="prices" else {"service_key":service,"date":date,"professional_key":prof}),cfg)
    allowed={"status","source","observed_at","reason","name","service_key","service_id","currency","price_cents","date","professional_key","duration_minutes","slots","timezone","reservation_performed"}
    context.update({k:v for k,v in facts.items() if k in allowed})
    if facts["status"]!="ok":
        return context,"A consulta oficial precisa de confirmação pela equipe. Nenhum valor ou horário foi confirmado.","handoff",None
    if op=="availability":
        slots=facts["slots"];selected=cfg["professionals"][prof]["name"]
        text=(f"Em {date}, os horários disponíveis com {selected} são "+", ".join(slots)+" (horário de Cuiabá)." if slots else f"Não encontrei horários disponíveis com {selected} em {date}.")
        return context,text+" Nenhum horário foi reservado.","answer",None
    return service_price(context,service,catalog,group)

def artwork_quote(question,service,catalog):
    """Match the approved printed description independently of scheduling IDs."""
    words=set(re.findall(r"[a-z]+",norm(question)))
    ignored={"a","de","da","na","do","em","somente","partir"}
    rows=catalog["service_art_prices"]
    matches=[r for r in rows if set(norm(r["name"]).replace("("," ").replace(")"," ").split())-ignored <= words]
    if len(matches)>1:
        return {"category":"clarify"},"Qual desses serviços você quer consultar primeiro?","answer",None
    row=matches[0] if matches else None
    # Only unambiguous shorthand may select a printed description. Neither
    # amount equality nor a generic AppBarber ID establishes equivalence.
    aliases={"maintenance":"manutencao","adjustment":"reajuste","taping":"fitagem","haircut":"corte","styling":"penteado","simple_beard":"barba simples"}
    if row is None and service in aliases and service in catalog.get("service_price_by_key",{}):
        found=[r for r in rows if norm(r["name"]).startswith(aliases[service])]
        if len(found)==1 and found[0]["price_cents"]==catalog["service_price_by_key"][service]:row=found[0]
    if row is None:
        if "coloracao" in words or service=="coloring":prompt="Você quer saber o valor da coloração da barba ou da prótese?"
        elif "barba" in words or service=="beard":prompt="Você prefere saber o valor da barba simples ou da completa?"
        elif "hidratacao" in words or service=="hydration":prompt="Você quer saber o valor da hidratação intensa?"
        elif "sobrancelha" in words or service=="eyebrows":prompt="Você quer saber o valor da sobrancelha na navalha?"
        else:return {"category":"price","source":"approved_artwork"},"O valor desse serviço precisa de confirmação pela equipe.","handoff",None
        return {"category":"clarify"},prompt,"answer",None
    name=row["name"];amount=row["price_cents"];starting="a partir de" in norm(name)
    label=re.sub(r"\s*\(a partir de\)\s*$","",name,flags=re.I)
    fact={"category":"price","status":"ok","source":"approved_artwork","name":label,"price_cents":amount,"currency":"BRL","starting_at":starting}
    return fact,f"{label}: {'a partir de ' if starting else ''}*{currency(amount)}*.","answer","servicos"

def service_price(context,service,catalog,group):
    cents=context["price_cents"];base=context["name"]
    # Artwork names and canonical service mapping are supplied privately with the approved catalog.
    recorded=catalog.get("service_price_by_key",{}).get(service)
    if recorded is None:
        return context,f"{base}: {currency(cents)} no cadastro do AppBarber. A arte não tem correspondência inequívoca com esse item.","answer",None
    if recorded is not None and int(recorded)!=cents:
        context["artwork_price_cents"]=int(recorded);context["divergence"]=True
        rule=precedence(catalog);context["applied_price_source"]=rule
        if rule in ("pending","human_review"):
            return context,"Há diferença entre o cadastro e a tabela. O valor precisa de confirmação pela equipe.","handoff",None
        if rule=="artwork":
            return context,f"Na tabela aprovada para este teste, {base}: {currency(int(recorded))}. O cadastro consultado apresenta um valor diferente; neste piloto vale a tabela aprovada.","answer",group
        # The actual sealed image contains a different numeric value; do not attach a misleading price table.
        return context,f"{base}: {currency(cents)} no cadastro do AppBarber consultado agora. O cadastro é a fonte definida para este teste.","answer",None
    return context,f"{base}: {currency(cents)} no cadastro do AppBarber consultado agora. A arte é a tabela aprovada do piloto.","answer",group
