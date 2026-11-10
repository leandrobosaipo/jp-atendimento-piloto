"""Join an authenticated JP channel address to the current AppBarber directory."""
from collections import Counter, defaultdict
import hashlib
import re

from bridge import need


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def first(row, *keys):
    return next((str(row[k]).strip() for k in keys if row.get(k)), "")


def phone(row):
    raw = first(row, "Cli_Celular","Celular","Telefone","Cli_Telefone","Telefone1","Cli_Fone")
    digits = re.sub(r"\D", "", raw).removeprefix("00")
    if len(digits) in (10,11):
        digits = (re.sub(r"\D", "", first(row,"DDI","PAF_DDI","Cli_DDI")) or "55") + digits
    return digits if re.fullmatch(r"55\d{10,11}",digits) else None


def resolve(directory, verified_addresses, lookup):
    """lookup is a batch existence query only. Inputs are private; results never authorize a send."""
    rows = directory["data"]
    need(isinstance(rows,list) and len(rows)>0 and directory.get("erro") in (None,"",0,"0") and
         all(str(directory.get(k)) == str(len(rows)) for k in ("recordsFiltered","recordsTotal")), "incomplete_directory")
    owners, phones = Counter(), defaultdict(list)
    for row in rows:
        need(isinstance(row,dict) and row.get("erro") in (None,"",0,"0"), "incomplete_directory")
        code = first(row,"Codigo","CliCodigo","CodigoCliente","Cli_Codigo","Cli_Cod")
        need(re.fullmatch(r"[0-9]+",code),"invalid_customer_code")
        owners[code] += 1
        number = phone(row)
        if not number:
            continue
        candidates = [number]
        if len(number)==13 and number[4]=="9" and number[5] in "6789":
            candidates.append(number[:4]+number[5:])
        for alias in candidates:
            phones[fingerprint(alias+"@s.whatsapp.net")].append((number,code,row))
    need(0<len(verified_addresses)<=64 and all(isinstance(k,str) and re.fullmatch(r"[0-9a-f]{64}",k)
         for k in verified_addresses), "invalid_native_address")
    decisions, candidates = {}, {}
    for address in sorted(set(verified_addresses)):
        matches = phones[address]
        if not matches:
            decisions[address] = {"status":"no_customer_match"}
        elif len(matches)!=1 or owners[matches[0][1]]!=1:
            decisions[address] = {"status":"ambiguous_customer_match"}
        else:
            candidates[address] = matches[0]
    if candidates:
        numbers = sorted({number for number,_,_ in candidates.values()})
        replies = lookup(numbers)
        need(isinstance(replies,list) and all(isinstance(r,dict) and str(r.get("number")) in numbers for r in replies),
             "invalid_provider_lookup")
        for address,(number,code,row) in candidates.items():
            evidence = [r for r in replies if str(r.get("number"))==number]
            jid = evidence[0].get("jid") if len(evidence)==1 and evidence[0].get("exists") is True else None
            if not (isinstance(jid,str) and re.fullmatch(r"55[0-9]{10,11}@s\.whatsapp\.net",jid) and fingerprint(jid)==address):
                decisions[address] = {"status":"provider_identity_unconfirmed"}
                continue
            notes = first(row,"Obs")
            decisions[address] = {"status":"identified", "client_code":code,
                "inactive":first(row,"PRe_Ativo").lower() in ("0","false","inativo") or row.get("PRe_Ativo") in (0,False),
                "opt_out":bool(re.search(r"(?:não|nao).*?(?:contatar|enviar|mandar)|bloquead|opt.?out",notes,re.I)),
                "matched_by":"exact" if jid==number+"@s.whatsapp.net" else "provider_verified_variant"}
    return decisions
