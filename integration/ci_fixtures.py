"""Write only disposable CI data, never real customer content or production credentials."""
import base64,hashlib,json,os,pathlib
assert os.getenv("GITHUB_ACTIONS")=="true" and os.geteuid()==0
os.umask(0o077);private=pathlib.Path("private")
def write(p,o):
    with p.open("x") as f:json.dump(o,f)
ai=private/"openai-inbox";ai.mkdir();write(ai/"kb.json",{"version":"fixture-3","entries":[{"id":"fixture","title":"Synthetic fixture","source":"CI","keywords":["fixture","teste","valor","manutenção"],"text":"Fictitious support facts for CI only."}]});(ai/"openai.key").write_text("sk-ci"+os.environ["GITHUB_SHA"])
p=private/"integration-inbox";p.mkdir();assets=p/"assets";assets.mkdir();items=[]
jpg=base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBMS0dARkVQWnNiUFVtVkVGZIhlbXd7gYKBTmCNl4x9lnN+gXz/2wBDARUXFx4aHjshITt8U0ZTfHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAARCAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/ALMAH//Z")
for group in ("servicos","clube-jp"):
    for i in range(3):
        key=group+"-"+str(i+1);comment=key.encode();b=jpg[:2]+b"\xff\xfe"+(2+len(comment)).to_bytes(2,"big")+comment+jpg[2:];n=key+".jpg";(assets/n).write_bytes(b)
        items.append({"id":key,"filename":n,"group":group,"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest(),"approved":True})
plans=[{"name":"Plano fictício de demonstração com atendimentos mensais "+str(i),"visits_per_month":i,"price_cents":15000*i} for i in (2,3,4)]
write(p/"assets.json",{"version":"fixture","assets":items,"club_plans":plans,"service_price_by_key":{"maintenance":14000},"service_art_prices":[{"name":"Manutenção fictícia","price_cents":14000}],"price_source_policy":"pending_user_resolution"})
write(p/"appbarber-readonly.json",{"origin":"https://sistema.appbarber.com.br","cookieHeader":"CI-only","services":{"maintenance":{"name":"Manutenção fictícia","id":"1"}},"professionals":{"fixture":{"id":"2","name":"Fictício","human_only":False}}})
